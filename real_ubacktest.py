# -*- coding: utf-8 -*-
"""
real_ubacktest.py — 正版共振系统回测（真实三周期数据）

入场要件（用户实盘口径）:
  1. 共振灯亮：真实 15m(趋势) + 3m(过滤) + 30s(触发) 三腿全满足（收盘时刻评估，防未来函数）
  2. 双底结构成立：decide 有方向 + quadrant∈(main,probe,confirm) + 止损位存在
  3. 方向一致：共振方向 == 双底方向
  4. 次根 1h 放量(>前20根均量) + OI 增仓 + 收线同向 → t+2 开盘入场
出场: +¥100/手 止盈 / −¥50/手 止损（按 tick_value 换算跳数，同根先判止损=保守）
      60 根超时强平；末尾强平。R 口径：50元 = 1R。

数据:
  1h   = 15m 聚合（已与 backtest/cache 的 1h 逐根核对 100% 一致，且补足到最新）
  15m/3m/30s = backtest/klines 真实主连数据
窗口: 仅 30s 覆盖区间内（免费账号单次拉取 ~8900 根上限）→ 每品种约 90 根 1h ≈ 8 交易日，
      属机制验证规模，非统计回测规模。

输出: 每品种 灯亮/过双底/过动能 漏斗 + 交易数/胜率/期望(元每笔)/累计盈亏 + 逐笔明细
"""
import argparse
import bisect
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: F401  确保常量被加载
import resonance
import symbols as symbols_mod
import panel_backtest as pb

BASE = os.path.dirname(os.path.abspath(__file__))
KL = os.path.join(BASE, "backtest", "klines")

WARMUP = 120              # 1h 预热（双底/regime 需要；窗口 t 远大于此）
TAIL = 90                 # 三腿尾部窗口（resonance 只读尾部 ~30 根，留余量）
TP_Y = pb.USER_TP_YUAN    # 100 元
SL_Y = pb.USER_SL_YUAN    # 50 元
VOL_MA = pb.USER_VOL_MA   # 20 根均量窗口
MAX_HOLD = pb.MAX_HOLD_BARS
COST = pb.COST_TICKS      # 2 跳往返成本
QUADS = ("main", "probe", "confirm")


def load_klines(sym, label):
    p = os.path.join(KL, f"{sym.replace('.', '_')}_{label}.json")
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)["bars"]


def agg_1h(b15):
    """15m -> 1h 聚合。dt='YYYY-MM-DD HH:00:00'。oi=4×15m 增仓之和(与 1h 增仓同义)，volume 累加。"""
    rows, cur_h, cur = [], None, None
    for b in b15:
        h = b["dt"][:13]
        if cur is None or h != cur_h:
            if cur is not None:
                rows.append(cur)
            cur_h = h
            cur = {"dt": h + ":00:00", "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"],
                   "volume": b.get("volume", 0), "oi": b.get("oi", 0)}
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b.get("volume", 0)
            cur["oi"] += b.get("oi", 0)
    if cur is not None:
        rows.append(cur)
    return rows


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def _tail_at(tarr, arr, close_ts, n=TAIL):
    """截至 close_ts 已收盘的最近 n 根（start < close_ts 即已收盘）。"""
    i = bisect.bisect_left(tarr, close_ts)
    return arr[max(0, i - n):i]


