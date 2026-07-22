# -*- coding: utf-8 -*-
"""
fundamentals.py — 基本面模块：库存 / 基差 / 主力席位持仓（akshare 数据源）。

设计：
- 后台线程 + 每日缓存 state/fundamentals.json：不阻塞行情主循环；
  每天 config.FUND_REFRESH_HOUR:FUND_REFRESH_MINUTE 后或首次启动时刷新；
  任一接口失败/超时 → 保留该品种旧缓存并标注日期（stale=True）。
- MOCK_MODE 下生成合成演示数据（方向各异，便于演示 UI/契约）。
- 接口验证结论（akshare 1.18.64，2026-07 实测）：
    SHFE: get_shfe_rank_table(date, vars_list=[品种代码])  ✓
    CZCE: get_rank_table_czce(date)（全品种一次返回，席位名带"（代客）"，数字带千分位逗号）✓
    DCE : get_dce_rank_table 挂起超时 / futures_dce_position_rank 抛 BadZipFile → 不可用，降级"暂无数据"
    库存: futures_inventory_em(symbol=中文名) ✓（部分品种无数据时如实标注）
    基差: futures_spot_price(date) ✓（symbol 列为品种代码）
"""
import json
import math
import os
import random
import threading
import time
from concurrent import futures as cf
from datetime import datetime, timedelta

import config
import symbols as symbols_mod
import user_settings

_lock = threading.Lock()
_thread = None
_state = {"updated_at": None, "data_date": None, "stale": False,
          "source": None, "symbols": {}}
_refreshing = threading.Event()


# ---------------- 工具 ----------------

def _product_of(preset_symbol):
    """SHFE.ag2608 → ag；CZCE.SA609 → SA（与 backtest 同口径）。"""
    return "".join(ch for ch in preset_symbol.split(".")[1] if not ch.isdigit())


def _contract_of(preset_symbol):
    """SHFE.ag2608 → ag2608（排名表 dict 键用小写合约）。"""
    return preset_symbol.split(".")[1].lower()


def _num(x):
    """排名表数字兼容：CZCE 为带逗号字符串（'18,707'），SHFE 为数值。失败返回 None。"""
    if x is None:
        return None
    try:
        if isinstance(x, str):
            x = x.replace(",", "").strip()
            if not x or x in ("-", "--"):
                return None
        v = float(x)
        return int(v) if v == int(v) else v
    except (TypeError, ValueError):
        return None


def _with_timeout(fn, timeout):
    """子线程执行 fn，超时返回 None（akshare 部分接口会挂起，绝不阻塞主流程）。"""
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=timeout)
    except Exception:  # noqa: BLE001 超时/接口异常都按无数据处理
        return None
    finally:
        ex.shutdown(wait=False)


def _recent_trade_date():
    """最近一个可能有结算数据的交易日（YYYYMMDD）：16 点前用前一日，跳周末。"""
    d = datetime.now()
    if d.hour < 16:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _cache_path():
    d = os.path.join(user_settings.app_dir(), "state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "fundamentals.json")


def _load_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "symbols" in d:
            with _lock:
                _state.update(d)
    except Exception:  # noqa: BLE001 无缓存/损坏 → 空状态起步
        pass


def _save_cache():
    with _lock:
        snap = dict(_state)
    try:
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _cache_path())
    except Exception:  # noqa: BLE001 落盘失败不影响主流程
        pass


# ---------------- 空数据占位 ----------------

def _empty_entry(note="暂无数据"):
    return {"date": None, "stale": False,
            "inventory": None, "basis": None, "brokers": [],
            "note": note, "summary": note}


def _inv_trend(series):
    if len(series) < 2:
        return "—"
    if series[-1] > series[0]:
        return "累库↑偏空"
    if series[-1] < series[0]:
        return "去库↓偏多"
    return "持平"


def _basis_tag(rate):
    if rate is None:
        return ""
    if rate > 1:
        return "现货升水↑"
    if rate < -1:
        return "现货贴水↓"
    return "基本平水"


