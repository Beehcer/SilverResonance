# -*- coding: utf-8 -*-
"""
fetch_klines.py — 五档K线本地缓存（1d / 1h / 15m / 3m / 30s）。

背景：面板真实共振需要 15m(趋势)/3m(过滤)/30s(触发) 三周期真实K线，
而 backtest/cache/ 里只有 1h（250d），回测用线性插值伪造低周期导致
共振灯永不亮（trigger 腿失效）。本脚本把五档真实K线落地本地。

覆盖策略（免费账号单次拉取上限 FREE_KLINE_CAP=8900 根留余量）：
  - 1d : 250 交易日全覆盖（≈250+ 根）
  - 1h : 优先复用同天数的本地缓存；无缓存或 --force 时联网更新
  - 15m: 250 交易日全覆盖（250×35+130=8880 根，贴近上限刚好够）
  - 3m : 上限内最近段（8900 根 ≈ 52 交易日）
  - 30s: 上限内最近段（8900 根 ≈ 8~9 交易日，仅够共振灯点亮验证）
数据源优先主力连续 KQ.m@EXCH.code，失败回退具体合约。

输出：backtest/klines/{SYM}_{1d|1h|15m|3m|30s}.json
  {"symbol","period_sec","source","fetched_at","bars"}，下载时间为北京时间。
  bars 沿用 {dt,open,high,low,close,volume,oi}；联网更新的 1h 同步到 backtest/cache。
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
import logging
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import symbols as symbols_mod

FREE_KLINE_CAP = 8900          # 免费账号单次拉取上限留余量（与 backtest.py 一致）
KLINE_DIR = "klines"           # 相对 out_dir 的缓存子目录
SERIAL_READY_TIMEOUT = 60      # get_kline_serial 返回后，等待初始化的最大秒数
BEIJING_TZ = timezone(timedelta(hours=8))

# (周期秒数, 标签, 每交易日估算根数, 目标天数)
# 每交易日根数估算依据：1h 缓存 2130 根/250 交易日 ≈ 8.5 根/天（含夜盘）
#  15m = 8.5×4 ≈ 34 → 取 35 保余量；3m = 8.5×20 ≈ 170 → 取 175；
#  30s = 8.5×120 ≈ 1020 → 取 1050；1d = 1
PERIODS = [
    (86400, "1d",   1,   250),
    (3600,  "1h",   9,   250),
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
        exch = tok.split(".", 1)[0] if "." in tok else None
        product = product_of(tok) if exch else tok
        hit = [p for p in symbols_mod.PRESETS
               if product_of(p["symbol"]).lower() == product.lower()
               and (exch is None or p["symbol"].split(".", 1)[0] == exch)]
        if not hit:
            print(f"  [warn] --symbols 项 {tok} 无匹配预设，跳过")
        out.extend(p for p in hit if p not in out)
    return out


def kline_path(out_dir, symbol, label):
    return os.path.join(out_dir, KLINE_DIR, f"{symbol.replace('.', '_')}_{label}.json")


def legacy_1h_path(out_dir, preset, days=250):
    return os.path.join(out_dir, "cache", f"{preset['symbol'].replace('.', '_')}_{days}d.json")


def _load_1h_record(out_dir, preset, days=250):
    """保留旧缓存的数据源和下载时间；复制缓存不能冒充刚下载的数据。"""
    path = legacy_1h_path(out_dir, preset, days)
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
    return {**d, "bars": bars}


def load_1h_cache(out_dir, preset, days=250):
    """兼容原调用：读取 1h 缓存并补秒，缺省仍读 250d。"""
    record = _load_1h_record(out_dir, preset, days)
    return record["bars"] if record else None


def atomic_write_json(path, payload):
    """同目录临时文件 + 原子替换；写入失败时保留完整旧文件。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="." + os.path.basename(path) + ".",
                                         suffix=".tmp", delete=False) as f:
            temp_path = f.name
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def create_api():
    """只在确实需要下载时读取凭据；SDK 日志不得输出认证信息。"""
    logging.disable(logging.CRITICAL)
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_config.json")
    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        auth = json.load(f)
    from tqsdk import TqApi, TqAuth
    return TqApi(auth=TqAuth(auth.get("tq_user", ""), auth.get("tq_password", "")),
                 debug=False, disable_print=True)