def replay_one(preset):
    sym = preset["symbol"]
    name = preset["name"]
    tick = preset["tick"]
    tv = preset.get("tick_value") or (tick * 10)
    mult = tv / tick
    tp_dist = TP_Y / tv * tick
    sl_dist = SL_Y / tv * tick

    b15 = load_klines(sym, "15m")
    b3 = load_klines(sym, "3m")
    b30 = load_klines(sym, "30s")
    if not (b15 and b3 and b30):
        return None
    b1h = agg_1h(b15)

    t15s = [_ts(b["dt"]) for b in b15]
    t3s = [_ts(b["dt"]) for b in b3]
    t30s = [_ts(b["dt"]) for b in b30]
    w_first = _ts(b30[0]["dt"]) + 30        # 30s 首根收盘
    w_last = _ts(b30[-1]["dt"]) + 30        # 30s 末根收盘
    n = len(b1h)

    stats = Counter()
    dirs = Counter()
    trades = []
    pos = None

    def open_pos(rdir, ps, t, ct):
        nonlocal pos
        entry = b1h[ct + 1]["open"]
        sgn = 1 if rdir == "long" else -1
        pos = {
            "entry_i": ct + 1, "entry": entry, "sgn": sgn,
            "sl": entry - sgn * sl_dist, "tp": entry + sgn * tp_dist,
            "entry_ts": _ts(b1h[ct + 1]["dt"]),          # 1h bar 开盘时刻
            "s30": bisect.bisect_left(t30s, _ts(b1h[ct + 1]["dt"])),
            "meta": {
                "symbol": sym, "name": name, "direction": rdir,
                "quadrant": (ps.get("decide") or {}).get("quadrant", "?"),
                "regime": (ps.get("regime_detail") or {}).get("regime", "?"),
                "entry_time": b1h[ct + 1]["dt"], "entry_date": b1h[ct + 1]["dt"][:10],
                "entry_price": entry, "tp_yuan": TP_Y, "sl_yuan": SL_Y,
                "sig_time": b1h[t]["dt"],
            },
        }

    for t in range(WARMUP, n - 1):
        # ---------- 持仓：出场（30s 逐根，先碰哪个算哪个）----------
        if pos:
            if t < pos["entry_i"]:
                # 尚未到入场 bar（确认根仍在走）——持仓等待，不判出场
                continue
            bar_end = _ts(b1h[t]["dt"]) + 3600        # 本 1h bar 收盘时刻
            sgn, sl, tp = pos["sgn"], pos["sl"], pos["tp"]
            k = pos["s30"]
            ep = rp = et = None
            while k < len(t30s) and t30s[k] + 30 <= bar_end:
                if t30s[k] + 30 <= pos["entry_ts"]:   # 入场前的 30s 根（防御）
                    k += 1
                    continue
                b = b30[k]
                hs = (b["low"] <= sl) if sgn > 0 else (b["high"] >= sl)
                ht = (b["high"] >= tp) if sgn > 0 else (b["low"] <= tp)
                if hs:
                    ep, rp, et = sl, "stop", b["dt"]
                    k += 1
                    break
                if ht:
                    ep, rp, et = tp, "target", b["dt"]
                    k += 1
                    break
                k += 1
            pos["s30"] = k
            if rp is None:
                bar = b1h[t]
                if t - pos["entry_i"] >= MAX_HOLD:
                    ep, rp, et = bar["close"], "timeout", bar["dt"]
                elif k >= len(t30s):
                    # 30s 耗尽（持仓跨出窗口）→ 退回 1h 判定兜底
                    hit_sl = bar["low"] <= sl if sgn > 0 else bar["high"] >= sl
                    hit_tp = bar["high"] >= tp if sgn > 0 else bar["low"] <= tp
                    if hit_sl:
                        ep, rp, et = sl, "stop", bar["dt"]
                    elif hit_tp:
                        ep, rp, et = tp, "target", bar["dt"]
                    elif t - pos["entry_i"] >= MAX_HOLD:
                        ep, rp, et = bar["close"], "timeout", bar["dt"]
                if rp is None:
                    continue
            pnl = (ep - pos["entry"]) * sgn * mult - COST * tv
            trades.append({**pos["meta"], "exit_time": et,
                           "exit_price": round(ep, 4), "exit_reason": rp,
                           "bars_held": t - pos["entry_i"] + 1,
                           "pnl_yuan": round(pnl, 1), "R": round(pnl / SL_Y, 3)})
            pos = None
            continue

        # ---------- 空仓：入场要件 ----------
        close_ts = _ts(b1h[t]["dt"]) + 3600
        if not (w_first <= close_ts <= w_last):
            continue                                # 30s 无覆盖，灯无从判断
        start_ts = close_ts - 3600
        # 扫该 1h bar 内每根 30s 收盘时刻（贴实盘：用户盘中实时看灯，亮过即算）
        i0 = bisect.bisect_left(t30s, start_ts)
        i1 = bisect.bisect_left(t30s, close_ts)
        if i1 - i0 < 1:
            continue
        rdir = None
        for k in range(i0, i1):
            ck = t30s[k] + 30
            a15 = _tail_at(t15s, b15, ck)
            a3 = _tail_at(t3s, b3, ck)
            a30 = _tail_at(t30s, b30, ck)
            if len(a15) < 35 or len(a3) < 35 or len(a30) < 35:
                continue
            res = resonance.evaluate(a15, a3, a30)      # 便宜筛灯
            if res["resonance"]:
                rdir = res["direction"]
                stats["灯亮"] += 1
                dirs[rdir] += 1
                break
        if rdir is None:
            continue

        # 灯亮才跑完整面板判定（双底结构，全前缀）
        ps = pb.compute_panel_score_fast(b1h[:t + 1], a15, a3, a30)
        d = ps.get("decide") or {}
        ddir = d.get("direction", "none")
        quad = d.get("quadrant", "standby")
        stop = (d.get("stop") or {}).get("price")
        if ddir not in ("long", "short"):
            stats["双底无方向"] += 1
            continue
        if rdir != ddir:
            stats["双底反向"] += 1
            continue
        if quad not in QUADS:
            stats["quadrant不符"] += 1
            continue
        if stop is None:
            stats["无止损位"] += 1
            continue
        stats["过双底"] += 1

        # 次根 1h 动能确认
        ct = t + 1
        cbar = b1h[ct]
        lo = max(WARMUP, ct - VOL_MA)
        vma = sum(b1h[j]["volume"] for j in range(lo, ct)) / (ct - lo) if ct > lo else 0
        vol_ok = vma > 0 and cbar["volume"] > vma
        oi_ok = (cbar.get("oi") or 0) > 0
        bar_ok = (cbar["close"] > cbar["open"]) if ddir == "long" \
            else (cbar["close"] < cbar["open"])
        if not (vol_ok and oi_ok and bar_ok):
            stats[f"动能不过 vol={int(vol_ok)} oi={int(oi_ok)} bar={int(bar_ok)}"] += 1
            continue
        stats["过动能"] += 1
        open_pos(rdir, ps, t, ct)

    # 末尾强平
    if pos:
        bar = b1h[-1]
        pnl = (bar["close"] - pos["entry"]) * pos["sgn"] * mult - COST * tv
        trades.append({**pos["meta"], "exit_time": bar["dt"], "exit_price": bar["close"],
                       "exit_reason": "end", "bars_held": n - 1 - pos["entry_i"],
                       "pnl_yuan": round(pnl, 1), "R": round(pnl / SL_Y, 3)})

    return {
        "preset": preset, "window": (b30[0]["dt"], b30[-1]["dt"], len(b30)),
        "stats": stats, "dirs": dict(dirs), "trades": trades,
        "b1h_n": n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="")
    args = ap.parse_args()
    presets = symbols_mod.PRESETS
    if args.symbols:
        toks = [x.strip() for x in args.symbols.split(",")]
        presets = [p for p in presets if p["name"] in toks or
                   pb.product_of(p["symbol"]).lower() in [x.lower() for x in toks] or
                   p["symbol"] in toks]
    if not presets:
        print("no preset"); return 1

    print("=" * 70)
    print("正版共振系统回测: 真实15m/3m/30s 灯亮 + 双底同向 + 次根1h放量/OI  →  止盈100元/止损50元")
    print("数据: klines 真实主连;  窗口 = 30s 覆盖区间(免费账号 ~8900根上限)")
    print("=" * 70)
    all_trades = []
    for p in presets:
        r = replay_one(p)
        if not r:
            print(f"\n✗ {p['name']} ({p['symbol']}) 缺档跳过")
            continue
        tr = r["trades"]
        all_trades += tr
        print(f"\n=== {p['name']} ({p['symbol']})  30s窗口 {r['window'][0]} ~ {r['window'][1]} ({r['window'][2]}根)")
        s = r["stats"]
        funnel = f"  灯亮 {s.get('灯亮', 0)}" \
            + "".join(f" → {k} {v}" for k, v in s.items() if k != "灯亮")
        print(funnel)
        print(f"  dirs={r['dirs']}  1h总根数={r['b1h_n']}")
        if not tr:
            print("  交易: 0 笔")
            continue
        wins = sum(1 for x in tr if x["pnl_yuan"] > 0)
        cum = sum(x["pnl_yuan"] for x in tr)
        exp = cum / len(tr)
        stop = sum(1 for x in tr if x["exit_reason"] == "stop")
        tgt = sum(1 for x in tr if x["exit_reason"] == "target")
        print(f"  交易 {len(tr)} 笔 | 胜率 {wins / len(tr):.1%} | 期望 {exp:+.1f}元/笔 "
              f"| 累计 {cum:+.0f}元 | 出场 stop={stop} target={tgt} "
              f"other={len(tr) - stop - tgt}")
        for x in tr:
            print(f"    {x['direction']:>5} {x['sig_time']} 入@{x['entry_price']} "
                  f"{x['exit_reason']:<7} {x['exit_time']} 盈亏{x['pnl_yuan']:+.0f}元 R={x['R']:+.2f} "
                  f"q={x['quadrant']} r={x['regime']}")

    # 总计
    print("\n" + "=" * 70)
    if all_trades:
        wins = sum(1 for x in all_trades if x["pnl_yuan"] > 0)
        cum = sum(x["pnl_yuan"] for x in all_trades)
        print(f"全品种合计: {len(all_trades)} 笔 | 胜率 {wins / len(all_trades):.1%} "
              f"| 期望 {cum / len(all_trades):+.1f}元/笔 | 累计 {cum:+.0f}元")
        rc = Counter(x["exit_reason"] for x in all_trades)
        print(f"出场原因: {dict(rc)}")
    else:
        print("全品种合计: 0 笔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