def _broker_tag(long_chg, short_chg):
    """席位动向标签：看多单/空单增减的净值方向。"""
    lc, sc = long_chg or 0, short_chg or 0
    net = lc - sc
    if lc == 0 and sc == 0:
        return "观望"
    if net > 0:
        return "偏多↑"
    if net < 0:
        return "偏空↓"
    return "中性"


def _entry_summary(entry):
    parts = []
    inv = entry.get("inventory")
    if inv and inv.get("trend") and inv["trend"] != "—":
        parts.append(inv["trend"])
    b = entry.get("basis")
    if b and b.get("tag"):
        parts.append(b["tag"])
    br = [x for x in entry.get("brokers", []) if x.get("tag") not in (None, "观望")]
    if br:
        parts.append(" · ".join(f"{x['name']}{x['tag']}" for x in br[:2]))
    entry["summary"] = " · ".join(parts) if parts else (entry.get("note") or "暂无数据")
    return entry


# ---------------- akshare 拉取（真实模式） ----------------

def _fetch_inventory(cn_name):
    """futures_inventory_em → {latest, delta, series(近5期), trend}；失败 None。"""
    import akshare as ak
    df = _with_timeout(lambda: ak.futures_inventory_em(symbol=cn_name), config.FUND_INV_TIMEOUT)
    if df is None or not hasattr(df, "columns") or "库存" not in df.columns:
        return None
    df = df.dropna(subset=["库存"])
    nonzero = df[df["库存"] != 0]
    if not nonzero.empty:
        df = nonzero
    df = df.tail(6)
    if df.empty:
        return None
    vals = [float(v) for v in df["库存"].tolist()]
    last, delta = vals[-1], (vals[-1] - vals[-2] if len(vals) >= 2 else 0)
    series = vals[-5:]
    return {"latest": last, "delta": delta, "series": series, "trend": _inv_trend(series)}


