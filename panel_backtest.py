# -*- coding: utf-8 -*-
"""
panel_backtest.py — 面板建议功能历史回测（方案A: IC分析 + 方案B: Top3组合回测）。

核心思路：
  在每个历史时点 t（1h K线收盘），对全品种计算面板评分（模拟 renderAdvice 逻辑），
  然后验证：
    A) 评分与未来 N 根 K线收益的相关性（IC 分析）
    B) 每天按评分选 Top3 品种，只交易这些品种的信号，对比全部交易的基准

数据限制：缓存只有 1h K线，track_a 共振需要 15min/3min/30s 三周期。
处理方案：从 1h K线降采样模拟（1h→15min×4→3min×20→30s×40），
这不会完美精确，但保留方向性和相对排序，足以验证面板逻辑的有效性。

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

# ---- 面板评分权重（与 renderAdvice JS 完全一致）----
W_RESONANCE = 50
W_CERTAINTY = 0.5
W_ER = 20
W_LOCATION = 0.3
W_QUADRANT = 30

# ---- 方案B参数 ----
TOP_N = 3                    # 每天选 Top N
MAX_HOLD_BARS = 60           # 最大持仓根数（与 backtest.py 一致）
COST_TICKS = 2               # 每来回成本跳数

# ---- 方案A采样间隔（每 N 根 K线计算一次，默认5）----
SAMPLE_INTERVAL = 5


def product_of(preset_symbol):
    """SHFE.ag2612 → ag"""
    return "".join(ch for ch in preset_symbol.split(".")[1] if not ch.isdigit())


def load_cache(out_dir, symbol, days=250):
    path = os.path.join(out_dir, "cache", f"{symbol.replace('.', '_')}_{days}d.json")
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d["bars"], d["source"]


def downsample_bars(bars_1h, target_sec):
    """
    从 1h K线降采样到目标周期。
    3600s → 900s(15min): ×4；3600s → 180s(3min): ×20；3600s → 30s: ×40
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


def compute_panel_score_fast(bars_1h, bars_15m, bars_3m, bars_30s):
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
    res = resonance.evaluate(bars_15m, bars_3m, bars_30s)

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
            sub_30s = ds["30s"][:(t + 1) * 40] # 1h→30s ×40

            ps = compute_panel_score_fast(sub_1h, sub_15m, sub_3m, sub_30s)
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

def scheme_b(all_bars, presets, warmup=120):
    """
    模拟"每天看面板选 Top3 品种，只交易这些品种"的策略：
    1. 每天收盘后，对全品种计算面板评分，选 Top3
    2. 次日只交易这 3 个品种的信号（probe/main/confirm）
    3. 对比"交易全部品种"的基准
    """
    print("\n" + "=" * 60)
    print("方案 B: Top3 组合回测（面板过滤 vs 全品种基准）")
    print("=" * 60)

    # ---- 第一步：对每个品种，逐根重放生成所有交易信号 ----
    print("\n[1/3] 逐品种重放生成信号 ...")
    all_signals = {}  # symbol -> list of signal dicts
    for preset in presets:
        sym = preset["symbol"]
        name = preset["name"]
        bars, _ = all_bars.get(sym, (None, None))
        if not bars:
            continue
        signals = replay_symbol(preset, bars, warmup)
        all_signals[sym] = signals
        print(f"  {name} ({sym}): {len(signals)} 个信号")

    # ---- 第二步：每日选 Top3 ----
    print("\n[2/3] 每日面板评分与 Top3 选择 ...")
    # 收集所有交易日
    all_dates = set()
    for sym, signals in all_signals.items():
        for sig in signals:
            all_dates.add(sig["entry_date"])
    all_dates = sorted(all_dates)
    print(f"  共 {len(all_dates)} 个交易日有信号")

    # 对每个交易日，计算全品种面板评分
    daily_top3 = {}  # date -> [(symbol, score), ...]
    for dt in all_dates:
        day_scores = []
        for preset in presets:
            sym = preset["symbol"]
            bars, _ = all_bars.get(sym, (None, None))
            if not bars:
                continue
            # 找到该日期对应的 bar index（取该日最后一根）
            idx = None
            for i in range(len(bars) - 1, warmup - 1, -1):
                if bars[i]["dt"][:10] <= dt:
                    idx = i
                    break
            if idx is None:
                continue
            sub_1h = bars[:idx + 1]
            ds = precompute_downsampled(sub_1h)
            ps = compute_panel_score_fast(sub_1h, ds["15m"], ds["3m"], ds["30s"])
            if ps and ps["group"] == "active":
                day_scores.append((sym, ps["score"]))
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

    # C6 变体：半仓止盈（对 Top3 信号重新跑一遍）
    c6_trades = []
    for sym, signals in all_signals.items():
        preset = presets_map[sym]
        bars, _ = all_bars.get(sym, (None, None))
        if not bars:
            continue
        c6_signals = replay_symbol(preset, bars, warmup, variant="c6_half")
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
        "baseline": baseline_stats,
        "filtered": filtered_stats,
        "c6": c6_stats,
        "daily_top3": daily_top3,
        "all_signals": all_signals,
    }


