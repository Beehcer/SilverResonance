# -*- coding: utf-8 -*-
"""
fetch_klines.py — 五档K线本地缓存（1d / 1h / 15m / 3m / 30s）。

背景：面板真实共振需要 15m(趋势)/3m(过滤)/30s(触发) 三周期真实K线，
而 backtest/cache/ 里只有 1h（250d），回测用线性插值伪造低周期导致
共振灯永不亮（trigger 腿失效）。本脚本把五档真实K线落地本地。

覆盖策略（免费账号单次拉取上限 FREE_KLINE_CAP=8900 根留余量）：
  - 1d : 250 交易日全覆盖（≈250+ 根）
  - 1h : 直接复用 backtest/cache/{SYM}_250d.json（已有，零联网）
  - 15m: 250 交易日全覆盖（250×35+130=8880 根，贴近上限刚好够）
  - 3m : 上限内最近段（8900 根 ≈ 52 交易日）
  - 30s: 上限内最近段（8900 根 ≈ 8~9 交易日，仅够共振灯点亮验证）
数据源优先主力连续 KQ.m@EXCH.code，失败回退具体合约。

输出：backtest/klines/{SYM}_{1d|1h|15m|3m|30s}.json
  {"symbol","period_sec","source","bars"}  bars 沿用 {dt,open,high,low,close,volume,oi}
  dt 统一 "%Y-%m-%d %H:%M:%S"（30s 同分钟多根不撞键；1h 复用缓存时补 :00 秒）。

用法：
  python fetch_klines.py                     # 全量 12 品种
  python fetch_klines.py --symbols ag,rb     # 指定品种
  python fetch_klines.py --periods 15m,30s   # 指定周期
  python fetch_klines.py --days 120          # 15m 改覆盖 120 交易日
  python fetch_klines.py --force             # 已存在也重拉

注意：需占用天勤账号在线连接；若 8848 面板正在跑，会被互踢下线。
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import symbols as symbols_mod

FREE_KLINE_CAP = 8900          # 免费账号单次拉取上限留余量（与 backtest.py 一致）
KLINE_DIR = "klines"           # 相对 out_dir 的缓存子目录

# (周期秒数, 标签, 每交易日估算根数, 目标天数)
# 每交易日根数估算依据：1h 缓存 2130 根/250 交易日 ≈ 8.5 根/天（含夜盘）
#  15m = 8.5×4 ≈ 34 → 取 35 保余量；3m = 8.5×20 ≈ 170 → 取 175；
#  30s = 8.5×120 ≈ 1020 → 取 1050；1d = 1
PERIODS = [
    (86400, "1d",   1,   250),
    (3600,  "1h",   9,   250),   # 1h 走缓存复用，不联网
    (900,   "15m",  35,  250),
    (180,   "3m",   175, 250),
    (30,    "30s",  1050, 250),
]
_PERIOD_BY_LABEL = {lab: (sec, per_day, days) for sec, lab, per_day, days in PERIODS}


def product_of(preset_symbol):
    """SHFE.ag2608 → ag；CZCE.SA609 → SA。"""
    return "".join(ch for ch in preset_symbol.split(".")[1] if not ch.isdigit())


def kq_symbol(preset_symbol):
    """SHFE.ag2608 → KQ.m@SHFE.ag（主力连续）。"""
    exch = preset_symbol.split(".")[0]
    return f"KQ.m@{exch}.{product_of(preset_symbol)}"


def select_presets(arg):
    """--symbols 支持品种代码(ag,rb)或完整合约(SHFE.ag2608)；缺省全量。"""
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


def kline_path(out_dir, symbol, label):
    return os.path.join(out_dir, KLINE_DIR, f"{symbol.replace('.', '_')}_{label}.json")


def load_1h_cache(out_dir, preset):
    """读既有 backtest/cache/{SYM}_250d.json（1h），补 :00 秒后返回 bars。"""
    path = os.path.join(out_dir, "cache", f"{preset['symbol'].replace('.', '_')}_250d.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    bars = []
    for b in d["bars"]:
        nb = dict(b)
        if len(nb["dt"]) == 16:          # "YYYY-MM-DD HH:MM" → 补秒
            nb["dt"] = nb["dt"] + ":00"
        bars.append(nb)
    return bars


def to_bars(df, period_sec):
    """DataFrame → bars 列表，dt 带秒，oi = close_oi - open_oi。"""
    import pandas as pd
    dts = pd.to_datetime(df["datetime"], unit="ns", utc=True).dt.tz_convert("Asia/Shanghai")
    bars = []
    for i in range(len(df)):
        c = df["close"].iloc[i]
        if math.isnan(c):
            continue
        ooi, coi = df["open_oi"].iloc[i], df["close_oi"].iloc[i]
        oi = 0 if (math.isnan(ooi) or math.isnan(coi)) else int(coi - ooi)
        v = df["volume"].iloc[i]
        bars.append({"dt": dts.iloc[i].strftime("%Y-%m-%d %H:%M:%S"),
                     "open": float(df["open"].iloc[i]), "high": float(df["high"].iloc[i]),
                     "low": float(df["low"].iloc[i]), "close": float(c),
                     "volume": 0 if math.isnan(v) else int(v), "oi": oi})
    return bars


def fetch_period(api, preset, sec, per_day, days, notes):
    """拉单个周期。返回 (bars, source)，全失败返回 (None, None)。"""
    if sec == 3600:
        return None, None                    # 1h 走缓存复用，不在此联网
    data_len = min(FREE_KLINE_CAP, days * per_day + 130)
    for sym, kind in ((kq_symbol(preset["symbol"]), "主连"), (preset["symbol"], "具体合约")):
        try:
            df = api.get_kline_serial(sym, sec, data_length=data_len)
            n = 0 if df is None else len(df)
            if n < 100:
                raise ValueError(f"仅 {n} 根，不足")
            bars = to_bars(df, sec)
            if len(bars) < 100:
                raise ValueError(f"有效K线仅 {len(bars)} 根")
            return bars, f"{kind} {sym}"
        except Exception as e:  # noqa: BLE001 单源失败尝试下一源
            notes.append(f"{preset['name']} {kind} {sym} {sec}s 拉取失败：{type(e).__name__}: {e}")
            time.sleep(1.0)
    return None, None


def main():
    ap = argparse.ArgumentParser(description="五档K线本地缓存（1d/1h/15m/3m/30s）")
    ap.add_argument("--symbols", type=str, default="", help="品种过滤，如 ag,rb")
    ap.add_argument("--periods", type=str, default="",
                    help="周期过滤，逗号分隔：1d,1h,15m,3m,30s（缺省全五档）")
    ap.add_argument("--days", type=int, default=250, help="高周期覆盖目标天数（默认250）")
    ap.add_argument("--sleep", type=float, default=1.5, help="品种间 sleep 秒数（免费账号限流）")
    ap.add_argument("--out", type=str, default="backtest", help="输出目录（默认 backtest）")
    ap.add_argument("--force", action="store_true", help="已存在也重拉")
    args = ap.parse_args()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    os.makedirs(os.path.join(out_dir, KLINE_DIR), exist_ok=True)

    presets = select_presets(args.symbols)
    if not presets:
        print("无可拉取品种。")
        return 1

    labels = [lab for lab in ("1d", "1h", "15m", "3m", "30s")
              if not args.periods or lab in [p.strip() for p in args.periods.split(",")]]
    if not labels:
        print("无可拉取周期。")
        return 1

    # 仅当存在需要联网的非 1h 周期且缺缓存时才建 TqApi（1h 永远走本地复用）
    need_net = any(
        (not os.path.exists(kline_path(out_dir, p["symbol"], lab))) or args.force
        for p in presets for lab in labels if lab != "1h"
    )
    api = None
    if need_net:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_config.json")
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            auth = json.load(f)
        from tqsdk import TqApi, TqAuth
        api = TqApi(auth=TqAuth(auth.get("tq_user", ""), auth.get("tq_password", "")))

    notes = []
    try:
        for i, p in enumerate(presets):
            sym_tag = p["symbol"]
            for lab in labels:
                path = kline_path(out_dir, sym_tag, lab)
                if os.path.exists(path) and not args.force:
                    continue
                sec, per_day, _ = _PERIOD_BY_LABEL[lab]
                if lab == "1h":
                    bars = load_1h_cache(out_dir, p)
                    source = "1h 缓存复用" if bars else None
                else:
                    bars, source = fetch_period(api, p, sec, per_day, args.days, notes)
                if not bars:
                    continue
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"symbol": sym_tag, "period_sec": sec,
                               "source": source, "bars": bars}, f,
                              ensure_ascii=False)
                span = f"{bars[0]['dt']} ~ {bars[-1]['dt']}"
                print(f"[{i + 1}/{len(presets)}] {p['name']} {lab}: {len(bars)}根 ({source}) {span}")
            time.sleep(args.sleep)
    finally:
        if api is not None:
            api.close()

    if notes:
        print("\n-- 失败记录 --")
        for n in notes:
            print(" ", n)
    print("\n完成。缓存目录：" + os.path.join(out_dir, KLINE_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