def _fetch_basis_all():
    """futures_spot_price → {品种代码: {spot, future, basis, basis_rate, tag, date}}；失败 {}。"""
    import akshare as ak
    date = _recent_trade_date()
    df = _with_timeout(lambda: ak.futures_spot_price(date), config.FUND_HTTP_TIMEOUT)
    for back in range(1, 6):                    # 当日无数据往前找 5 天
        if df is not None and hasattr(df, "columns") and not df.empty:
            break
        d = (datetime.strptime(date, "%Y%m%d") - timedelta(days=back)).strftime("%Y%m%d")
        df = _with_timeout(lambda: ak.futures_spot_price(d), config.FUND_HTTP_TIMEOUT)
        if df is not None and hasattr(df, "columns") and not df.empty:
            date = d
            break
    if df is None or not hasattr(df, "columns") or df.empty:
        return {}
    out = {}
    for _, r in df.iterrows():
        try:
            spot, fut = float(r["spot_price"]), float(r["dominant_contract_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if not spot or math.isnan(spot) or math.isnan(fut):
            continue
        basis = spot - fut                        # 传统口径：基差=现货-期货
        rate = round(basis / spot * 100, 2)
        out[str(r["symbol"]).upper()] = {"spot": spot, "future": fut, "basis": round(basis, 2),
                                         "basis_rate": rate, "tag": _basis_tag(rate),
                                         "date": date}
    return out


def _brokers_from_df(df, strip_suffix=""):
    """从排名表提取关注席位的多/空持仓日变化。
    行格式：每行一个名次，含 long_party_name(+oi+chg) 与 short_party_name(+oi+chg) 两组。"""
    out = []
    for w in config.BROKER_WATCHLIST:
        long_chg = short_chg = None
        for _, r in df.iterrows():
            lp = str(r.get("long_party_name", "")).replace(strip_suffix, "") if strip_suffix \
                else str(r.get("long_party_name", ""))
            sp = str(r.get("short_party_name", "")).replace(strip_suffix, "") if strip_suffix \
                else str(r.get("short_party_name", ""))
            if long_chg is None and any(a in lp for a in w["alias"]):
                long_chg = _num(r.get("long_open_interest_chg"))
            if short_chg is None and any(a in sp for a in w["alias"]):
                short_chg = _num(r.get("short_open_interest_chg"))
        if long_chg is None and short_chg is None:
            continue                            # 该席位不在前 20 榜
        out.append({"name": w["name"], "long_chg": long_chg, "short_chg": short_chg,
                    "tag": _broker_tag(long_chg, short_chg)})
    return out


def _fetch_brokers(exch, var, contract):
    """按交易所取关注席位动向。dce 接口当前不可用（挂起/BadZipFile），如实返回 None。"""
    import akshare as ak
    date = _recent_trade_date()
    if exch == "shfe":
        d = _with_timeout(lambda: ak.get_shfe_rank_table(date=date, vars_list=[var]),
                          config.FUND_HTTP_TIMEOUT)
        if not isinstance(d, dict) or not d:
            return None
        df = d.get(contract)
        if df is None:
            df = next(iter(d.values()))
        return _brokers_from_df(df)
    if exch == "czce":
        d = _with_timeout(lambda: ak.get_rank_table_czce(date=date), config.FUND_HTTP_TIMEOUT)
        if not isinstance(d, dict) or not d:
            return None
        frames = [df for df in d.values()
                  if hasattr(df, "columns") and str(df["variety"].iloc[0]).upper() == var]
        if not frames:
            return None
        return _brokers_from_df(frames[0], strip_suffix="（代客）")
    return None                                 # dce：接口不可用，暂无数据


def _refresh_real():
    """全量刷新（真实模式）。单品种失败只影响该品种维度，其余保留。"""
    with _lock:
        old = dict(_state.get("symbols") or {})
    basis_all = _fetch_basis_all()
    new = {}
    for p in symbols_mod.PRESETS:
        sym = p["symbol"]
        prod = _product_of(sym)
        m = config.FUND_MAP.get(prod)
        prev = old.get(sym) or _empty_entry()
        if not m:
            new[sym] = _empty_entry()
            continue
        entry = {"date": None, "stale": False, "inventory": prev.get("inventory"),
                 "basis": prev.get("basis"), "brokers": prev.get("brokers") or [],
                 "note": None}
        got_any = False
        inv = _fetch_inventory(m["inv"]) if m.get("inv") else None
        if inv:
            entry["inventory"] = inv
            entry["date"] = inv.get("date") or _recent_trade_date()
            got_any = True
        b = basis_all.get(m["basis"]) if m.get("basis") else None
        if b:
            entry["basis"] = b
            entry["date"] = b.get("date")
            got_any = True
        br = _fetch_brokers(m["exch"], m["var"], _contract_of(sym)) if m.get("exch") else None
        if br:
            entry["brokers"] = br
            got_any = True
        elif m.get("exch") == "dce":
            entry["note"] = "席位：大商所接口暂不可用"
        if not got_any:
            entry["stale"] = bool(prev.get("inventory") or prev.get("basis") or prev.get("brokers"))
            entry["note"] = entry["note"] or ("刷新失败，保留旧数据" if entry["stale"] else "暂无数据")
        new[sym] = _entry_summary(entry)
    with _lock:
        _state.update({"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "data_date": _recent_trade_date(),
                       "stale": False, "source": "akshare", "symbols": new})
    _save_cache()


# ---------------- MOCK 合成数据 ----------------

def _refresh_mock():
    """合成演示数据：至少 3 品种方向各异（ag 偏多 / rb 偏空 / jm 震荡），其余随机。"""
    bias = {"SHFE.ag2608": 1, "SHFE.rb2610": -1, "DCE.jm2609": 0}
    new = {}
    today = _recent_trade_date()
    for i, p in enumerate(symbols_mod.PRESETS):
        sym = p["symbol"]
        rnd = random.Random(hash((sym, today)) & 0xFFFF)
        side = bias.get(sym, rnd.choice([-1, 0, 1]))
        inv_base = rnd.randint(20000, 900000)
        series = []
        v = inv_base
        for _ in range(5):
            v = max(1000, v + side * rnd.randint(200, 2000) + rnd.randint(-300, 300))
            series.append(v)
        delta = series[-1] - series[-2]
        inv = {"latest": series[-1], "delta": delta, "series": series,
               "trend": _inv_trend(series)}
        spot = round(rnd.uniform(3000, 9000), 1)
        rate = round(side * rnd.uniform(0.5, 3.5), 2)
        fut = round(spot * (1 - rate / 100), 1)
        basis = {"spot": spot, "future": fut, "basis": round(spot - fut, 1),
                 "basis_rate": rate, "tag": _basis_tag(rate), "date": today}
        brokers = []
        for w in config.BROKER_WATCHLIST:
            lc = int(side * rnd.randint(0, 1500) + rnd.randint(-200, 200))
            sc = int(-side * rnd.randint(0, 1500) + rnd.randint(-200, 200))
            brokers.append({"name": w["name"], "long_chg": lc, "short_chg": sc,
                            "tag": _broker_tag(lc, sc)})
        new[sym] = _entry_summary({"date": today, "stale": False, "inventory": inv,
                                   "basis": basis, "brokers": brokers, "note": None})
    with _lock:
        _state.update({"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "data_date": today, "stale": False,
                       "source": "mock", "symbols": new})
    _save_cache()


# ---------------- 后台线程 ----------------

def _due(now):
    """今天是否已到刷新时点且当前数据还是旧的。"""
    hm = config.FUND_REFRESH_HOUR * 60 + config.FUND_REFRESH_MINUTE
    if now.hour * 60 + now.minute < hm:
        return False
    return (_state.get("updated_at") or "")[:10] < now.strftime("%Y-%m-%d")


def _loop():
    _load_cache()
    while True:
        try:
            if not _state.get("symbols") or _due(datetime.now()):
                if config.MOCK_MODE:
                    _refresh_mock()
                else:
                    _refresh_real()
        except Exception:  # noqa: BLE001 刷新失败保留旧缓存，下轮再试
            with _lock:
                _state["stale"] = True
        # 真数据拉取耗时较长（12 品种×库存+排名），完成后歇 60s 再看是否到期
        _refreshing.clear()
        time.sleep(60)


def start():
    """启动基本面后台线程（幂等）。"""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, daemon=True, name="fundamentals")
        _thread.start()


def refresh_now():
    """手动触发一次刷新（后台异步，立即返回）。"""
    def _once():
        try:
            _refresh_mock() if config.MOCK_MODE else _refresh_real()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_once, daemon=True).start()


# ---------------- 对外查询 ----------------

def get(symbol):
    """某品种基本面条目（含 inventory/basis/brokers/summary）。无数据给占位，不阻塞。"""
    with _lock:
        e = (_state.get("symbols") or {}).get(symbol)
    return e or _empty_entry()


def state_block(symbol):
    """signals_state.json 每品种 fundamentals 摘要块（openclaw 契约）。"""
    e = get(symbol)
    inv = e.get("inventory") or {}
    b = e.get("basis") or {}
    br = [x for x in e.get("brokers", []) if x.get("tag") not in (None, "观望")]
    return {
        "inventory_trend": inv.get("trend"),
        "basis_rate": b.get("basis_rate"),
        "broker_flow": "；".join(f"{x['name']}{x['tag']}" for x in br) if br else None,
        "summary": e.get("summary"),
        "date": e.get("date") or _state.get("data_date"),
        "stale": bool(e.get("stale") or _state.get("stale")),
    }


def meta():
    """模块级元信息：最近刷新时间/数据日期/来源/是否旧缓存。"""
    with _lock:
        return {"updated_at": _state.get("updated_at"), "data_date": _state.get("data_date"),
                "source": _state.get("source"), "stale": bool(_state.get("stale"))}