def replay_symbol(preset, bars, warmup, variant=None):
    """
    对单品种逐根重放，生成所有交易信号（含入场/出场/R 值）。
    复用 backtest.py 的逻辑，但增加了面板评分记录。
    variant:
      None 或 'baseline' → 原规则
      'c6_half'          → C6 半仓止盈：浮盈≥1R 平 50%，余下止损移到保本位
    """
    tick = preset["tick"]
    timeout_bars = MAX_HOLD_BARS
    trades = []
    pos = None

    # 预计算降采样（一次性，用于面板评分）
    ds = precompute_downsampled(bars)

    # C6 状态
    half_taken = False          # 是否已平掉 50%
    orig_weight = 0.0           # 原始权重（用于拆成两笔）

    for t in range(warmup, len(bars)):
        bar = bars[t]

        # 持仓中：检查出场
        if pos:
            sgn = pos["sgn"]
            exit_price = reason = None

            # ---- C6 半仓止盈：浮盈首次 ≥1R → 平 50%，止损移保本 ----
            if variant == "c6_half" and not half_taken:
                unreal = (bar["close"] - pos["entry"]) * sgn
                if unreal >= pos["risk"]:                    # 浮盈 ≥ 1R
                    half_pnl = unreal * 0.5
                    half_r = (half_pnl - COST_TICKS * tick * 0.5) / pos["risk"]
                    trades.append({
                        **pos["meta"],
                        "exit_time": bar["dt"],
                        "exit_price": round(bar["close"], 4),
                        "exit_reason": "c6_half_profit",
                        "bars_held": t - pos["entry_i"] + 1,
                        "pnl_ticks": round(half_pnl / tick, 1),
                        "R": round(half_r, 3),
                        "weighted_R": round(half_r * pos["weight"] * 0.5, 3),
                        "panel_score": pos.get("panel_score", 0),
                    })
                    half_taken = True
                    pos["stop"] = pos["entry"]                # 余下 50% 止损移到保本
                    pos["weight"] = pos["weight"] * 0.5        # 权重减半
                    continue                                   # 本根不再判其他出场

            hit_stop = bar["low"] <= pos["stop"] if sgn > 0 else bar["high"] >= pos["stop"]
            hit_tgt = bar["high"] >= pos["target"] if sgn > 0 else bar["low"] <= pos["target"]
            if hit_stop:
                exit_price, reason = pos["stop"], "stop"
            elif hit_tgt:
                exit_price, reason = pos["target"], "target"
            elif t - pos["entry_i"] >= timeout_bars:
                exit_price, reason = bar["close"], "timeout"
            if exit_price is None:
                continue
            pnl = (exit_price - pos["entry"]) * sgn - COST_TICKS * tick * (0.5 if half_taken else 1.0)
            r = pnl / pos["risk"] if pos["risk"] > 0 else 0
            trades.append({
                **pos["meta"],
                "exit_time": bar["dt"],
                "exit_price": round(exit_price, 4),
                "exit_reason": reason,
                "bars_held": t - pos["entry_i"] + 1,
                "pnl_ticks": round(pnl / tick, 1),
                "R": round(r, 3),
                "weighted_R": round(r * pos["weight"], 3),
                "panel_score": pos.get("panel_score", 0),
            })
            pos = None
            half_taken = False
            continue

        # 空仓：跑引擎
        kb = bars[:t + 1]
        ev = double_bottom.evaluate(kb)
        rg = entry_logic.detect_regime(kb)
        atr = double_bottom._atr(kb)
        d = entry_logic.decide(ev, bar["close"], atr=atr, regime=rg)
        quadrant = d["quadrant"]

        # 计算面板评分（使用预计算数据）
        sub_15m = ds["15m"][:(t + 1) * 4]
        sub_3m = ds["3m"][:(t + 1) * 20]
        sub_30s = ds["30s"][:(t + 1) * 40]
        ps = compute_panel_score_fast(kb, sub_15m, sub_3m, sub_30s)
        panel_score = ps["score"] if ps else 0

        if quadrant not in ("main", "probe", "confirm") or d["direction"] not in ("long", "short") \
                or not d.get("stop") or t + 1 >= len(bars):
            continue

        entry = bars[t + 1]["open"]
        sgn = 1 if d["direction"] == "long" else -1
        stop = d["stop"]["price"]
        risk = (entry - stop) * sgn
        if risk <= 0:
            continue

        weight = {"main": 1.0, "confirm": 0.25, "probe": 0.25}.get(quadrant, 0.25)
        orig_weight = weight                                   # C6 记录原始权重
        t1 = (ev.get("plan") or {}).get("target1") \
            or round(2 * ev["pattern"]["neck"] - ev["pattern"]["L1"]["price"], 2)

        pos = {
            "entry_i": t + 1, "entry": entry, "stop": stop, "target": t1,
            "sgn": sgn, "risk": risk, "weight": weight,
            "panel_score": panel_score,
            "meta": {
                "symbol": preset["symbol"], "name": preset["name"],
                "direction": d["direction"], "quadrant": quadrant,
                "regime": rg["regime"], "weight": weight,
                "entry_time": bars[t + 1]["dt"],
                "entry_date": bars[t + 1]["dt"][:10],
                "entry_price": entry, "stop": stop, "target": t1,
            },
        }
        half_taken = False                                     # C6 重置

    # 末尾强平
    if pos:
        bar = bars[-1]
        cost_factor = 0.5 if half_taken else 1.0
        pnl = (bar["close"] - pos["entry"]) * pos["sgn"] - COST_TICKS * tick * cost_factor
        r = pnl / pos["risk"] if pos["risk"] > 0 else 0
        trades.append({
            **pos["meta"],
            "exit_time": bar["dt"], "exit_price": bar["close"],
            "exit_reason": "end", "bars_held": len(bars) - pos["entry_i"],
            "pnl_ticks": round(pnl / tick, 1),
            "R": round(r, 3),
            "weighted_R": round(r * pos["weight"], 3),
            "panel_score": pos.get("panel_score", 0),
        })
    return trades


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


