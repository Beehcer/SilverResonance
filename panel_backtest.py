# -*- coding: utf-8 -*-
"""
panel_backtest.py — 面板建议功能历史回测（方案A: IC分析 + 方案B: Top3组合回测）。

核心思路：
  在每个历史时点 t（1h K线收盘），对全品种计算面板评分（模拟 renderAdvice 逻辑），
  然后验证：
    A) 评分与未来 N 根 K线收益的相关性（IC 分析）
    B) 每天按评分选 Top3 品种，只交易这些品种的信号，对比全部交易的基准

数据限制：缓存只有 1h K线，track_a 共振需要 15min/3min/30s 三周期。
处理方案：从 1h K线插值模拟（1h→15min×4→3min×20→30s×120），
这种插值无法恢复真实价格路径，只能用于机制演示，不能验证胜率或面板有效性。
修复前的历史报告包含同日选品前视和 C6 记账错误，不能作为可用业绩证据。

性能优化：
  - 降采样结果预计算缓存（每品种只算一次）
  - 方案A采用采样计算（默认每5根K线取一个时点，可配置）

用法：
  python panel_backtest.py                  # 全量 12 品种
  python panel_backtest.py --symbols rb,jm  # 小样本验证
  python panel_backtest.py --scheme A       # 只跑 IC 分析
  python panel_backtest.py --scheme B       # 只跑 Top3 组合回测
"""
import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import double_bottom
import entry_logic
import resonance
import symbols as symbols_mod
from backtest import (ROUND_TRIP_COST_TICKS, EXECUTION_VERSION, EXECUTION_LIMITATIONS,
                      align_price, aligned_levels, barrier_exit,
                      trading_day_labels, day_close_indices)

# ---- 面板评分权重（与 renderAdvice JS 完全一致）----
W_RESONANCE = 50
W_CERTAINTY = 0.5
W_ER = 20
W_LOCATION = 0.3
W_QUADRANT = 30

# ---- 方案B参数 ----
TOP_N = 3                    # 每天选 Top N
MAX_HOLD_BARS = 60           # 最大持仓根数（与 backtest.py 一致）
COST_TICKS = ROUND_TRIP_COST_TICKS  # 明确假设，不是实际交易所/期货公司费用

# ---- 方案U（用户实盘系统）参数 ----
# 用户口径：共振亮+双底结构 → 量仓动能过滤 → 每手固定止盈100元/止损50元（各品种按
# tick_value 换算成价格距离：dist_price = 金额 ÷ tick_value × tick），每信号一手。
USER_TP_YUAN = 100           # 每手止盈金额（元）
USER_SL_YUAN = 50            # tick对齐前毛止损金额；R按对齐后实际初始风险归一
USER_VOL_MA = 20             # 放量均量窗口（前 N 根，不含确认根）

# ---- 方案A采样间隔（每 N 根 K线计算一次，默认5）----
SAMPLE_INTERVAL = 5

# ---- rows 磁盘缓存（执行层参数改动免重算，秒级迭代）----
# rows 只含入场决策要素（quadrant/direction/stop/t1/regime/score），
# 与执行层参数（TOP_N/MAX_HOLD_BARS/COST_TICKS/variant）无关 → 可落盘复用。
# 失效条件：
#  1) 决策/评分源文件（entry_logic/double_bottom/resonance/config）mtime 更新；
#  2) 本文件评分权重常量 W_* 变化（指纹入文件名，改权重自动换缓存）；
#  3) 手动改 ROWS_CACHE_VERSION（改了 precompute_rows/replay 入场门等 rows 逻辑时 +1）。
ROWS_CACHE_DIR = "cache_rows"
ROWS_CACHE_VERSION = 3
ROWS_SOURCE_FILES = ("entry_logic.py", "double_bottom.py",
                     "resonance.py", "config.py")
_ROWS_W_SIG = f"{W_RESONANCE}-{W_CERTAINTY}-{W_ER}-{W_LOCATION}-{W_QUADRANT}"


def rows_cache_path(out_dir, symbol, days=250, warmup=120):
    return os.path.join(out_dir, ROWS_CACHE_DIR,
                        f"{symbol.replace('.', '_')}_{days}d_w{warmup}_sig{_ROWS_W_SIG}_v{ROWS_CACHE_VERSION}.json")


def rows_cache_valid(path):
    if not os.path.exists(path):
        return False
    ctime = os.path.getmtime(path)
    src_dir = os.path.dirname(os.path.abspath(__file__))
    for fn in ROWS_SOURCE_FILES:
        p = os.path.join(src_dir, fn)
        if os.path.exists(p) and os.path.getmtime(p) > ctime:
            return False
    return True


