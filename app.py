"""
白银三频共振面板 - 后端
- MOCK_MODE=True：生成随机行情，纯本地预览面板，无需网络/账号
- MOCK_MODE=False：用 TQSDK 拉真实行情(在独立线程运行其事件循环)

启动： python app.py  然后浏览器打开 http://127.0.0.1:8848
"""

import threading
import time
import random
import math
import os
import sys
import webbrowser
from flask import Flask, jsonify, render_template, request, redirect, url_for

import config
import resonance
import user_settings


def _resource_dir():
    """PyInstaller 打包后模板在 sys._MEIPASS，源码运行时用脚本目录。"""
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


app = Flask(__name__, template_folder=os.path.join(_resource_dir(), "templates"))

# 返回给前端画图的最大K线根数
CHART_BARS = 60
# 画图用的均线周期
CHART_MAS = (5, 20, 60)


def _attach_mas(full_bars):
    """
    用完整序列(full_bars)算 MA5/20/60 + VWAP + RSI + 摆动高低点结构，
    把这些值附到每根K线上，再返回最后 CHART_BARS 根。这样可见区间内
    三条均线/VWAP 都是完整的，不会因为切片后数据不足导致画不出。

    附加的领先型指标（走在 MA 之前）：
    - vwap : 成交量加权均价，日内资金真实成本线，比 MA 更贴合多空分界。
    - rsi  : 14周期相对强弱，用于背离预警（价格新高但 RSI 未新高＝顶背离）。
    - swing: "H"=摆动高点 / "L"=摆动低点，用于画高低点结构、识别递减高点。
    """
    closes = [b["close"] for b in full_bars]
    highs = [b["high"] for b in full_bars]
    lows = [b["low"] for b in full_bars]
    vols = [b.get("volume", 0) or 0 for b in full_bars]

    # ---- MA ----
    for i, b in enumerate(full_bars):
        for p in CHART_MAS:
            if i + 1 >= p:
                b[f"ma{p}"] = round(sum(closes[i - p + 1:i + 1]) / p, 1)
            else:
                b[f"ma{p}"] = None

    # ---- VWAP（滚动累计：sum(典型价×量)/sum(量)）----
    cum_pv = 0.0
    cum_v = 0.0
    for i, b in enumerate(full_bars):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        v = vols[i] if vols[i] > 0 else 1  # 量为0时给微量，避免 VWAP 断裂
        cum_pv += typical * v
        cum_v += v
        b["vwap"] = round(cum_pv / cum_v, 1) if cum_v else None

    # ---- RSI(14)：Wilder 平滑 ----
    period = 14
    if len(closes) > period:
        gains, losses = [], []
        for i in range(1, len(closes)):
            ch = closes[i] - closes[i - 1]
            gains.append(max(ch, 0.0))
            losses.append(max(-ch, 0.0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rsis = [None] * (period + 1)  # 前 period+1 根无值（含第0根）
        for i in range(period, len(gains)):
            if i > period:
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - 100.0 / (1.0 + rs)
            rsis.append(round(rsi, 1))
        # 对齐长度到 bars
        while len(rsis) < len(full_bars):
            rsis.append(None)
        for i, b in enumerate(full_bars):
            b["rsi"] = rsis[i] if i < len(rsis) else None
    else:
        for b in full_bars:
            b["rsi"] = None

    # ---- 摆动高低点（左右各 win 根做分形）----
    win = 2
    for b in full_bars:
        b["swing"] = None
    n = len(full_bars)
    for i in range(win, n - win):
        seg_h = highs[i - win:i + win + 1]
        seg_l = lows[i - win:i + win + 1]
        if highs[i] == max(seg_h):
            full_bars[i]["swing"] = "H"
        elif lows[i] == min(seg_l):
            full_bars[i]["swing"] = "L"

    return full_bars[-CHART_BARS:]


def _detect_signals(full_bars):
    """
    动能/背离信号（领先于 MA 的预警）。基于完整序列判断，返回：
    {
      momentum_fade: bool,   # 递减高点（lower highs）→ 上涨动能衰竭
      momentum_rise: bool,   # 抬高低点（higher lows）→ 下跌动能衰竭（可能转多）
      vol_diverge:   bool,   # 价创新高但量能不配合 → 量价背离
      oi_diverge:    bool,   # 价涨但持仓不增/减 → 空头回补假涨（无新多进场）
      rsi_diverge:   bool,   # 价创新高但 RSI 未新高 → 顶背离
      text:          str,    # 汇总人话说明
      level:         "none"/"warn"/"alert"
    }
    只做客观信号提示，不替用户决策。
    """
    out = {"momentum_fade": False, "momentum_rise": False,
           "vol_diverge": False, "oi_diverge": False, "rsi_diverge": False,
           "text": "", "level": "none"}
    if not full_bars or len(full_bars) < 6:
        out["text"] = "数据不足，暂不评估动能。"
        return out

    closes = [b["close"] for b in full_bars]
    highs = [b["high"] for b in full_bars]
    lows = [b["low"] for b in full_bars]
    vols = [b.get("volume", 0) or 0 for b in full_bars]
    ois = [b.get("oi", 0) or 0 for b in full_bars]

    # 最近的两个摆动高点 / 低点
    sw_highs = [(i, highs[i]) for i, b in enumerate(full_bars) if b.get("swing") == "H"]
    sw_lows = [(i, lows[i]) for i, b in enumerate(full_bars) if b.get("swing") == "L"]

    # 递减高点：最近两个摆动高点后一个更低
    if len(sw_highs) >= 2:
        (_, h_prev), (_, h_last) = sw_highs[-2], sw_highs[-1]
        if h_last < h_prev:
            out["momentum_fade"] = True
    # 抬高低点
    if len(sw_lows) >= 2:
        (_, l_prev), (_, l_last) = sw_lows[-2], sw_lows[-1]
        if l_last > l_prev:
            out["momentum_rise"] = True

    # 量价背离：近段收盘创新高，但成交量未同步创新高
    look = min(20, len(full_bars))
    seg_close = closes[-look:]
    seg_vol = vols[-look:]
    if len(seg_close) >= 5:
        price_new_high = seg_close[-1] >= max(seg_close)
        # 近3根均量 vs 之前均量
        recent_v = sum(seg_vol[-3:]) / 3.0
        earlier_v = (sum(seg_vol[:-3]) / max(1, len(seg_vol) - 3))
        if price_new_high and recent_v < earlier_v:
            out["vol_diverge"] = True

    # 持仓背离：近3根价格上涨但持仓变化(oi)之和 ≤ 0（无新多进场）
    if len(closes) >= 4:
        price_up = closes[-1] > closes[-4]
        oi_sum = sum(ois[-3:])
        if price_up and oi_sum <= 0:
            out["oi_diverge"] = True

    # RSI 顶背离：价格新高但 RSI 未创新高
    rsis = [b.get("rsi") for b in full_bars if b.get("rsi") is not None]
    if len(rsis) >= 5 and len(seg_close) >= 5:
        if seg_close[-1] >= max(seg_close) and rsis[-1] < max(rsis[-look:] if len(rsis) >= look else rsis):
            out["rsi_diverge"] = True

    # 汇总
    msgs = []
    if out["momentum_fade"]:
        msgs.append("📉 高点递减（涨势力量变弱）")
    if out["vol_diverge"]:
        msgs.append("量价背离（价新高但量不跟）")
    if out["oi_diverge"]:
        msgs.append("持仓背离（价涨但没新多进场，疑空头回补假涨）")
    if out["rsi_diverge"]:
        msgs.append("RSI顶背离")
    if out["momentum_rise"] and not msgs:
        msgs.append("📈 低点抬高（跌势力量变弱）")

    hit = sum([out["momentum_fade"], out["vol_diverge"], out["oi_diverge"], out["rsi_diverge"]])
    if hit >= 2:
        out["level"] = "alert"
        out["text"] = "⚠️ 动能衰竭：" + "、".join(msgs) + "。持仓者检查是否离场，别等回本。"
    elif hit == 1:
        out["level"] = "warn"
        out["text"] = "留意：" + "、".join(msgs) + "。单一信号，结合结构再判断。"
    elif out["momentum_rise"]:
        out["level"] = "warn"
        out["text"] = "、".join(msgs) + "。空头动能减弱，观察是否止跌。"
    else:
        out["text"] = "动能正常，无明显衰竭/背离信号。"
    return out


def _find_sr(full_bars, direction=None):
    """
    自动找支撑/压力位（基于摆动高低点 pivot/分形）。
    - 摆动高点(左右各 window 根都比它低) → 潜在压力
    - 摆动低点(左右各 window 根都比它高) → 潜在支撑
    临近的点聚合成一条线，并统计被触碰次数(触碰越多越有效)。
    返回 {"support": [{price, touches}], "resistance": [...]}。
    """
    window = 3
    n = len(full_bars)
    highs = [b["high"] for b in full_bars]
    lows = [b["low"] for b in full_bars]
    piv_hi, piv_lo = [], []
    for i in range(window, n - window):
        seg_h = highs[i - window:i + window + 1]
        seg_l = lows[i - window:i + window + 1]
        if highs[i] == max(seg_h):
            piv_hi.append(highs[i])
        if lows[i] == min(seg_l):
            piv_lo.append(lows[i])

    # 价格容差：用近段振幅的百分比聚合相近的点
    if not full_bars:
        return {"support": [], "resistance": []}
    ref = full_bars[-1]["close"] or 1
    tol = ref * 0.0015  # 0.15%

    def cluster(points):
        points = sorted(points)
        groups = []
        for p in points:
            if groups and abs(p - groups[-1]["sum"] / groups[-1]["n"]) <= tol:
                groups[-1]["sum"] += p
                groups[-1]["n"] += 1
            else:
                groups.append({"sum": p, "n": 1})
        out = [{"price": round(g["sum"] / g["n"], 1), "touches": g["n"]} for g in groups]
        # 触碰次数多的优先，最多取 3 条
        out.sort(key=lambda x: x["touches"], reverse=True)
        return out[:3]

    price = full_bars[-1]["close"]
    res = [r for r in cluster(piv_hi) if r["price"] >= price]   # 压力在现价上方
    sup = [s for s in cluster(piv_lo) if s["price"] <= price]   # 支撑在现价下方
    return {"support": sup, "resistance": res}


def _nearest_levels(price, sr):
    """返回现价上方最近压力、下方最近支撑（含跳数距离）。"""
    tick = config.PRICE_TICK or 1.0
    res_above = sorted([r for r in sr.get("resistance", []) if r["price"] >= price],
                       key=lambda x: x["price"])
    sup_below = sorted([s for s in sr.get("support", []) if s["price"] <= price],
                       key=lambda x: -x["price"])
    near_res = res_above[0] if res_above else None
    near_sup = sup_below[0] if sup_below else None
    res_ticks = round((near_res["price"] - price) / tick) if near_res else None
    sup_ticks = round((price - near_sup["price"]) / tick) if near_sup else None
    return near_res, res_ticks, near_sup, sup_ticks


def _proximity_warning(price, sr):
    """
    压力/支撑贴近警告。现价距最近压力(或支撑) ≤ PROXIMITY_TICKS 跳 → 警告。
    返回 None 或 {side, level, ticks, text}。
    """
    if price is None or not sr:
        return None
    near_res, res_ticks, near_sup, sup_ticks = _nearest_levels(price, sr)
    lim = config.PROXIMITY_TICKS
    # 优先报更近的一侧
    cand = []
    if near_res and res_ticks is not None and res_ticks <= lim:
        cand.append(("resistance", near_res["price"], res_ticks))
    if near_sup and sup_ticks is not None and sup_ticks <= lim:
        cand.append(("support", near_sup["price"], sup_ticks))
    if not cand:
        return None
    cand.sort(key=lambda x: x[2])
    side, level, ticks = cand[0]
    if side == "resistance":
        text = f"🚧 逼近压力 {level}（仅差 {ticks} 跳）— 做多风险区，勿追突破！"
    else:
        text = f"🚧 逼近支撑 {level}（仅差 {ticks} 跳）— 做空风险区，勿追破位！"
    return {"side": side, "level": level, "ticks": ticks, "text": text}


def _entry_check():
    """
    "我要开仓" 前的盘面自检。返回结论 + 逐条检查。
    只做客观技术条件核对，不替用户下单。
    """
    st = get_state()
    res = st.get("result")
    if not res:
        return {"verdict": "no-data", "title": "无数据", "checks": [],
                "summary": "未取到行情，无法检查。"}

    direction = res.get("direction", "none")
    resonance = res.get("resonance", False)
    price = res.get("price")
    legs = res.get("legs", {})
    sr = (res.get("sr") or {}).get("trigger") or (res.get("sr") or {}).get("trend") or {}

    checks = []

    # 1. 方向是否明确
    dir_txt = {"long": "偏多", "short": "偏空", "none": "方向不明"}[direction]
    checks.append({"ok": direction != "none", "name": "大周期方向",
                   "detail": f"15分定方向：{dir_txt}"})

    # 2. 三频共振
    checks.append({"ok": bool(resonance), "name": "三频共振",
                   "detail": "三频对齐" if resonance else "未共振（15/3/30 未全部同向就位）"})

    # 3. 三腿明细
    for key, label in (("trend", "15分"), ("filter", "3分"), ("trigger", "30秒")):
        leg = legs.get(key, {})
        checks.append({"ok": bool(leg.get("ok")), "name": f"{label}·{leg.get('role','')}",
                       "detail": leg.get("detail", "-")})

    # 4. 压力/支撑贴近（追单风险）
    warn = _proximity_warning(price, sr)
    checks.append({"ok": warn is None, "name": "位置安全（远离压力/支撑）",
                   "detail": warn["text"] if warn else "现价未贴近关键位，位置较安全"})

    # 5. 掩体 / 盈亏比预估
    near_res, res_ticks, near_sup, sup_ticks = _nearest_levels(price, sr) if (price and sr) else (None, None, None, None)
    stop_ticks = config.DEFAULT_STOP_TICKS
    rr_txt, rr_ok = "无法估算（缺支撑压力位）", False
    if direction == "long" and near_res and res_ticks is not None:
        rr = res_ticks / stop_ticks if stop_ticks else 0
        rr_ok = rr >= config.MIN_RR
        rr_txt = f"上方掩体空间 {res_ticks} 跳 / 止损 {stop_ticks} 跳 ≈ 盈亏比 {rr:.1f}"
    elif direction == "short" and near_sup and sup_ticks is not None:
        rr = sup_ticks / stop_ticks if stop_ticks else 0
        rr_ok = rr >= config.MIN_RR
        rr_txt = f"下方掩体空间 {sup_ticks} 跳 / 止损 {stop_ticks} 跳 ≈ 盈亏比 {rr:.1f}"
    checks.append({"ok": rr_ok, "name": f"掩体/盈亏比 (≥{config.MIN_RR})", "detail": rr_txt})

    # 结论：硬条件 = 方向明确 + 共振 + 位置安全 + 盈亏比达标
    hard_ok = (direction != "none") and resonance and (warn is None) and rr_ok
    fail = [c["name"] for c in checks if not c["ok"]]
    if hard_ok:
        verdict, title = "go", "✅ 技术条件允许观察进场"
        summary = f"方向{dir_txt}、三频共振、位置安全、盈亏比达标。仍需你手动确认结构止损位。"
    elif warn is not None:
        verdict, title = "stop", "🚧 不建议：正贴近关键位（追单陷阱）"
        summary = warn["text"] + " 等回踩确认，别追当根。"
    else:
        verdict, title = "wait", "⛔ 条件不足，建议观望"
        summary = "未通过：" + "、".join(fail) + "。任一硬条件不满足都不该进。"

    return {"verdict": verdict, "title": title, "direction": direction,
            "price": price, "checks": checks, "summary": summary}

# 共享状态：数据线程写，Flask 读。用锁保护。
_state_lock = threading.Lock()
_state = {
    "connected": False,
    "mock": config.MOCK_MODE,
    "symbol": config.SYMBOL,
    "result": None,       # resonance.evaluate 的输出
    "updated": 0,         # 最后更新时间戳
    "error": None,
    # 启动阶段：need_config / booting / connecting / loading / ready / error
    "boot_phase": "booting",
    "boot_pct": 0,        # 进度 0-100
    "boot_msg": "正在启动…",
}


def _set_boot(phase, pct, msg):
    _set_state(boot_phase=phase, boot_pct=pct, boot_msg=msg)


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)
        _state["updated"] = time.time()


def get_state():
    with _state_lock:
        return dict(_state)


# ---------------- MOCK 数据 ----------------
def _mock_series(base, n, drift, vol):
    """生成一段随机游走 OHLC 序列（含成交量/持仓量变化）。"""
    out = []
    price = base
    oi = 200000
    for _ in range(n):
        o = price
        price += drift + random.uniform(-vol, vol)
        c = price
        h = max(o, c) + random.uniform(0, vol)
        low = min(o, c) - random.uniform(0, vol)
        volume = int(abs(c - o) * 300 + random.uniform(200, 1500))
        oi_delta = int((c - o) * 200 + random.uniform(-400, 400))
        oi += oi_delta
        out.append({
            "open": round(o, 1), "high": round(h, 1),
            "low": round(low, 1), "close": round(c, 1),
            "volume": volume, "oi": oi_delta,
        })
    return out


def _mock_loop():
    """随机模拟三个周期，方向会缓慢漂移，用来观察面板各种状态。"""
    _set_boot("loading", 60, "生成模拟数据…")
    _set_state(connected=True, error=None)
    phase = 0.0
    first = True
    while True:
        # 用正弦让方向周期性在多/空/震荡之间切换，便于看灯变化
        phase += 0.15
        drift = math.sin(phase) * 0.6
        base = 7200
        kl_trend = _mock_series(base, config.KLINE_LENGTH, drift * 0.4, 3)
        kl_filter = _mock_series(kl_trend[-1]["close"], config.KLINE_LENGTH, drift * 0.5, 2)
        kl_trigger = _mock_series(kl_filter[-1]["close"], config.KLINE_LENGTH, drift * 0.6, 1)
        result = resonance.evaluate(kl_trend, kl_filter, kl_trigger)
        result["klines"] = {
            "trend": _attach_mas(kl_trend),
            "filter": _attach_mas(kl_filter),
            "trigger": _attach_mas(kl_trigger),
        }
        # 领先型动能/背离信号（各周期，供面板信号灯用）
        result["signals"] = {
            "trend": _detect_signals(kl_trend),
            "filter": _detect_signals(kl_filter),
            "trigger": _detect_signals(kl_trigger),
        }
        # 支撑/压力：用当前显示周期算(默认15分)，跟随各周期
        result["sr"] = {
            "trend": _find_sr(kl_trend),
            "filter": _find_sr(kl_filter),
            "trigger": _find_sr(kl_trigger),
        }
        # 压力/支撑贴近警告（用最短周期30秒的S/R + 现价）
        result["proximity"] = _proximity_warning(result.get("price"), result["sr"]["trigger"])
        _set_state(result=result)
        if first:
            _set_boot("ready", 100, "就绪")
            first = False
        time.sleep(1)


# ---------------- TQSDK 真实数据 ----------------
def _tqsdk_loop():
    try:
        from tqsdk import TqApi, TqAuth
    except ImportError:
        _set_boot("error", 0, "未安装 tqsdk")
        _set_state(connected=False, error="未安装 tqsdk，请先 pip install -r requirements.txt")
        return

    try:
        _set_boot("connecting", 25, "正在连接天勤行情…")
        auth = None
        if config.TQ_USER:
            auth = TqAuth(config.TQ_USER, config.TQ_PASSWORD)
        api = TqApi(auth=auth) if auth else TqApi()
    except Exception as e:  # noqa: BLE001
        _set_boot("error", 0, f"连接天勤失败：{e}")
        _set_state(connected=False, error=f"连接天勤失败: {e}")
        return

    try:
        # 先校验合约是否存在/有效（换月过期、拼写错误会在这里立刻暴露，而不是死等K线）
        _set_boot("loading", 45, "正在校验合约…")
        try:
            q = api.get_quote(config.SYMBOL)
            api.wait_update(deadline=time.time() + 15)
            # 不存在的合约：datetime 为空、价格为 NaN
            dt = getattr(q, "datetime", "") or ""
            last = getattr(q, "last_price", float("nan"))
            has_price = isinstance(last, (int, float)) and not math.isnan(last)
            if getattr(q, "expired", False):
                raise ValueError("合约已到期/换月")
            if not dt and not has_price:
                raise ValueError("查无此合约（无行情数据）")
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"合约 {config.SYMBOL} 无法识别或已过期。"
                f"请在配置页改成当前主力合约（如 SHFE.ag2510），或核对代码格式。原始信息：{e}"
            )

        _set_boot("loading", 60, "正在拉取三周期K线…")
        kt = api.get_kline_serial(config.SYMBOL, config.PERIOD_TREND, config.KLINE_LENGTH)
        kf = api.get_kline_serial(config.SYMBOL, config.PERIOD_FILTER, config.KLINE_LENGTH)
        kg = api.get_kline_serial(config.SYMBOL, config.PERIOD_TRIGGER, config.KLINE_LENGTH)
        _set_state(connected=True, error=None)

        def to_list(df):
            # 过滤未生成的NaN行，返回 OHLC + 成交量 + 持仓量变化(CCL)
            rows = []
            opens = df["open"].tolist()
            highs = df["high"].tolist()
            lows = df["low"].tolist()
            closes = df["close"].tolist()
            volumes = df["volume"].tolist() if "volume" in df else [0] * len(closes)
            open_ois = df["open_oi"].tolist() if "open_oi" in df else [0] * len(closes)
            close_ois = df["close_oi"].tolist() if "close_oi" in df else [0] * len(closes)
            for o, h, low, c, v, ooi, coi in zip(opens, highs, lows, closes, volumes, open_ois, close_ois):
                if math.isnan(c):
                    continue
                vol = 0 if (isinstance(v, float) and math.isnan(v)) else int(v)
                oi_delta = 0
                if not (isinstance(coi, float) and math.isnan(coi)) and not (isinstance(ooi, float) and math.isnan(ooi)):
                    oi_delta = int(coi - ooi)
                rows.append({"open": float(o), "high": float(h),
                             "low": float(low), "close": float(c),
                             "volume": vol, "oi": oi_delta})
            return rows

        first = True
        wait_start = time.time()
        while True:
            # 关键：必须带 deadline，否则非交易时段/无推送时 wait_update() 会永久阻塞，
            # 下面的 30 秒看门狗永远执行不到，界面就会一直卡在"正在拉取三周期K线…"。
            # deadline 到点 tqsdk 会抛 TimeoutError，这里吞掉，让流程走到看门狗判断。
            try:
                api.wait_update(deadline=time.time() + 5)
            except TimeoutError:
                pass
            kt_list, kf_list, kg_list = to_list(kt), to_list(kf), to_list(kg)
            # 首次要等三周期都有足够数据
            if first and (len(kt_list) < config.MA_SLOW or len(kg_list) < config.MA_FAST):
                # 看门狗：30秒还填不满K线 → 报错，避免永久卡在"拉取三周期K线"
                if time.time() - wait_start > 30:
                    raise TimeoutError(
                        f"30秒内未取到足够K线（15分{len(kt_list)}根/30秒{len(kg_list)}根）。"
                        f"可能原因：①合约 {config.SYMBOL} 非当前主力/已换月 "
                        f"②同一天勤账号在别处登录（同账号不能多设备同时在线）"
                        f"③当前非交易时段且无历史缓存。"
                        f"请在配置页核对合约、确认账号未在别处使用后重试。"
                    )
                continue
            result = resonance.evaluate(kt_list, kf_list, kg_list)
            result["sr"] = {
                "trend": _find_sr(kt_list),
                "filter": _find_sr(kf_list),
                "trigger": _find_sr(kg_list),
            }
            result["proximity"] = _proximity_warning(result.get("price"), result["sr"]["trigger"])
            result["klines"] = {
                "trend": _attach_mas(kt_list),
                "filter": _attach_mas(kf_list),
                "trigger": _attach_mas(kg_list),
            }
            # 领先型动能/背离信号（须在 _attach_mas 之后，依赖其附加的 swing/rsi 字段）
            result["signals"] = {
                "trend": _detect_signals(kt_list),
                "filter": _detect_signals(kf_list),
                "trigger": _detect_signals(kg_list),
            }
            _set_state(result=result)
            if first:
                _set_boot("ready", 100, "就绪")
                first = False
    except Exception as e:  # noqa: BLE001
        _set_boot("error", 0, f"行情线程异常：{e}")
        _set_state(connected=False, error=f"行情线程异常: {e}")
    finally:
        try:
            api.close()
        except Exception:  # noqa: BLE001
            pass