# ================= 主流程 =================

def main():
    ap = argparse.ArgumentParser(description="面板建议功能历史回测")
    ap.add_argument("--symbols", type=str, default="", help="品种过滤，如 rb,jm")
    ap.add_argument("--warmup", type=int, default=120, help="前 N 根不交易")
    ap.add_argument("--scheme", type=str, default="AB", help="A=IC分析 B=Top3回测 AB=都跑")
    ap.add_argument("--out", type=str, default="backtest", help="输出目录")
    ap.add_argument("--sample", type=int, default=SAMPLE_INTERVAL, help="方案A采样间隔（默认5）")
    args = ap.parse_args()

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
        results["B"] = scheme_b(all_bars, presets, warmup=args.warmup)

    # 输出总结
    print("\n" + "=" * 60)
    print("总结")
    print("=" * 60)
    if "A" in results:
        print("\n方案 A（IC 分析）：")
        print("  IC > 0.03 = 评分有预测力；IC < 0.03 = 评分无预测力")
        print("  分组收益差异大 = 面板分组有效")
    if "B" in results:
        b = results["B"]
        print("\n方案 B（Top3 组合回测）：")
        if b["baseline"] and b["filtered"]:
            diff = b["filtered"]["cumR"] - b["baseline"]["cumR"]
            print(f"  基准累计R: {b['baseline']['cumR']:+.2f} ({b['baseline']['n']} 笔)")
            print(f"  面板Top3:  {b['filtered']['cumR']:+.2f} ({b['filtered']['n']} 笔)")
            print(f"  差值:      {diff:+.2f}")
            if diff > 0:
                print("  → 面板过滤有效，Top3 优于全品种")
            else:
                print("  → 面板过滤无效或样本不足")
            if b.get("c6"):
                c6 = b["c6"]
                diff_c6 = c6["cumR"] - b["filtered"]["cumR"]
                print(f"\n  C6半仓止盈: {c6['cumR']:+.2f} ({c6['n']} 笔) 胜率={c6['winrate']:.1%}")
                print(f"  vs Top3 差值: {diff_c6:+.2f}")
        else:
            print("  样本不足，无法对比")

    return 0


if __name__ == "__main__":
    sys.exit(main())