def load_rows_cache(out_dir, symbol, days=250, warmup=120):
    path = rows_cache_path(out_dir, symbol, days, warmup)
    if not rows_cache_valid(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {int(k): v for k, v in d.items()}, path
    except (OSError, ValueError):
        return None, path


def save_rows_cache(out_dir, symbol, rows, days=250, warmup=120):
    path = rows_cache_path(out_dir, symbol, days, warmup)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return path


def product_of(preset_symbol):
    """SHFE.ag2612 → ag"""
    return "".join(ch for ch in preset_symbol.split(".")[1] if not ch.isdigit())


def load_cache(out_dir, symbol, days=250):
    path = os.path.join(out_dir, "cache", f"{symbol.replace('.', '_')}_{days}d.json")
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d["bars"][:-1], d["source"]  # final downloaded candle may still be forming


def downsample_bars(bars_1h, target_sec):
    """
    从 1h K线降采样到目标周期。
    3600s → 900s(15min): ×4；3600s → 180s(3min): ×20；3600s → 30s: ×120
    方法：对每根 1h K线，线性插值生成子 K线（OHLCV 按时间均匀分布）。
    这不是真实的 tick 数据，但保留了 1h 内的价格路径方向。
    """
    ratio = 3600 // target_sec
    if ratio <= 1:
        return bars_1h
    out = []
    for bar in bars_1h:
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        v = bar.get("volume", 0)
        # 将 1h 分成 ratio 段，线性插值 close，high/low 取整根的范围
        step = (c - o) / ratio
        for i in range(ratio):
            sub_o = o + step * i
            sub_c = o + step * (i + 1)
            out.append({
                "dt": bar["dt"],  # 同组子K线共享时间戳（简化）
                "open": round(sub_o, 4),
                "high": round(max(sub_o, sub_c, h if i == ratio - 1 else max(sub_o, sub_c)), 4),
                "low": round(min(sub_o, sub_c, l if i == 0 else min(sub_o, sub_c)), 4),
                "close": round(sub_c, 4),
                "volume": v // ratio,
                "oi": bar.get("oi", 0),
            })
    return out


def precompute_downsampled(bars_1h):
    """预计算三个周期的降采样结果，返回 dict。"""
    return {
        "15m": downsample_bars(bars_1h, 900),
        "3m": downsample_bars(bars_1h, 180),
        "30s": downsample_bars(bars_1h, 30),
    }


def compute_panel_score_fast(bars_1h, bars_15m, bars_3m, bars_30s, tick=1.0):
    """
    快速计算面板评分（使用预计算的降采样数据）。
    bars_15m/3m/30s 必须与 bars_1h 对应同一时间点。
    """
    if len(bars_1h) < 10:
        return None

    # --- track_b: 双底结构（1h）---
    ev = double_bottom.evaluate(bars_1h)
    atr = double_bottom._atr(bars_1h)
    price = bars_1h[-1]["close"]

    # --- regime ---
    rg = entry_logic.detect_regime(bars_1h)

    # --- decide ---
    d = entry_logic.decide(ev, price, atr=atr, regime=rg)

    # --- track_a: 三频共振（使用预计算数据）---
    res = resonance.evaluate(bars_15m, bars_3m, bars_30s, tick=tick)

    # --- 面板评分公式（与 renderAdvice JS 一致）---
    score = 0.0
    if res.get("resonance"):
        score += W_RESONANCE
    score += (d.get("certainty_score") or 0) * W_CERTAINTY
    score += (rg.get("er") or 0) * W_ER
    if d.get("location_score") is not None:
        score += d["location_score"] * W_LOCATION
    if d.get("quadrant") in ("probe", "main"):
        score += W_QUADRANT

    # --- 分组 ---
    quadrant = d.get("quadrant", "standby")
    is_active = res.get("resonance", False) or quadrant != "standby"
    is_watch = (not is_active) and ((rg.get("er", 0) > 0.3) or (d.get("certainty_score", 0) > 30))

    return {
        "score": round(score, 2),
        "resonance": res.get("resonance", False),
        "direction": res.get("direction", "none"),
        "quadrant": quadrant,
        "certainty": d.get("certainty_score", 0),
        "location": d.get("location_score"),
        "er": rg.get("er", 0),
        "regime": rg.get("regime", "range"),
        "group": "active" if is_active else ("watch" if is_watch else "none"),
        "price": price,
        "atr": atr,
        "ev": ev,
        "decide": d,
        "regime_detail": rg,
        "resonance_detail": res,
    }


def forward_return(bars, t, n_bars):
    """计算 t 时点之后 n_bars 根 K线的收益率（close-to-close）。"""
    if t + n_bars >= len(bars):
        return None
    c0 = bars[t]["close"]
    c1 = bars[t + n_bars]["close"]
    if c0 == 0:
        return None
    return (c1 - c0) / c0


def precompute_rows(bars, ds, warmup, tick=1.0):
    """
    单品种一次性预计算：对每根 bar t（t ≥ warmup）算面板评分/决策状态并**瘦身存表**。
    返回 {t: row}，row 仅含 replay_symbol 与 Top3 选择需要的字段：
      score/group          → Top3 每日排序
      quadrant/direction   → replay 入场判断
      stop_price           → replay 止损价（decide 算出的）
      ev_plan_t1 / neck / L1  → replay 目标价（轨道B plan 或 2×颈位−L1 丈量）
      regime               → 交易 meta
      price/atr            → 备用
    之后 replay_symbol / [2/3] Top3 都查表，不再逐根重复 evaluate/decide（提速关键）。

    一致性（防 lookahead）：旧 [2/3] 对每时点用 bars[:t+1] 单独建降采样数组、再传全量，
    等价于在全量 ds 上取前 (t+1)*ratio 个元素。resonance 只读序列**尾部**固定窗口
    （最大 MA_SLOW+TREND_SLOPE_LOOKBACK = 30 个元素），因此传尾部切片
    arr[(t+1)*ratio-60:(t+1)*ratio]（长度不足 60 时传 [0:(t+1)*ratio]）即可精确复现，
    且不引入未来数据。注：30s 真实 ratio = 120（3600//30），不是注释里写的 40。
    """
    rows = {}
    n = len(bars)
    ds15, ds3, ds30 = ds["15m"], ds["3m"], ds["30s"]

    def tail(arr, end):
        return arr[0:end] if end <= 60 else arr[end - 60:end]

    for t in range(warmup, n):
        kb = bars[:t + 1]
        # 旧 [2/3] 语义：截至 t 的完整降采样前缀（30s ×120），只取尾部窗口防未来数据
        a15 = tail(ds15, (t + 1) * 4)
        a3 = tail(ds3, (t + 1) * 20)
        a30 = tail(ds30, (t + 1) * 120)
        ps = compute_panel_score_fast(kb, a15, a3, a30, tick=tick)
        if not ps:
            continue
        d = ps.get("decide") or {}
        ev = ps.get("ev") or {}
        plan = ev.get("plan") or {}
        pat = ev.get("pattern") or {}
        neck = pat.get("neck")
        l1 = pat.get("L1") or {}
        stop = d.get("stop") or {}
        rows[t] = {
            "score": ps.get("score", 0),
            "group": ps.get("group", "none"),
            "quadrant": d.get("quadrant", "standby"),
            "direction": d.get("direction", "none"),
            "resonance": ps.get("resonance", False),      # 轨道A共振灯（方案U入场要件）
            "res_dir": ps.get("direction", "none"),      # 共振方向（多/空/无）
            "stop_price": stop.get("price"),
            "ev_plan_t1": plan.get("target1"),
            "neck": neck,
            "l1": l1.get("price"),
            "regime": (ps.get("regime_detail") or {}).get("regime", "range"),
            "price": ps.get("price"),
        }
    return rows


def last_idx_by_date(bars, warmup):
    """返回 {date: idx}，date 为该日最后一根 1h bar 的索引（t ≥ warmup 才记录）。"""
    m = {}
    days = trading_day_labels(bars)
    for i in range(warmup, len(bars)):
        m[days[i]] = i
    return m


# ================= 方案 A: IC 分析 =================

def scheme_a(all_bars, presets, warmup=120, sample_interval=SAMPLE_INTERVAL):
    """
    在每个采样时点，对每个品种计算面板评分，
    然后计算评分与未来 1/3/5/10 根 K线收益的 Spearman 相关系数（IC）。
    使用采样减少计算量（默认每5根K线取一个时点）。
    """
    print("\n" + "=" * 60)
    print("方案 A: 面板评分 IC 分析（评分 vs 未来收益相关性）")
    print(f"采样间隔: 每 {sample_interval} 根 K线")
    print("=" * 60)

    horizons = [1, 3, 5, 10]
    # 收集所有 (score, forward_return) 对
    pairs = {h: [] for h in horizons}

    for preset in presets:
        sym = preset["symbol"]
        name = preset["name"]
        bars, _ = all_bars.get(sym, (None, None))
        if not bars:
            continue
        n = len(bars)
        print(f"  处理 {name} ({sym}) — {n} 根 K线 ...", end=" ", flush=True)

        # 预计算降采样（一次性）
        ds = precompute_downsampled(bars)

        count = 0
        for t in range(warmup, n - max(horizons) - 1, sample_interval):
            # 取到 t 为止的数据（模拟当时能看到的信息）
            sub_1h = bars[:t + 1]
            sub_15m = ds["15m"][:(t + 1) * 4]  # 1h→15min ×4
            sub_3m = ds["3m"][:(t + 1) * 20]   # 1h→3min ×20
            sub_30s = ds["30s"][:(t + 1) * 120]

            ps = compute_panel_score_fast(sub_1h, sub_15m, sub_3m, sub_30s, tick=preset["tick"])
            if ps is None:
                continue
            for h in horizons:
                fr = forward_return(bars, t, h)
                if fr is not None:
                    pairs[h].append((ps["score"], fr, ps["group"]))
            count += 1
        print(f"{count} 个有效时点")

    # 计算 Spearman IC
    print("\n--- IC 结果（Spearman 相关系数）---")
    print(f"{' horizon':>8} {'IC':>8} {'|IC|>0.03?':>12} {'样本数':>8}")
    for h in horizons:
        data = pairs[h]
        if len(data) < 30:
            print(f"{h:>8} {'N/A':>8} {'样本不足':>12} {len(data):>8}")
            continue
        scores = [x[0] for x in data]
        rets = [x[1] for x in data]
        ic = spearman_corr(scores, rets)
        sig = "✓ 有效" if abs(ic) > 0.03 else "✗ 无效"
        print(f"{h:>8} {ic:>8.4f} {sig:>12} {len(data):>8}")

    # 分组统计
    print("\n--- 分组统计（各组未来收益均值）---")
    for h in [3, 5]:
        data = pairs[h]
        if not data:
            continue
        groups = defaultdict(list)
        for s, r, g in data:
            groups[g].append(r)
        print(f"\n  horizon={h} 根:")
        for g in ("active", "watch", "none"):
            rets = groups.get(g, [])
            if rets:
                avg = sum(rets) / len(rets)
                win = sum(1 for r in rets if r > 0) / len(rets)
                print(f"    {g:>8}: n={len(rets):>5}, 平均收益={avg:+.4f} ({avg * 100:+.2f}%), 胜率={win:.1%}")

    return pairs


def spearman_corr(x, y):
    """Spearman 秩相关系数（纯 Python 实现）。"""
    n = len(x)
    if n < 3:
        return 0.0
    rx = rank(x)
    ry = rank(y)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))