def to_bars(df, period_sec):
    """DataFrame → bars 列表，dt 带秒，oi = close_oi - open_oi。"""
    import pandas as pd
    dts = pd.to_datetime(df["datetime"], unit="ns", utc=True).dt.tz_convert("Asia/Shanghai")
    bars = []
    for i in range(len(df)):
        prices = [float(df[key].iloc[i]) for key in ("open", "high", "low", "close")]
        if not all(math.isfinite(value) for value in prices) or pd.isna(dts.iloc[i]) \
                or df["datetime"].iloc[i] <= 0:
            continue
        ooi, coi = df["open_oi"].iloc[i], df["close_oi"].iloc[i]
        oi = int(coi - ooi) if math.isfinite(ooi) and math.isfinite(coi) else 0
        v = df["volume"].iloc[i]
        bars.append({"dt": dts.iloc[i].strftime("%Y-%m-%d %H:%M:%S"),
                     "open": prices[0], "high": prices[1], "low": prices[2], "close": prices[3],
                     "volume": int(v) if math.isfinite(v) else 0, "oi": oi})
    return bars


def fetch_period(api, preset, sec, per_day, days, notes):
    """拉单个周期。返回 (bars, source)，全失败返回 (None, None)。"""
    data_len = min(FREE_KLINE_CAP, days * per_day + 130)
    for sym, kind in ((kq_symbol(preset["symbol"]), "主连"), (preset["symbol"], "具体合约")):
        try:
            df = api.get_kline_serial(sym, sec, data_length=data_len)
            if df is None:
                raise ValueError("未返回K线序列")
            # SDK 的同步接口通常已等待初始化；显式确认，不能仅凭 DataFrame 长度判断。
            deadline = time.time() + SERIAL_READY_TIMEOUT
            while not api.is_serial_ready(df):
                if time.time() >= deadline or not api.wait_update(deadline=deadline):
                    raise TimeoutError("K线初始化超时")
            n = 0 if df is None else len(df)
            if n < 100:
                raise ValueError(f"仅 {n} 根，不足")
            bars = to_bars(df, sec)
            if len(bars) < 100:
                raise ValueError(f"有效K线仅 {len(bars)} 根")
            return bars, f"{kind} {sym}"
        except Exception as e:  # noqa: BLE001 单源失败尝试下一源
            # SDK 异常可能带连接/认证细节，只记录异常类型和不含凭据的请求信息。
            notes.append(f"{preset['name']} {kind} {sym} {sec}s 拉取失败：{type(e).__name__}")
            time.sleep(1.0)
    return None, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="五档K线本地缓存（1d/1h/15m/3m/30s）")
    ap.add_argument("--symbols", type=str, default="", help="品种过滤，如 ag,rb")
    ap.add_argument("--periods", type=str, default="",
                    help="周期过滤，逗号分隔：1d,1h,15m,3m,30s（缺省全五档）")
    ap.add_argument("--days", type=int, default=250, help="高周期覆盖目标天数（默认250）")
    ap.add_argument("--sleep", type=float, default=1.5, help="品种间 sleep 秒数（免费账号限流）")
    ap.add_argument("--out", type=str, default="backtest", help="输出目录（默认 backtest）")
    ap.add_argument("--force", action="store_true", help="已存在也重拉")
    args = ap.parse_args(argv)
    if args.days <= 0 or args.sleep < 0:
        ap.error("--days 必须大于 0，--sleep 不能为负数")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    os.makedirs(os.path.join(out_dir, KLINE_DIR), exist_ok=True)

    presets = select_presets(args.symbols)
    if not presets:
        print("无可拉取品种。")
        return 1

    requested = {p.strip() for p in args.periods.split(",")} if args.periods else set(_PERIOD_BY_LABEL)
    unknown = requested - set(_PERIOD_BY_LABEL)
    if unknown:
        print("未知周期：" + ", ".join(sorted(unknown)))
        return 1
    labels = [lab for lab in _PERIOD_BY_LABEL if lab in requested]
    if not labels:
        print("无可拉取周期。")
        return 1

    api = None
    api_failed = False
    close_failed = False
    notes = []
    counts = {"success": 0, "skipped": 0, "failed": 0}
    # 先判断是否需要联网，纯缓存复用无需账户连接。联网后先解析真实主力，
    # 再按品种重匹配（用户可能用的是刷新前的具体合约作为 --symbols 参数）。
    need_net = False
    for p in presets:
        for lab in labels:
            if os.path.exists(kline_path(out_dir, p["symbol"], lab)) and not args.force:
                continue
            if lab == "1h" and not args.force:
                try:
                    cached = _load_1h_record(out_dir, p, args.days)
                    if cached and cached["bars"]:
                        continue
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            need_net = True

    try:
        if need_net:
            try:
                api = create_api()
                selected_keys = [(p["symbol"].split(".")[0], product_of(p["symbol"]).lower())
                                 for p in presets]
                symbols_mod.refresh_from_api(api)
                current = {(p["symbol"].split(".")[0], product_of(p["symbol"]).lower()): p
                           for p in symbols_mod.PRESETS}
                presets = [current[key] for key in selected_keys]
            except Exception as e:
                api_failed = True
                notes.append(f"行情连接或真实主力刷新失败：{type(e).__name__}")
        for i, p in enumerate(presets):
            sym_tag = p["symbol"]
            for lab in labels:
                path = kline_path(out_dir, sym_tag, lab)
                if os.path.exists(path) and not args.force:
                    counts["skipped"] += 1
                    continue
                sec, per_day, _ = _PERIOD_BY_LABEL[lab]
                try:
                    cached = None
                    if lab == "1h" and not args.force:
                        try:
                            cached = _load_1h_record(out_dir, p, args.days)
                            if cached and not cached["bars"]:
                                cached = None
                        except (OSError, ValueError, KeyError, TypeError) as e:
                            notes.append(f"{p['name']} 1h 旧缓存不可用，改为下载：{type(e).__name__}")
                    if cached:
                        bars = cached["bars"]
                        source = cached.get("source") or "本地1h缓存（原数据源未记录）"
                        fetched_at = cached.get("fetched_at")  # 旧文件未知下载时间时保持 null
                    else:
                        if api is None or api_failed:
                            counts["failed"] += 1
                            continue
                        bars, source = fetch_period(api, p, sec, per_day, args.days, notes)
                        if not bars:
                            counts["failed"] += 1
                            continue
                        fetched_at = datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
                    payload = {"symbol": sym_tag, "period_sec": sec, "source": source,
                               "fetched_at": fetched_at, "bars": bars}
                    if cached:
                        payload["copied_from"] = legacy_1h_path(out_dir, p, args.days)
                        payload["cached_at"] = datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
                    atomic_write_json(path, payload)
                    if lab == "1h" and not cached:
                        # 原有 backtest.py/panel_backtest.py 仍读 cache，两个入口都更新。
                        atomic_write_json(legacy_1h_path(out_dir, p, args.days),
                                          {"symbol": sym_tag, "days": args.days, "source": source,
                                           "fetched_at": fetched_at, "bars": bars})
                    counts["success"] += 1
                    span = f"{bars[0]['dt']} ~ {bars[-1]['dt']}"
                    action = "复用" if cached else "更新"
                    print(f"[{i + 1}/{len(presets)}] {p['name']} {lab}: {action} {len(bars)}根 ({source}) {span}")
                except Exception as e:
                    counts["failed"] += 1
                    notes.append(f"{p['name']} {lab} 更新失败：{type(e).__name__}")
            time.sleep(args.sleep)
    finally:
        if api is not None:
            try:
                api.close()
            except Exception as e:
                close_failed = True
                notes.append(f"行情连接关闭失败：{type(e).__name__}")

    if notes:
        print("\n-- 异常记录（单数据源失败后可能已回退成功）--")
        for n in notes:
            print(" ", n)
    print(f"\n汇总：成功 {counts['success']}，跳过 {counts['skipped']}，失败 {counts['failed']}。")
    failed = counts["failed"] > 0 or close_failed or api_failed
    print(("更新未全部成功。" if failed else "更新完成。") + "缓存目录：" + os.path.join(out_dir, KLINE_DIR))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
