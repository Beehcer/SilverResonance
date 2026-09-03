"""
oi_alert_validate.py — 持仓异动检测器离线验证（用 backtest/cache 的 1h 数据回放）。

验证目标（PRD 第 4 步"先离线验证再开工"）：
  1. 触发频率：mult=3/5/8 时 12 品种 250 天各触发几次（太少=漏报、太多=噪音）
  2. 方向命中：long_add 后未来 3/6/12 根(1h)上涨比例；short_add 后下跌比例（>50%=有效）
  3. 收益统计：触发后未来 N 根的平均收益（按方向归一：long_add 记 +ret、short_add 记 -ret）

用法：
  C:\\...\\python.exe oi_alert_validate.py [--mult 5] [--vol-mult 1.0]
输出：终端表格 + backtest/oi_alert_report.md
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oi_alert  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "cache")
OUT_REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "oi_alert_report.md")


def load_all():
    """加载 cache 下全部品种 1h bars。"""
    out = {}
    for fn in sorted(os.listdir(CACHE_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(CACHE_DIR, fn), "r", encoding="utf-8") as f:
            d = json.load(f)
        out[d["symbol"]] = d["bars"]
    return out


def run(all_bars, mult, vol_mult, window=20):
    rows = []          # (symbol, direction, ret_3, ret_6, ret_12)
    per_sym = {}       # symbol -> count
    for sym, bars in all_bars.items():
        cnt = 0
        for i in range(window + 3, len(bars)):
            a = oi_alert.detect_surge(bars[:i + 1], window=window, mult=mult, vol_mult=vol_mult)
            if a is None:
                continue
            cnt += 1
            p0 = bars[i]["close"]
            # 未来 3/6/12 根收益
            rets = []
            for h in (3, 6, 12):
                if i + h < len(bars):
                    p1 = bars[i + h]["close"]
                    rets.append((p1 - p0) / p0)
                else:
                    rets.append(None)
            rows.append((sym, a["direction"], *rets))
        per_sym[sym] = cnt
    return rows, per_sym


def norm_ret(direction, r):
    """按方向归一化：long_add 记 +，short_add 记 -（表示"顺向收益"）。"""
    if r is None:
        return None
    return r if direction == "long_add" else -r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mult", type=float, default=5.0)
    ap.add_argument("--vol-mult", type=float, default=1.0)
    ap.add_argument("--window", type=int, default=20)
    args = ap.parse_args()

    all_bars = load_all()
    print(f"加载 {len(all_bars)} 品种 cache")
    rows, per_sym = run(all_bars, args.mult, args.vol_mult, args.window)

    total = len(rows)
    long_n = sum(1 for r in rows if r[1] == "long_add")
    short_n = sum(1 for r in rows if r[1] == "short_add")
    print(f"\n=== mult={args.mult} vol_mult={args.vol_mult} window={args.window} ===")
    print(f"总触发 {total} 次 | long_add {long_n} | short_add {short_n}")

    # 分品种
    print("\n品种分布:")
    for sym in sorted(all_bars):
        print(f"  {sym:>20}: {per_sym.get(sym, 0)} 次")
    freq = total / max(1, len(all_bars)) / 250
    print(f"  平均每品种每交易日 {freq:.3f} 次")

    # 顺向胜率 / 平均顺向收益
    print("\n顺向命中（long_add 后上涨 / short_add 后下跌）:")
    header = ("horizon", "n", "win_rate", "avg_dir_ret")
    print(f"{'  horizon':>10} {'n':>6} {'胜率':>8} {'平均顺向收益':>14}")
    for h_i in (3, 6, 12):
        idx = 2 + (0 if h_i == 3 else 1 if h_i == 6 else 2)
        vals = []
        for r in rows:
            nr = norm_ret(r[1], r[idx])
            if nr is not None:
                vals.append(nr)
        if not vals:
            continue
        n = len(vals)
        win = sum(1 for v in vals if v > 0) / n
        avg = sum(vals) / n
        print(f"{'h=' + str(h_i):>10} {n:>6} {win:>7.1%} {avg * 100:>12.3f}%")

    # 写报告
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(f"# 持仓异动离线验证报告（mult={args.mult} vol_mult={args.vol_mult} window={args.window}）\n\n")
        f.write(f"- 数据：backtest/cache 12 品种 × 250 天 1h K线（主连，换月跳变可能产生个别假触发）\n")
        f.write(f"- 总触发 {total} 次（long_add {long_n} / short_add {short_n}）\n")
        f.write(f"- 平均每品种每交易日 {freq:.3f} 次（≈每 {'%.0f' % (1/freq if freq else 999)} 交易日 1 次）\n\n")
        f.write("| horizon | n | 顺向胜率 | 平均顺向收益 |\n|---|---|---|---|\n")
        for h_i in (3, 6, 12):
            idx = 2 + (0 if h_i == 3 else 1 if h_i == 6 else 2)
            vals = []
            for r in rows:
                nr = norm_ret(r[1], r[idx])
                if nr is not None:
                    vals.append(nr)
            if vals:
                win = sum(1 for v in vals if v > 0) / len(vals)
                avg = sum(vals) / len(vals)
                f.write(f"| {h_i}h | {len(vals)} | {win:.1%} | {avg * 100:+.3f}% |\n")
        f.write("\n（仅 1h 级别；3m 档需实盘上线后校准）\n")
    print(f"\n报告写入 {OUT_REPORT}")


if __name__ == "__main__":
    main()