def rank(vals):
    """计算秩次（处理并列取平均秩）。"""
    indexed = sorted(enumerate(vals), key=lambda x: x[1])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


# ================= 方案 B: Top3 组合回测 =================

def scheme_b(all_bars, presets, warmup=120, out_dir=None):
    """
    模拟"每天看面板选 Top3 品种，只交易这些品种"的策略：
    1. 每天收盘后，对全品种计算面板评分，选 Top3
    2. 次日只交易这 3 个品种的信号（probe/main/confirm）
    3. 对比"交易全部品种"的基准
    """
    presets_map = {p["symbol"]: p for p in presets}
    print("\n机制演示：插值小周期不得用于胜率/盈利能力验证；旧报告不可作为本版证据。")
    print("\n" + "=" * 60)
    print("方案 B: Top3 组合回测（面板过滤 vs 全品种基准）")
    print("=" * 60)

    # ---- 第一步：对每个品种，一次性预计算评分行 + 逐根重放生成信号 ----
    # rows 磁盘缓存：冷跑时全量预计算（慢），之后执行层参数改动直接命中（秒级）
    print("\n[1/3] 预计算评分并重放生成信号 ...")
    all_signals = {}  # symbol -> list of signal dicts
    rows_all = {}     # symbol -> {t: row}
    last_all = {}     # symbol -> {date: idx}
    for preset in presets:
        sym = preset["symbol"]
        name = preset["name"]
        bars, _ = all_bars.get(sym, (None, None))
        if not bars:
            continue
        rows, cache_path = load_rows_cache(out_dir, sym, warmup=warmup) if out_dir else (None, None)
        if rows is None:
            t0 = time.time()
            ds = precompute_downsampled(bars)          # 全量降采样只算一次
            rows = precompute_rows(bars, ds, warmup, tick=preset["tick"])
            tag = f"预计算 {time.time()-t0:.0f}s"
            if out_dir:
                save_rows_cache(out_dir, sym, rows, warmup=warmup)
        else:
            tag = "rows缓存命中"
        rows_all[sym] = rows
        last_all[sym] = last_idx_by_date(bars, warmup)
        signals = replay_symbol(preset, bars, warmup, rows=rows, lots=2)
        all_signals[sym] = signals
        print(f"  {name} ({sym}): {len(signals)} 个信号  [{tag}]")

    # ---- 第二步：每日选 Top3（查表，O(1)/品种/日）----
    print("\n[2/3] 每日面板评分与 Top3 选择 ...")
    # 收集所有交易日
    all_dates = set()
    for sym, signals in all_signals.items():
        for sig in signals:
            all_dates.add(sig["entry_date"])
    all_dates = sorted(all_dates)
    print(f"  共 {len(all_dates)} 个交易日有信号")

    # 当天交易只能使用前一个已结束交易日的评分。
    daily_top3 = {}  # date -> [(symbol, score), ...]
    for dt in all_dates:
        day_scores = []
        for preset in presets:
            sym = preset["symbol"]
            rows = rows_all.get(sym)
            if not rows:
                continue
            prev = [d for d in sorted(last_all[sym]) if d < dt]
            if not prev:
                continue
            idx = last_all[sym][prev[-1]]
            row = rows.get(idx)
            if row and row["group"] == "active":
                day_scores.append((sym, row["score"]))
        day_scores.sort(key=lambda x: -x[1])
        daily_top3[dt] = day_scores[:TOP_N]

    # ---- 第三步：对比回测 ----
    print("\n[3/3] 对比回测 ...")

    # 基准：交易全部品种的信号
    baseline_trades = []
    for sym, signals in all_signals.items():
        baseline_trades.extend(signals)
    baseline_stats = compute_trade_stats(baseline_trades)

    # 面板过滤：只交易当天 Top3 品种的信号
    filtered_trades = []
    for sym, signals in all_signals.items():
        for sig in signals:
            dt = sig["entry_date"]
            top3 = daily_top3.get(dt, [])
            top3_syms = {s for s, _ in top3}
            if sym in top3_syms:
                filtered_trades.append(sig)
    filtered_stats = compute_trade_stats(filtered_trades)

    # C6 变体：半仓止盈（对 Top3 信号重新跑一遍，rows 查表免重算）
    c6_trades = []
    for sym, signals in all_signals.items():
        preset = presets_map[sym]
        bars, _ = all_bars.get(sym, (None, None))
        if not bars:
            continue
        c6_signals = replay_symbol(preset, bars, warmup, variant="c6_half",
                                   rows=rows_all.get(sym), lots=2)
        for sig in c6_signals:
            dt = sig["entry_date"]
            top3 = daily_top3.get(dt, [])
            top3_syms = {s for s, _ in top3}
            if sym in top3_syms:
                c6_trades.append(sig)
    c6_stats = compute_trade_stats(c6_trades)

    # 输出对比
    print("\n--- 对比结果 ---")
    print(f"{'':>12} {'交易数':>6} {'胜率':>8} {'平均R':>8} {'期望R':>8} {'盈亏比':>8} {'累计R':>8}")
    for label, s in (("基准(全部)", baseline_stats), ("面板Top3", filtered_stats), ("C6半仓止盈", c6_stats)):
        if s:
            print(f"{label:>12} {s['n']:>6} {s['winrate']:>8.1%} {s['avgR']:>+8.3f} "
                  f"{s['expR']:>+8.3f} {s['pf']:>8.2f} {s['cumR']:>+8.2f}")
        else:
            print(f"{label:>12} {'0':>6}")

    # 日内统计（按 entry_date 聚合）
    print("\n--- 日内（按交易日）统计 ---")
    for label, trades in (("基准(全部)", baseline_trades),
                          ("面板Top3", filtered_trades),
                          ("C6半仓止盈", c6_trades)):
        d = daily_stats(trades, label)
        if d:
            print(d)
            print()

    # 每日 Top3 明细（前 20 天）
    print("\n--- 每日 Top3 明细（前 20 天）---")
    for dt in all_dates[:20]:
        top3 = daily_top3.get(dt, [])
        if top3:
            items = [f"{presets_map[s]['name']}({sc:.0f})" for s, sc in top3]
            print(f"  {dt}: {' | '.join(items)}")
        else:
            print(f"  {dt}: (无 active 品种)")

    return {
        "validation_status": "synthetic_demo_only",
        "execution_version": EXECUTION_VERSION,
        "baseline": baseline_stats,
        "filtered": filtered_stats,
        "c6": c6_stats,
        "daily_top3": daily_top3,
        "all_signals": all_signals,
    }


