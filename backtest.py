# -*- coding: utf-8 -*-
"""
backtest.py — 双轴评分系统历史回测（一期双轴 + 二期 regime）。

重放规则（严格无前视）：每根 1h K线收盘 t，只用 ≤t 的数据跑
double_bottom.evaluate + entry_logic.detect_regime + entry_logic.decide。
- 开仓：quadrant ∈ {main, probe, confirm} 且 direction ≠ none → 次根开盘价入场；
  仓位只影响 R 倍统计（probe=0.25R、confirm=0.5R、main=1.0R 权重）。
- 出场：止损（decide 的 stop.price）/ 目标 target1 / 超时 60 根收盘强平；
  同一根同时触及止损和目标 → 保守计为止损；最多持一单，持仓期间新信号忽略。
- 成本：每来回 2 跳（滑点+手续费近似），按各品种 tick 折算进 R。

数据：symbols.py 全量 11 品种，优先主力连续合约（SHFE.ag2608 → KQ.m@SHFE.ag），
拉不到主连回退具体合约并在数据质量说明中注明。
账号仅从 user_config.json 读取、仅在脚本内使用，任何输出/日志/报告禁止出现。

用法：
  python backtest.py                          # 全量 11 品种 250 交易日
  python backtest.py --days 60 --symbols ag,rb   # 小样本验证
  python backtest.py --symbols ss,m,jm --append  # 分批跑后合并出总报告
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import double_bottom
import entry_logic
import symbols as symbols_mod

WEIGHTS = {"main": 1.0, "confirm": 0.5, "probe": 0.25}
TRADE_QUADRANTS = ("main", "probe", "confirm")
FREE_KLINE_CAP = 8900          # 免费账号单次拉取上限留余量

CSV_FIELDS = ["symbol", "name", "direction", "quadrant", "regime", "weight",
              "entry_time", "entry_price", "stop", "target",
              "exit_time", "exit_price", "exit_reason", "bars_held",
              "pnl_ticks", "R", "weighted_R"]


# ---------------- 品种/合约映射 ----------------

def product_of(preset_symbol):
    """SHFE.ag2608 → ag；CZCE.SA609 → SA。"""
    return "".join(ch for ch in preset_symbol.split(".")[1] if not ch.isdigit())


def kq_symbol(preset_symbol):
    """SHFE.ag2608 → KQ.m@SHFE.ag（主力连续）。"""
    exch = preset_symbol.split(".")[0]
    return f"KQ.m@{exch}.{product_of(preset_symbol)}"


def select_presets(arg):
    """--symbols 支持品种代码(ag,rb)或完整合约(SHFE.ag2608)，逗号分隔；缺省全量。"""
    if not arg:
        return list(symbols_mod.PRESETS)
    out = []
    for tok in arg.split(","):
        tok = tok.strip()
        hit = [p for p in symbols_mod.PRESETS
               if p["symbol"] == tok or product_of(p["symbol"]).lower() == tok.lower()]
        if not hit:
            print(f"  [warn] --symbols 项 {tok} 无匹配预设，跳过")
        out.extend(hit)
    return out


# ---------------- 数据拉取 ----------------

def fetch_bars(api, preset, days, notes):
    """优先主连，失败回退具体合约。返回 (bars, source)，全失败返回 (None, None)。"""
    import pandas as pd
    data_len = min(FREE_KLINE_CAP, days * 8 + 130)   # 1h 每日约 6-8 根 + warmup 余量
    for sym, kind in ((kq_symbol(preset["symbol"]), "主连"), (preset["symbol"], "具体合约")):
        try:
            df = api.get_kline_serial(sym, 3600, data_length=data_len)
            n = 0 if df is None else len(df)
            if n < 200:
                raise ValueError(f"仅 {n} 根，不足回测")
            dts = pd.to_datetime(df["datetime"], unit="ns", utc=True).dt.tz_convert("Asia/Shanghai")
            bars = []
            for i in range(len(df)):
                c = df["close"].iloc[i]
                if math.isnan(c):
                    continue
                ooi, coi = df["open_oi"].iloc[i], df["close_oi"].iloc[i]
                oi = 0 if (math.isnan(ooi) or math.isnan(coi)) else int(coi - ooi)
                v = df["volume"].iloc[i]
                bars.append({"dt": dts.iloc[i].strftime("%Y-%m-%d %H:%M"),
                             "open": float(df["open"].iloc[i]), "high": float(df["high"].iloc[i]),
                             "low": float(df["low"].iloc[i]), "close": float(c),
                             "volume": 0 if math.isnan(v) else int(v), "oi": oi})
            if len(bars) < 200:
                raise ValueError(f"有效K线仅 {len(bars)} 根")
            return bars, f"{kind} {sym}"
        except Exception as e:  # noqa: BLE001 单源失败尝试下一源
            notes.append(f"{preset['name']} {kind} {sym} 拉取失败：{type(e).__name__}: {e}")
            time.sleep(1.0)
    return None, None


# ---------------- 无前视重放 ----------------

def run_symbol(preset, bars, warmup, timeout_bars):
    """单品种重放，返回逐笔交易列表。严格无前视：t 根收盘只用 bars[:t+1]。"""
    tick = preset["tick"]
    trades = []
    pos = None
    for t in range(warmup, len(bars)):
        bar = bars[t]
        if pos:
            sgn = pos["sgn"]
            exit_price = reason = None
            hit_stop = bar["low"] <= pos["stop"] if sgn > 0 else bar["high"] >= pos["stop"]
            hit_tgt = bar["high"] >= pos["target"] if sgn > 0 else bar["low"] <= pos["target"]
            if hit_stop:                    # 同根双触 → 保守计止损
                exit_price, reason = pos["stop"], "stop"
            elif hit_tgt:
                exit_price, reason = pos["target"], "target"
            elif t - pos["entry_i"] >= timeout_bars:
                exit_price, reason = bar["close"], "timeout"
            if exit_price is None:
                continue
            pnl = (exit_price - pos["entry"]) * sgn - 2 * tick   # 成本 2 跳/来回
            r = pnl / pos["risk"]
            trades.append({**pos["meta"], "exit_time": bar["dt"], "exit_price": round(exit_price, 4),
                           "exit_reason": reason, "bars_held": t - pos["entry_i"] + 1,
                           "pnl_ticks": round(pnl / tick, 1), "R": round(r, 3),
                           "weighted_R": round(r * pos["weight"], 3)})
            pos = None
            continue
        # ---- 空仓：跑引擎找信号（只用到 t，无前视）----
        kb = bars[:t + 1]
        ev = double_bottom.evaluate(kb)
        rg = entry_logic.detect_regime(kb)
        d = entry_logic.decide(ev, bar["close"], atr=double_bottom._atr(kb), regime=rg)
        if d["quadrant"] not in TRADE_QUADRANTS or d["direction"] not in ("long", "short") \
                or not d["stop"] or t + 1 >= len(bars):
            continue
        entry = bars[t + 1]["open"]
        sgn = 1 if d["direction"] == "long" else -1
        stop = d["stop"]["price"]
        risk = (entry - stop) * sgn
        if risk <= 0:                       # 次根开盘已越过止损，信号失效
            continue
        p = ev["pattern"]
        t1 = (ev.get("plan") or {}).get("target1") \
            or round(2 * p["neck"] - p["L1"]["price"], 2)   # 丈量目标：2×颈位−L1（双向通用）
        pos = {"entry_i": t + 1, "entry": entry, "stop": stop, "target": t1,
               "sgn": sgn, "risk": risk, "weight": WEIGHTS[d["quadrant"]],
               "meta": {"symbol": preset["symbol"], "name": preset["name"],
                        "direction": d["direction"], "quadrant": d["quadrant"],
                        "regime": rg["regime"], "weight": WEIGHTS[d["quadrant"]],
                        "entry_time": bars[t + 1]["dt"], "entry_price": entry,
                        "stop": stop, "target": t1}}
    if pos:                                 # 数据末尾强平
        bar = bars[-1]
        pnl = (bar["close"] - pos["entry"]) * pos["sgn"] - 2 * tick
        r = pnl / pos["risk"]
        trades.append({**pos["meta"], "exit_time": bar["dt"], "exit_price": bar["close"],
                       "exit_reason": "end", "bars_held": len(bars) - pos["entry_i"],
                       "pnl_ticks": round(pnl / tick, 1), "R": round(r, 3),
                       "weighted_R": round(r * pos["weight"], 3)})
    return trades


# ---------------- 统计 ----------------

def stats_of(trades):
    """交易次数/胜率/平均R/期望R/盈亏比/最大连亏/累计R（按加权R）。"""
    n = len(trades)
    if not n:
        return None
    wr = [x["weighted_R"] for x in trades]
    wins = [x for x in wr if x > 0]
    gw = sum(wins)
    gl = -sum(x for x in wr if x < 0)
    consec = mx = 0
    for x in wr:
        consec = consec + 1 if x <= 0 else 0
        mx = max(mx, consec)
    return {"n": n, "winrate": len(wins) / n,
            "avgR": sum(x["R"] for x in trades) / n,
            "expR": sum(wr) / n, "pf": (gw / gl) if gl > 0 else float("inf"),
            "max_consec_loss": mx, "cumR": sum(wr)}


def fmt_pct(x):
    return f"{x * 100:.1f}%"


def fmt_pf(x):
    return "∞" if x == float("inf") else f"{x:.2f}"


def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def stat_row(label, s):
    if not s:
        return [label, 0, "--", "--", "--", "--", "--", "0.00"]
    return [label, s["n"], fmt_pct(s["winrate"]), f"{s['avgR']:+.3f}", f"{s['expR']:+.3f}",
            fmt_pf(s["pf"]), s["max_consec_loss"], f"{s['cumR']:+.2f}"]


STAT_HEADERS = ["维度", "交易数", "胜率", "平均R", "期望R(加权)", "盈亏比PF", "最大连亏", "累计R(加权)"]


def build_report(all_trades, quality, args):
    L = []
    L.append("# 双轴评分系统历史回测报告")
    L.append("")
    L.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- 区间：近 {args.days} 个交易日 1h K线｜warmup {args.warmup} 根｜超时 {args.timeout_bars} 根强平")
    L.append("- 成本：每来回 2 跳（滑点+手续费）｜成交假设：次根开盘价入场，止损/目标按触及价成交，"
             "同根双触保守计止损｜仓位权重：main 1.0 / confirm 0.5 / probe 0.25（只影响加权 R）")
    L.append("- 引擎：double_bottom.evaluate + entry_logic.detect_regime + entry_logic.decide（无前视重放）")
    L.append("")
    L.append("## 一、总体表现")
    L.append("")
    L.append(md_table([stat_row("全部交易", stats_of(all_trades))], STAT_HEADERS))
    L.append("")
    L.append("## 二、分象限（仓位逻辑有效性）")
    L.append("")
    by_q = defaultdict(list)
    for t in all_trades:
        by_q[t["quadrant"]].append(t)
    L.append(md_table([stat_row(q, stats_of(by_q.get(q, []))) for q in ("main", "confirm", "probe")],
                      STAT_HEADERS))
    L.append("")
    L.append("## 三、分市况（regime 判定有效性）")
    L.append("")
    trend = [t for t in all_trades if t["regime"] in ("trend_up", "trend_down")]
    rang = [t for t in all_trades if t["regime"] == "range"]
    L.append(md_table([stat_row("趋势市", stats_of(trend)), stat_row("震荡市", stats_of(rang))],
                      STAT_HEADERS))
    L.append("")
    L.append("## 四、分品种")
    L.append("")
    by_s = defaultdict(list)
    for t in all_trades:
        by_s[t["name"]].append(t)
    rows = sorted(((name, stats_of(ts)) for name, ts in by_s.items()),
                  key=lambda kv: (kv[1]["cumR"] if kv[1] else 0), reverse=True)
    L.append(md_table([stat_row(n, s) for n, s in rows], STAT_HEADERS))
    L.append("")
    L.append("## 五、数据质量说明")
    L.append("")
    for q in quality:
        L.append(f"- {q}")
    L.append("")
    L.append("## 六、出场原因分布")
    L.append("")
    by_r = defaultdict(list)
    for t in all_trades:
        by_r[t["exit_reason"]].append(t)
    L.append(md_table([stat_row(r, stats_of(ts)) for r, ts in
                       sorted(by_r.items(), key=lambda kv: -len(kv[1]))], STAT_HEADERS))
    L.append("")
    return "\n".join(L)


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description="双轴评分系统历史回测")
    ap.add_argument("--days", type=int, default=250, help="回溯交易日数（默认 250）")
    ap.add_argument("--symbols", type=str, default="", help="品种过滤，如 ag,rb 或完整合约，逗号分隔")
    ap.add_argument("--warmup", type=int, default=120, help="前 N 根不交易（默认 120）")
    ap.add_argument("--timeout-bars", type=int, default=60, help="超时强平根数（默认 60）")
    ap.add_argument("--sleep", type=float, default=1.5, help="品种间 sleep 秒数（免费账号限流）")
    ap.add_argument("--out", type=str, default="backtest", help="输出目录（默认 backtest）")
    ap.add_argument("--append", action="store_true", help="追加到已有 results.csv 后合并出总报告")
    ap.add_argument("--report-only", action="store_true", help="不拉数，用既有结果重建报告")
    args = ap.parse_args()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "results.csv")
    q_path = os.path.join(out_dir, "quality.txt")

    def read_csv_trades():
        rows = []
        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    for k in ("entry_price", "stop", "target", "exit_price", "pnl_ticks",
                              "R", "weighted_R", "weight"):
                        row[k] = float(row[k])
                    row["bars_held"] = int(row["bars_held"])
                    rows.append(row)
        return rows

    # --report-only：不拉数不回测，用既有 results.csv + quality.txt 重建报告
    if args.report_only:
        all_trades = read_csv_trades()
        quality = []
        if os.path.exists(q_path):
            with open(q_path, "r", encoding="utf-8") as f:
                quality = [l for l in f.read().splitlines() if l.strip()]
        with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
            f.write(build_report(all_trades, quality, args))
        s = stats_of(all_trades)
        if s:
            print(f"总体：{s['n']} 笔 | 胜率 {fmt_pct(s['winrate'])} | 期望R {s['expR']:+.3f} | "
                  f"PF {fmt_pf(s['pf'])} | 累计加权R {s['cumR']:+.2f}")
        print(f"报告已重建：{os.path.join(out_dir, 'report.md')}")
        return 0

    presets = select_presets(args.symbols)
    if not presets:
        print("无可回测品种。")
        return 1

    # 账号：仅脚本内使用，禁止输出
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_config.json")
    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        auth = json.load(f)
    from tqsdk import TqApi, TqAuth
    api = TqApi(auth=TqAuth(auth.get("tq_user", ""), auth.get("tq_password", "")))

    new_trades, quality, notes = [], [], []
    try:
        for i, p in enumerate(presets):
            t0 = time.time()
            bars, source = fetch_bars(api, p, args.days, notes)
            if not bars:
                quality.append(f"{p['name']}（{p['symbol']}）：拉取失败，已跳过")
                print(f"[{i + 1}/{len(presets)}] {p['name']} 拉取失败，跳过")
                continue
            trades = run_symbol(p, bars, args.warmup, args.timeout_bars)
            new_trades.extend(trades)
            span = f"{bars[0]['dt']} ~ {bars[-1]['dt']}"
            quality.append(f"{p['name']}：{source}，{len(bars)} 根 1h（{span}），"
                           f"交易 {len(trades)} 笔，耗时 {time.time() - t0:.0f}s")
            print(f"[{i + 1}/{len(presets)}] {p['name']} {source} {len(bars)}根 "
                  f"→ {len(trades)} 笔（{time.time() - t0:.0f}s）")
            if i < len(presets) - 1:
                time.sleep(args.sleep)
    finally:
        api.close()
    quality += [f"拉取备注：{n}" for n in notes]

    # 合并既有 CSV（--append 分批跑）
    all_trades = []
    if args.append:
        rerun = {p["symbol"] for p in presets}
        all_trades = [t for t in read_csv_trades() if t["symbol"] not in rerun]
    all_trades += new_trades

    # 数据质量说明持久化（--append 时合并，重跑品种去旧留新；旧拉取备注仅保留本轮）
    old_q = []
    if args.append and os.path.exists(q_path):
        with open(q_path, "r", encoding="utf-8") as f:
            old_q = [l for l in f.read().splitlines() if l.strip()
                     and not l.startswith("拉取备注：")]
    rerun_names = {p["name"] for p in presets}
    old_q = [l for l in old_q if not any(l.startswith(n + "：") for n in rerun_names)]
    quality = old_q + quality
    with open(q_path, "w", encoding="utf-8") as f:
        f.write("\n".join(quality) + "\n")

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for t in all_trades:
            w.writerow({k: t.get(k) for k in CSV_FIELDS})

    report = build_report(all_trades, quality, args)
    rp = os.path.join(out_dir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(report)
    s = stats_of(all_trades)
    if s:
        print(f"\n总体：{s['n']} 笔 | 胜率 {fmt_pct(s['winrate'])} | 期望R {s['expR']:+.3f} | "
              f"PF {fmt_pf(s['pf'])} | 累计加权R {s['cumR']:+.2f}")
    print(f"报告：{rp}\n明细：{csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