_data_thread = None
_thread_lock = threading.Lock()


def apply_user_settings():
    """把 user_config.json 覆盖到 config 运行时值。"""
    s = user_settings.load_settings()
    if s.get("tq_user"):
        config.TQ_USER = s["tq_user"]
    if s.get("tq_password"):
        config.TQ_PASSWORD = s["tq_password"]
    if s.get("symbol"):
        config.SYMBOL = s["symbol"]
    config.MOCK_MODE = bool(s.get("mock_mode", False))


def start_data_thread():
    """启动行情线程（只启动一次）。"""
    global _data_thread
    with _thread_lock:
        if _data_thread and _data_thread.is_alive():
            return
        target = _mock_loop if config.MOCK_MODE else _tqsdk_loop
        _data_thread = threading.Thread(target=target, daemon=True)
        _data_thread.start()


# ---------------- 路由 ----------------
@app.route("/")
def index():
    # 未配置 → 去配置页
    if not user_settings.is_configured():
        return redirect(url_for("setup"))
    return render_template("index.html",
                           symbol=config.SYMBOL,
                           poll_ms=config.POLL_INTERVAL_MS)


@app.route("/setup")
def setup():
    s = user_settings.load_settings()
    return render_template("setup.html",
                           tq_user=s.get("tq_user", ""),
                           symbol=s.get("symbol", config.SYMBOL))


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        s = user_settings.load_settings()
        s.pop("tq_password", None)  # 不回传密码
        return jsonify(s)
    # POST：保存配置 → 应用 → 启动行情线程
    data = request.get_json(force=True, silent=True) or {}
    user_settings.save_settings({
        "tq_user": (data.get("tq_user") or "").strip(),
        "tq_password": (data.get("tq_password") or "").strip(),
        "symbol": (data.get("symbol") or "").strip(),
        "mock_mode": bool(data.get("mock_mode", False)),
    })
    apply_user_settings()
    _set_boot("booting", 10, "配置已保存，正在启动…")
    start_data_thread()
    return jsonify({"ok": True})


@app.route("/api/state")
def api_state():
    return jsonify(get_state())


@app.route("/api/entry_check")
def api_entry_check():
    return jsonify(_entry_check())


def _open_browser_later():
    time.sleep(1.0)
    try:
        webbrowser.open(f"http://{config.HOST}:{config.PORT}")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    apply_user_settings()
    # 已配置 → 直接起行情线程；未配置 → 等用户在配置页提交后再起
    if user_settings.is_configured():
        start_data_thread()
    else:
        _set_boot("need_config", 0, "首次使用，请先填写天勤账号")
    threading.Thread(target=_open_browser_later, daemon=True).start()
    print(f"面板已启动: http://{config.HOST}:{config.PORT}  (MOCK_MODE={config.MOCK_MODE})")
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)