def replay_symbol(preset, bars, warmup, variant=None, rows=None, lots=1, intraday=True):
    """Replay integer lots; one record per original position, including partial exits.

    R is net P&L divided by original total stop risk. Each completed lot pays the
    stated round-trip cost exactly once. Synthetic rows remain demonstration data.
    """
    if isinstance(lots, bool) or not isinstance(lots, int) or lots < 1:
        raise ValueError("lots must be a positive integer")
    tick = preset["tick"]
    days = trading_day_labels(bars)
    day_ends = day_close_indices(bars, days)
    trades, pos = [], None
    ds = precompute_downsampled(bars) if rows is None else None

    def close_lots(price, quantity, reason, t):
        price = align_price(price, tick, pos["sgn"] < 0)
        net = ((price - pos["entry"]) * pos["sgn"] - COST_TICKS * tick) * quantity
        pos["net"] += net
        pos["remaining"] -= quantity
        pos["exits"].append({"time": bars[t]["dt"], "price": price,
                             "lots": quantity, "reason": reason})
        if pos["remaining"]:
            return
        r = pos["net"] / (pos["risk"] * lots)
        trades.append({**pos["meta"], "exit_time": bars[t]["dt"],
                       "exit_price": price, "exit_reason": reason,
                       "bars_held": t - pos["entry_i"] + 1,
                       "pnl_ticks": round(pos["net"] / tick, 6),
                       "R": round(r, 6), "weighted_R": round(r * pos["weight"], 6),
                       "panel_score": pos["panel_score"], "exits": list(pos["exits"]),
                       "cost_ticks_per_lot": COST_TICKS})

    for t in range(warmup, len(bars)):
        bar = bars[t]
        if pos:
            if t < pos["entry_i"]:
                continue
            price, reason = barrier_exit(bar, pos["stop"], pos["target"], pos["sgn"], tick)
            if reason is None and intraday and t in day_ends:
                price, reason = bar["close"], "day_close"
            elif reason is None and t - pos["entry_i"] >= MAX_HOLD_BARS:
                price, reason = bar["close"], "timeout"
            if reason is not None:
                close_lots(price, pos["remaining"], reason, t)
                pos = None
                continue
            # A close-based partial exit cannot precede an intrabar stop/target.
            unreal = (bar["close"] - pos["entry"]) * pos["sgn"]
            if (variant == "c6_half" and not pos["exits"] and lots >= 2
                    and unreal >= pos["risk"]):
                close_lots(bar["close"], lots // 2, "c6_half_profit", t)
                pos["stop"] = pos["entry"]
            continue

        if rows is not None:
            row = rows.get(t)
            if not row:
                continue
            quadrant, direction = row["quadrant"], row["direction"]
            stop, regime, score = row["stop_price"], row["regime"], row["score"]
            target = row["ev_plan_t1"]
            if not target and row["neck"] is not None and row["l1"] is not None:
                target = 2 * row["neck"] - row["l1"]
        else:
            kb = bars[:t + 1]
            ev = double_bottom.evaluate(kb)
            rg = entry_logic.detect_regime(kb)
            d = entry_logic.decide(ev, bar["close"], atr=double_bottom._atr(kb), regime=rg)
            quadrant, direction, regime = d["quadrant"], d["direction"], rg["regime"]
            stop = (d.get("stop") or {}).get("price")
            ps = compute_panel_score_fast(kb, ds["15m"][:(t+1)*4],
                                         ds["3m"][:(t+1)*20], ds["30s"][:(t+1)*120], tick=tick)
            score = ps["score"] if ps else 0
            target = (ev.get("plan") or {}).get("target1")
            pattern = ev.get("pattern") or {}
            if target is None and pattern.get("neck") is not None and pattern.get("L1"):
                target = 2 * pattern["neck"] - pattern["L1"]["price"]
        if (quadrant not in ("main", "probe", "confirm") or direction not in ("long", "short")
                or stop is None or target is None or t + 1 >= len(bars)):
            continue
        if intraday and days[t + 1] != days[t]:
            continue
        sgn = 1 if direction == "long" else -1
        entry, stop, target = aligned_levels(bars[t + 1]["open"], stop, target, sgn, tick)
        risk = (entry - stop) * sgn
        if risk <= 0 or (target - entry) * sgn <= 0:
            continue
        weight = {"main": 1.0, "confirm": 0.25, "probe": 0.25}[quadrant]
        pos = {"entry_i": t + 1, "entry": entry, "stop": stop, "target": target,
               "sgn": sgn, "risk": risk, "weight": weight, "remaining": lots,
               "net": 0.0, "exits": [], "panel_score": score,
               "meta": {"symbol": preset["symbol"], "name": preset["name"],
                        "direction": direction, "quadrant": quadrant, "regime": regime,
                        "weight": weight, "lots": lots, "entry_time": bars[t + 1]["dt"],
                        "entry_date": days[t + 1], "entry_price": entry,
                        "stop": stop, "target": target}}
    if pos:
        close_lots(bars[-1]["close"], pos["remaining"], "end", len(bars) - 1)
    return trades


def daily_stats(trades, label):
    """
    日内统计：按 entry_date 聚合加权R → 回答"面板在日线级别到底赚不赚钱"。
    返回多行文本；无交易返回 None。
    """
    if not trades:
        return None
    day_r = defaultdict(float)
    day_n = defaultdict(int)
    for tr in trades:
        d = tr.get("entry_date")
        if not d:
            continue
        day_r[d] += tr.get("weighted_R", 0.0)
        day_n[d] += 1
    if not day_r:
        return None
    days = sorted(day_r)
    rv = [day_r[d] for d in days]
    pos_days = sum(1 for r in rv if r > 0)
    neg_days = sum(1 for r in rv if r < 0)
    flat_days = len(rv) - pos_days - neg_days

    gp = sum(r for r in rv if r > 0)          # 盈利日累计R
    gl = -sum(r for r in rv if r < 0)         # 亏损日累计R绝对值
    pf_day = gp / gl if gl > 0 else float("inf")

    best = max(days, key=lambda d: day_r[d])
    worst = min(days, key=lambda d: day_r[d])

    # 最长连盈/连亏天数
    best_streak = cur_w = 0
    worst_streak = cur_l = 0
    for d in days:
        r = day_r[d]
        if r > 0:
            cur_w += 1
            cur_l = 0
            best_streak = max(best_streak, cur_w)
        elif r < 0:
            cur_l += 1
            cur_w = 0
            worst_streak = max(worst_streak, cur_l)
        else:
            cur_w = cur_l = 0

    avg_win_day = gp / pos_days if pos_days else 0.0
    avg_loss_day = -gl / neg_days if neg_days else 0.0

    lines = [
        f"── {label}（按 entry_date 聚合 {len(days)} 个交易日 / {len(trades)} 笔）",
        f"  盈利日 {pos_days} / 亏损日 {neg_days} / 打平 {flat_days}   日胜率 {pos_days / len(days):.1%}",
        f"  盈利日均R {avg_win_day:+.3f} × {pos_days}日   亏损日均R {avg_loss_day:+.3f} × {neg_days}日",
        f"  日级盈亏比(Σ盈/Σ亏) {pf_day:.2f}   日均R {sum(rv) / len(rv):+.3f}   累计R {sum(rv):+.2f}",
        f"  最佳日 {best} {day_r[best]:+.2f}   最差日 {worst} {day_r[worst]:+.2f}",
        f"  最长连盈 {best_streak} 日 / 最长连亏 {worst_streak} 日",
    ]
    return "\n".join(lines)


def compute_trade_stats(trades):
    """计算交易统计。"""
    n = len(trades)
    if not n:
        return None
    wr = [x["weighted_R"] for x in trades]
    wins = [x for x in wr if x > 0]
    gw = sum(wins)
    gl = -sum(x for x in wr if x < 0)
    return {
        "n": n,
        "winrate": len(wins) / n,
        "avgR": sum(x["R"] for x in trades) / n,
        "expR": sum(wr) / n,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "cumR": sum(wr),
    }


# ================= 方案 U: 用户实盘系统回测 =================
# 口径（用户原话）：
#   共振 + 双底会亮信号开始重点关注；实际操作按量仓变化跟当前动能；
#   止盈100元 / 止损50元（每品种固定金额，一手）。
# 映射到回测：
#   - 入场 t 决策：rows[t] resonance==True 且 quadrant∈(main,probe,confirm)
#     且 direction∈(long,short) 且 stop 存在（结构成立）
#   - v0：信号亮 → t+1 open 入场（不含量仓过滤，纯信号对照）
#   - user：信号亮后，t+1 根做量仓动能确认（放量>前20根均量 + oi>0增仓
#      + 收线方向同向），t+2 open 入场（贴实盘：看量仓配合才进）
#   - 出场：固定金额止盈/止损 → 每手止盈距离 = 100/tick_value×tick（点数），
#     止损距离 = 50/tick_value×tick；同根先触止损保守；timeout MAX_HOLD_BARS
#   - R：按tick对齐后的实际初始毛止损金额归一；weight=1（一手）。
# 入场棒从open之后检查止损/目标；确认棒早于实际入场，不能判出场。

def replay_user_symbol(preset, bars, warmup, rows, need_conf=False,
                       vol_ma=USER_VOL_MA, intraday=True):
    """旧规则机制演示；价格向外对齐tick，R以实际初始风险归一。"""
    tick = preset["tick"]
    tv = preset.get("tick_value") or (tick * 10)          # 每跳金额(元/手)
    mult = tv / tick                                       # 合约乘数（点数→元）
    tp_dist = USER_TP_YUAN / tv * tick                     # 止盈价格距离
    sl_dist = USER_SL_YUAN / tv * tick                     # 止损价格距离
    trades, pos = [], None
    days = trading_day_labels(bars)
    day_ends = day_close_indices(bars, days)

    for t in range(warmup, len(bars)):
        # ---------- 持仓：判出场 ----------
        if pos:
            if t < pos["entry_i"]:
                continue
            bar = bars[t]
            sgn = pos["sgn"]
            sl, tp = pos["sl"], pos["tp"]
            exit_price, reason = barrier_exit(bar, sl, tp, sgn, tick)
            if reason is None and intraday and t in day_ends:
                exit_price, reason = align_price(bar["close"], tick, sgn < 0), "day_close"
            elif reason is None and t - pos["entry_i"] >= MAX_HOLD_BARS:
                exit_price, reason = bar["close"], "timeout"
            if exit_price is None:
                continue
            pnl_yuan = (exit_price - pos["entry"]) * sgn * mult - COST_TICKS * tv
            r = pnl_yuan / pos["risk_yuan"]
            trades.append({
                **pos["meta"],
                "exit_time": bar["dt"], "exit_price": round(exit_price, 4),
                "exit_reason": reason, "bars_held": t - pos["entry_i"] + 1,
                "pnl_yuan": round(pnl_yuan, 1), "R": round(r, 3),
                "weighted_R": round(r, 3),
            })
            pos = None
            continue

        # ---------- 空仓：共振+双底入场要件 ----------
        # 注：真实面板共振灯 = 15m/3m/30s 三腿全亮；回测只有 1h K 线，
        # 30s/3m 为线性插值合成，trigger(企稳)腿永不满足 → 用降级近似：
        # 共振方向(res_dir，来自15m趋势) 与 双底方向(direction) 同向即视为信号。
        row = rows.get(t)
        if not row:
            continue
        quadrant = row.get("quadrant", "standby")
        direction = row.get("direction", "none")
        res_dir = row.get("res_dir", "none")
        if direction not in ("long", "short") or res_dir != direction:
            continue
        if quadrant not in ("main", "probe", "confirm"):
            continue
        if row.get("stop_price") is None:
            continue

        # 量仓动能确认（贴实盘）：t+1 根收盘看放量+增仓+收线同向
        if need_conf:
            ct = t + 1
            if ct >= len(bars):
                continue
            cbar = bars[ct]
            lo = max(warmup, ct - vol_ma)
            vma = sum(bars[j]["volume"] for j in range(lo, ct)) / (ct - lo) if ct > lo else 0
            vol_ok = vma > 0 and cbar["volume"] > vma
            oi_ok = (cbar.get("oi") or 0) > 0              # 增仓
            bar_ok = (cbar["close"] > cbar["open"]) if direction == "long" \
                else (cbar["close"] < cbar["open"])
            if not (vol_ok and oi_ok and bar_ok):
                continue
            entry_i = ct + 1
        else:
            entry_i = t + 1

        if entry_i >= len(bars) or (intraday and days[entry_i] != days[t]):
            continue
        sgn = 1 if direction == "long" else -1
        raw_entry = bars[entry_i]["open"]
        entry, sl, tp = aligned_levels(raw_entry, raw_entry - sgn * sl_dist,
                                       raw_entry + sgn * tp_dist, sgn, tick)
        pos = {
            "entry_i": entry_i, "entry": entry, "sgn": sgn, "sl": sl, "tp": tp,
            "risk_yuan": abs(entry - sl) * mult,
            "meta": {
                "symbol": preset["symbol"], "name": preset["name"],
                "direction": direction, "quadrant": quadrant,
                "regime": row.get("regime", "range"), "weight": 1.0,
                "entry_time": bars[entry_i]["dt"],
                "entry_date": days[entry_i],
                "entry_price": entry, "tp_yuan": abs(tp - entry) * mult,
                "sl_yuan": abs(entry - sl) * mult,
                "requested_tp_yuan": USER_TP_YUAN, "requested_sl_yuan": USER_SL_YUAN,
            },
        }

    # 末尾强平
    if pos:
        bar = bars[-1]
        pnl_yuan = (bar["close"] - pos["entry"]) * pos["sgn"] * mult - COST_TICKS * tv
        r = pnl_yuan / pos["risk_yuan"]
        trades.append({
            **pos["meta"],
            "exit_time": bar["dt"], "exit_price": bar["close"],
            "exit_reason": "end", "bars_held": len(bars) - pos["entry_i"],
            "pnl_yuan": round(pnl_yuan, 1), "R": round(r, 3), "weighted_R": round(r, 3),
        })
    return trades


def _yuan_daily_stats(trades, label):
    """按 entry_date 聚合每手金额(元)，给方案U用的日内统计。"""
    if not trades:
        return None
    day = defaultdict(float)
    day_n = defaultdict(int)
    for tr in trades:
        d = tr.get("entry_date")
        if d:
            day[d] += tr.get("pnl_yuan", 0.0)
            day_n[d] += 1
    days = sorted(day)
    rv = [day[d] for d in days]
    pos_d = sum(1 for r in rv if r > 0)
    neg_d = sum(1 for r in rv if r < 0)
    gp = sum(r for r in rv if r > 0)
    gl = -sum(r for r in rv if r < 0)
    pf = gp / gl if gl > 0 else float("inf")
    best = max(days, key=lambda d: day[d])
    worst = min(days, key=lambda d: day[d])
    return "\n".join([
        f"── {label}（{len(days)} 交易日 / {len(trades)} 笔，一手）",
        f"  盈利日 {pos_d} / 亏损日 {neg_d}   日胜率 {pos_d / len(days):.1%}",
        f"  盈利日合计 {gp:+.0f}元 / 亏损日合计 {gl:+.0f}元   日级盈亏比 {pf:.2f}",
        f"  累计 {sum(rv):+.0f}元   日均 {sum(rv) / len(days):+.0f}元",
        f"  最佳日 {best} {day[best]:+.0f}元   最差日 {worst} {day[worst]:+.0f}元",
    ])


def scheme_user(all_bars, presets, warmup=120, out_dir=None):
    """
    方案 U：按用户实盘口径回测——共振+双底亮灯 → （量仓动能确认）→
    每手固定止盈100元/止损50元。
    输出两档对比：
      v0   ：共振+双底信号直接 t+1 入场（纯信号，看灯就做的理论值）
      user ：信号后次根量仓配合才入场（放量+增仓+收线同向）——贴实盘做法
    每品种金额(元)分开给；R分母为tick对齐后的实际初始风险。
    """
    print("\n" + "=" * 60)
    print("方案 U: 用户实盘系统（共振+双底 → 量仓动能 → 止盈100元/止损50元 一手）")
    print("机制演示；100/50元为tick对齐前设定，R=净盈亏/对齐后实际初始风险")
    print("=" * 60)

    # ---- 预计算/加载 rows（与方案B共用 v2 磁盘缓存）----
    print("\n[1/2] 加载/预计算评分行 ...")
    rows_all = {}
    for preset in presets:
        sym = preset["symbol"]
        name = preset["name"]
        bars, _ = all_bars.get(sym, (None, None))
        if not bars:
            continue
        rows, cache_path = load_rows_cache(out_dir, sym, warmup=warmup) if out_dir else (None, None)
        if rows is None:
            t0 = time.time()
            ds = precompute_downsampled(bars)
            rows = precompute_rows(bars, ds, warmup, tick=preset["tick"])
            tag = f"预计算 {time.time()-t0:.0f}s"
            if out_dir:
                save_rows_cache(out_dir, sym, rows, warmup=warmup)
        else:
            tag = "rows缓存命中"
        rows_all[sym] = rows
        print(f"  ✓ {name} ({sym}) [{tag}]")

    # ---- 重放两档 ----
    print("\n[2/2] 重放 v0(纯信号) vs user(量仓动能确认) ...")
    t0_trades, u_trades = [], []
    for preset in presets:
        sym = preset["symbol"]
        bars, _ = all_bars.get(sym, (None, None))
        rows = rows_all.get(sym)
        if not bars or not rows:
            continue
        t0_trades += replay_user_symbol(preset, bars, warmup, rows, need_conf=False)
        u_trades += replay_user_symbol(preset, bars, warmup, rows, need_conf=True)

    # ---- 每品种金额明细 ----
    print("\n--- 每品种（一手，金额=元；R以实际初始风险归一）---")
    hdr = (f"{'品种':<8}{'v0笔数':>6}{'v0胜率':>8}{'v0累计':>10}"
           f"{'v0盈亏比':>8}  |  {'u笔数':>6}{'u胜率':>8}{'u累计':>10}{'u盈亏比':>8}")
    print(hdr)
    print("-" * len(hdr))
    g_v0 = g_u = 0.0
    for preset in presets:
        name = preset["name"]
        a = [x for x in t0_trades if x["symbol"] == preset["symbol"]]
        b = [x for x in u_trades if x["symbol"] == preset["symbol"]]
        def brief(ts):
            if not ts:
                return 0, 0.0, 0.0, 0.0
            n = len(ts)
            win = sum(1 for x in ts if x["pnl_yuan"] > 0) / n
            cum = sum(x["pnl_yuan"] for x in ts)
            gp = sum(x["pnl_yuan"] for x in ts if x["pnl_yuan"] > 0)
            gl = -sum(x["pnl_yuan"] for x in ts if x["pnl_yuan"] < 0)
            pf = gp / gl if gl > 0 else float("inf")
            return n, win, cum, pf
        n0, w0, c0, p0 = brief(a)
        n1, w1, c1, p1 = brief(b)
        g_v0 += c0
        g_u += c1
        print(f"{name:<8}{n0:>6}{w0:>8.1%}{c0:>+10.0f}{p0:>8.2f}"
              f"  |  {n1:>6}{w1:>8.1%}{c1:>+10.0f}{p1:>8.2f}")
    print(f"{'合计':<8}{sum(len(x) for x in t0_trades):>6}{'':>8}{g_v0:>+10.0f}{'':>8}"
          f"  |  {sum(len(x) for x in u_trades):>6}{'':>8}{g_u:>+10.0f}")

    # ---- 出场原因分布（u 档）----
    print("\n--- 出场原因（user 档）---")
    from collections import Counter
    for reason, cnt in Counter(x["exit_reason"] for x in u_trades).most_common():
        print(f"  {reason:<8} {cnt:>4}")

    # ---- 总体统计 + 日内金额 ----
    print("\n--- 总体机制演示（R以实际初始风险归一）---")
    for label, ts in (("v0 纯信号(t+1入场)", t0_trades), ("user 量仓确认(t+2入场)", u_trades)):
        if not ts:
            print(f"  {label}: 0 笔")
            continue
        st = compute_trade_stats(ts)
        cum_yuan = sum(x["pnl_yuan"] for x in ts)
        print(f"  {label}: {st['n']}笔 胜率{st['winrate']:.1%} "
              f"平均R {st['expR']:+.3f} 盈亏比 {st['pf']:.2f} 累计R {st['cumR']:+.2f} "
              f"累计 {cum_yuan:+.0f}元")
        d = _yuan_daily_stats(ts, label)
        if d:
            print(d)
            print()

    return {"v0": t0_trades, "user": u_trades, "validation_status": "synthetic_demo_only",
            "execution_version": EXECUTION_VERSION}



# ================= 主流程 =================

def main():
    ap = argparse.ArgumentParser(description="面板建议功能历史回测")
    ap.add_argument("--symbols", type=str, default="", help="品种过滤，如 rb,jm")
    ap.add_argument("--warmup", type=int, default=120, help="前 N 根不交易")
    ap.add_argument("--scheme", type=str, default="AB", help="A=IC分析 B=Top3回测 U=用户系统回测（可组合，如 BU）")
    ap.add_argument("--out", type=str, default="backtest", help="输出目录")
    ap.add_argument("--sample", type=int, default=SAMPLE_INTERVAL, help="方案A采样间隔（默认5）")
    ap.add_argument("--no-cache", action="store_true", help="禁用 rows 磁盘缓存（强制全量预计算）")
    ap.add_argument("--allow-synthetic-demo", action="store_true",
                    help="明确仅运行插值机制演示；输出不得用来验证胜率或盈利能力")
    args = ap.parse_args()
    if not args.allow_synthetic_demo:
        ap.error("本脚本含插值小周期，不能提供有效业绩证据；机制演示须显式传 --allow-synthetic-demo")
    print(EXECUTION_LIMITATIONS)
    print("仅机制演示：插值结果不支持胜率、过滤有效或盈利能力结论。")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)

    # 选择品种
    if args.symbols:
        tokens = [t.strip() for t in args.symbols.split(",")]
        presets = []
        for tok in tokens:
            hit = [p for p in symbols_mod.PRESETS
                   if p["symbol"] == tok or product_of(p["symbol"]).lower() == tok.lower()]
            presets.extend(hit)
    else:
        presets = list(symbols_mod.PRESETS)

    if not presets:
        print("无可回测品种。")
        return 1

    # 加载缓存
    print("加载缓存数据 ...")
    all_bars = {}
    for p in presets:
        bars, source = load_cache(out_dir, p["symbol"])
        if bars:
            all_bars[p["symbol"]] = (bars, source)
            print(f"  ✓ {p['name']} ({p['symbol']}): {len(bars)} 根")
        else:
            print(f"  ✗ {p['name']} ({p['symbol']}): 无缓存，跳过")

    if not all_bars:
        print("无可用缓存数据。请先运行 python backtest.py --cache-only")
        return 1

    # 全局 presets_map（供 scheme_b 使用）
    global presets_map
    presets_map = {p["symbol"]: p for p in presets}

    results = {}

    if "A" in args.scheme.upper():
        results["A"] = scheme_a(all_bars, presets, warmup=args.warmup, sample_interval=args.sample)

    if "B" in args.scheme.upper():
        results["B"] = scheme_b(all_bars, presets, warmup=args.warmup,
                                 out_dir=None if args.no_cache else out_dir)

    if "U" in args.scheme.upper():
        results["U"] = scheme_user(all_bars, presets, warmup=args.warmup,
                                   out_dir=None if args.no_cache else out_dir)

    # 输出总结
    print("\n" + "=" * 60)
    print("总结")
    print("=" * 60)
    if "A" in results:
        print("\n方案 A（IC 分析）：")
        print("  插值机制演示：IC 和分组收益不用于判断实际预测力。")
    if "B" in results:
        b = results["B"]
        print("\n方案 B（Top3 组合回测）：")
        if b["baseline"] and b["filtered"]:
            diff = b["filtered"]["cumR"] - b["baseline"]["cumR"]
            print(f"  基准累计R: {b['baseline']['cumR']:+.2f} ({b['baseline']['n']} 笔)")
            print(f"  面板Top3:  {b['filtered']['cumR']:+.2f} ({b['filtered']['n']} 笔)")
            print(f"  差值:      {diff:+.2f}")
            print("  → 仅为本次机制演示差值，不构成面板过滤有效性证据。")
            if b.get("c6"):
                c6 = b["c6"]
                diff_c6 = c6["cumR"] - b["filtered"]["cumR"]
                print(f"\n  C6半仓止盈: {c6['cumR']:+.2f} ({c6['n']} 笔) 胜率={c6['winrate']:.1%}")
                print(f"  vs Top3 差值: {diff_c6:+.2f}")
        else:
            print("  样本不足，无法对比")
    if "U" in results:
        u = results["U"]
        print("\n方案 U（用户实盘系统：共振+双底 → 止盈100元/止损50元 一手）：")
        for label, ts in (("v0 纯信号", u["v0"]), ("user 量仓确认", u["user"])):
            if ts:
                cum = sum(x["pnl_yuan"] for x in ts)
                win = sum(1 for x in ts if x["pnl_yuan"] > 0) / len(ts)
                print(f"  {label}: {len(ts)}笔 胜率{win:.1%} 累计{cum:+.0f}元")
            else:
                print(f"  {label}: 0 笔")
        if u["v0"] and u["user"]:
            d = sum(x["pnl_yuan"] for x in u["user"]) - sum(x["pnl_yuan"] for x in u["v0"])
            print(f"  量仓动能过滤差值: {d:+.0f}元")

    return 0


if __name__ == "__main__":
    sys.exit(main())
