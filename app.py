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
import json
import webbrowser
from flask import Flask, jsonify, render_template, request, redirect, url_for

import config
import resonance
import symbols
import double_bottom
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


# ---- 三批次入场决策模型：阶段识别 → 可执行四件套（禁止"永远无法开仓"死路） ----
_STAGE_META = {
    "2b_probe":       {"stage_name": "底部2B·试单",   "tranche": "试单", "idx": 0},
    "retest_confirm": {"stage_name": "突破回踩·确认", "tranche": "确认", "idx": 1},
    "range_addon":    {"stage_name": "震荡顺势·点3",  "tranche": "顺势", "idx": 2},
    "standby":        {"stage_name": "等待中",        "tranche": None,   "idx": -1},
}


def _reversal_recent(kb, kind, n=3):
    """近 n 根内是否出现企稳/反转K线（复用轨道B的锤子/吞没判定）。"""
    for i in range(max(1, len(kb) - n), len(kb)):
        if double_bottom._reversal_candle(kb, i, kind):
            return True
    return False


def _stage_advice(kb, tbs, price):
    """
    三批次入场决策核心。kb=轨道B周期(1h)K线；tbs=轨道B摘要（overview 条目）；price=现价。
    按优先级识别阶段：2B试单 → 回踩确认 → 震荡顺势(点3)；
    无阶段输出 standby + 具体观察指令（等什么、关注哪个价位），永远有可执行输出。
    """
    tick = config.PRICE_TICK or 1.0
    atr = double_bottom._atr(kb) or (price * 0.005 if price else 1.0)
    # 方向：优先双底(做多)，其次双顶(做空镜像)
    kind, pattern = None, None
    pb = double_bottom._find_pattern(kb, "bottom")
    pt = double_bottom._find_pattern(kb, "top")
    if pb["found"]:
        kind, pattern = "bottom", pb
    elif pt["found"]:
        kind, pattern = "top", pt
    direction = {"bottom": "long", "top": "short"}.get(kind, "none")

    def out(**kw):
        base = {"mode": None, "stage": "standby", "stage_name": "等待中",
                "strength": "standby", "direction": direction,
                "tranche": None, "stop": None, "trigger": None, "targets": [],
                "ladder": {"L1": None, "L2": None, "neck": None, "price": price,
                           "stop": None, "target1": None, "target2": None},
                "wait_for": None, "summary": ""}
        base.update(kw)
        return base

    if not pattern:
        return out(wait_for="暂无结构，等双底/双顶左底形成后再评估",
                   summary="轨道B未识别到双底/双顶，耐心等形态出现，不提前预判进场。")

    L1, L2, neck = pattern["L1"]["price"], pattern["L2"]["price"], pattern["neck"]
    sgn = 1 if kind == "bottom" else -1   # 交易方向符号：做多 +1 / 做空 -1
    brk, brk_i, _ = double_bottom._breakout_info(kb, pattern, kind)

    # 目标：优先轨道B计划，缺则丈量目标(颈位±形态高度)/最近强位补齐
    t1 = (tbs or {}).get("target1") or round(neck + (neck - L1) * sgn, 2)
    t2 = (tbs or {}).get("target2")
    if t2 is None and tbs and price:
        pool = tbs["track_b"]["resistance" if kind == "bottom" else "support"]
        ahead = [l for l in pool if (l["price"] - price) * sgn > 0]
        if ahead:
            t2 = min(ahead, key=lambda l: abs(l["price"] - price))["price"]
    ladder = {"L1": L1, "L2": L2, "neck": neck, "price": price,
              "stop": None, "target1": t1, "target2": t2}

    def pkg(stage, stop_price, stop_basis, trig_met, trig_text, note):
        """组装某阶段的可执行四件套 + 推荐强度（strong/normal/reduce）。"""
        stop_ticks = round(abs(price - stop_price) / tick) if price and stop_price else None
        targets = []
        risk = abs(price - stop_price) if (price and stop_price) else 0
        for tp in (t1, t2):
            if tp is None or price is None:
                continue
            targets.append({"price": tp, "rr": round(abs(tp - price) / risk, 1) if risk else None})
        rr1 = targets[0]["rr"] if targets else None
        meta = _STAGE_META[stage]
        fraction = config.TRANCHE_FRACTIONS[meta["idx"]]
        if rr1 is not None and rr1 < config.MIN_RR:
            strength = "reduce"                       # RR 不足：降仓位比例提示而非禁止
            fraction = round(fraction / 2, 2)
            note += f"；RR {rr1}<{config.MIN_RR}，本批仓位建议减半"
        elif trig_met:
            strength = "strong"
        else:
            strength = "normal"
        ladder["stop"] = round(stop_price, 2)
        return out(stage=stage, stage_name=meta["stage_name"], strength=strength,
                   tranche={"name": meta["tranche"], "fraction": fraction, "note": note},
                   stop={"price": round(stop_price, 2), "ticks": stop_ticks, "basis": stop_basis},
                   trigger={"met": bool(trig_met), "text": trig_text},
                   targets=targets, ladder=dict(ladder),
                   summary=f"{meta['stage_name']}批（计划仓位{fraction * 100:.0f}%）：{trig_text}"
                           + (f"；目标 {t1}" if t1 else ""))

    # ---- 阶段识别（按优先级）----
    if not brk:
        # 尚未放量突破颈位
        if price is not None and abs(price - L2) <= config.STAGE_ZONE_ATR * atr:
            trig = _reversal_recent(kb, kind)
            return pkg("2b_probe", L2 - sgn * config.DB_STOP_ATR * atr, "L2下方1.5×ATR",
                       trig,
                       "企稳K线已出现，可按试单批执行" if trig
                       else "等企稳K线：锤子/吞没/收盘收回L2上方",
                       "右底区域试单，止损放L2外侧，破位即走")
        if price is not None and (price - neck) * sgn > 0:
            return out(wait_for=f"价已在颈位 {neck} 上方但未放量确认，等放量突破或回踩颈位 {neck}",
                       summary="价格越过颈位但缺放量确认，不追，等确认批区域。")
        return out(wait_for=f"等回落至 L2 {L2} 区域试单，或等放量突破颈位 {neck}",
                   summary=f"结构在（L1 {L1} / 颈位 {neck}），现价不上不下，等价格到位。")

    # 曾放量突破颈位
    if price is not None and abs(price - neck) <= config.RETEST_ZONE_ATR * atr and (price - L2) * sgn > 0:
        seg = kb[-10:]
        ref = min(b["low"] for b in seg) if kind == "bottom" else max(b["high"] for b in seg)
        stop_price = ref - sgn * 0.5 * atr
        vols = [b.get("volume", 0) or 0 for b in kb]
        shrink = len(vols) >= 10 and sum(vols[-5:]) <= sum(vols[-10:-5])   # 缩量回踩
        trig = shrink and _reversal_recent(kb, kind)
        return pkg("retest_confirm", stop_price, "回踩低点下方0.5×ATR",
                   trig,
                   "缩量企稳已现，可按确认批执行" if trig
                   else "等触发：缩量回踩+企稳信号，禁止挂限价裸接",
                   "突破后首次回踩颈位，缩量企稳才接")

    if price is not None and (price - neck) * sgn > config.RETEST_ZONE_ATR * atr:
        # 已站稳颈位上方：看近 RANGE_LOOKBACK 根是否波动收缩（横盘/回调整理）
        seg = kb[-config.RANGE_LOOKBACK:]
        amp = max(b["high"] for b in seg) - min(b["low"] for b in seg)
        contracted = amp <= config.RANGE_CONTRACT_ATR * atr
        edge = min(b["low"] for b in seg) if kind == "bottom" else max(b["high"] for b in seg)
        return pkg("range_addon", edge - sgn * 0.3 * atr,
                   "整理区下沿" if kind == "bottom" else "整理区上沿",
                   contracted,
                   "波动已收缩，可沿突破方向进" if contracted
                   else "等触发：波动收缩后沿整理区边沿进",
                   "突破确认后的整理区顺势加仓")

    # 跌回/弹回结构内
    if price is not None and (price - L2) * sgn <= 0:
        return out(wait_for=f"已跌回右底 {L2} 之下，结构疑似破坏，等重新企稳或新结构",
                   summary="价格击穿右底，原结构存疑，离场观望等重新组织。")
    return out(wait_for=f"等回踩颈位 {neck} 确认（±0.5×ATR 带）",
               summary="已放量突破，等回踩颈位的确认批机会，不追中间位。")


def _focus_track_b():
    """聚焦品种的轨道B摘要（全局层每轮已算好，直接取用）；未就绪返回 None。"""
    with _overview_lock:
        e = _overview.get(config.SYMBOL)
    if not e or e["track_b"].get("score") is None:
        return None
    return e


def _entry_check():
    """
    "我要开仓" 前的盘面自检：三批次入场决策模型。
    阶段识别（2B试单 / 回踩确认 / 震荡顺势点3 / 观望）→ 永远给出可执行输出：
    有阶段=止损+触发+目标+仓位比例四件套；无阶段=standby + 具体等什么。
    不再有"禁止开仓"档；RR 不足时降为 reduce（仓位减半提示）而非否决。
    """
    st = get_state()
    res = st.get("result")
    combo = _current_combo()
    mode = "scalp" if combo["key"] == "scalp" else "swing"
    mode_label = ("超短模式" if mode == "scalp" else "波段模式") + \
        f"（{combo['name']} {combo['trend_label']}/{combo['filter_label']}/{combo['trigger_label']}）"
    kb = _track_b_bars.get(config.SYMBOL)
    if not kb or len(kb) < 40:
        return {"mode": mode, "mode_label": mode_label, "stage": "standby",
                "stage_name": "等待中", "strength": "standby", "direction": "none",
                "tranche": None, "stop": None, "trigger": None, "targets": [],
                "ladder": {"L1": None, "L2": None, "neck": None, "price": None,
                           "stop": None, "target1": None, "target2": None},
                "wait_for": "轨道B数据收集中（需≥40根1h K线），稍候再试",
                "summary": "1h K线尚未收齐，无法做阶段识别。"}
    price = (res or {}).get("price") or kb[-1]["close"]
    out = _stage_advice(kb, _focus_track_b(), price)
    out["mode"] = mode
    out["mode_label"] = mode_label
    return out

# 共享状态：数据线程写，Flask 读。用锁保护。
_state_lock = threading.Lock()
_state = {
    "connected": False,
    "mock": config.MOCK_MODE,
    "symbol": config.SYMBOL,
    "combo": config.DEFAULT_COMBO,  # 当前共振周期组合 key
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


# ---------------- 周期组合（面板直接切换，无需重启） ----------------
_combo_lock = threading.Lock()
_combo_key = config.DEFAULT_COMBO   # 当前生效的组合 key
_combo_request = None               # 前端请求切换的目标 key（由数据线程消费）


def _current_combo():
    with _combo_lock:
        return config.get_combo(_combo_key)


def _combo_labels(combo):
    """组合 → resonance.evaluate 的三腿文案。"""
    return {"trend": combo["trend_label"], "filter": combo["filter_label"],
            "trigger": combo["trigger_label"]}


def _consume_combo_request():
    """数据线程每轮调用：有切换请求则应用并返回新组合，否则返回 None。"""
    global _combo_key, _combo_request
    with _combo_lock:
        if _combo_request and _combo_request != _combo_key:
            _combo_key = _combo_request
            combo = config.get_combo(_combo_key)
            _combo_request = None
            _set_state(combo=combo["key"])
            return combo
        _combo_request = None
        return None


# ---------------- 多周期K线（按需懒加载，避免免费账号订阅数打满） ----------------
_extra_lock = threading.Lock()
_extra_requests = set()   # 前端查看过的周期(秒)
_extra_data = {}          # 周期(秒) -> {"bars": [...], "full": [...], "updated": 时间戳}


# ---------------- 聚焦品种热切换（无需重启） ----------------
_symbol_lock = threading.Lock()
_symbol_request = None    # 前端请求切换的品种代码（由数据线程消费）


def _consume_symbol_request():
    """数据线程每轮调用：有切换请求则应用并返回新品种代码，否则 None。"""
    global _symbol_request
    with _symbol_lock:
        req = _symbol_request
        _symbol_request = None
    if not req or req == config.SYMBOL:
        return None
    config.SYMBOL = req
    symbols.apply_symbol_params(config)   # 新品种 tick 参数（预设查不到保留默认）
    _set_state(symbol=req)
    with _extra_lock:
        _extra_data.clear()               # 多周期缓存属于旧品种，清掉等前端重拉
    return req


# ---------------- 全局监控层（全品种快照 + openclaw 落盘契约） ----------------
_overview_lock = threading.Lock()
_overview = {}   # 品种代码 -> 快照条目（结构即 signals_state.json 的每品种条目）
_tb_throttle = {}  # 品种 -> {"n": 已跑轮数, "summary": 轨道B摘要缓存}（节流用）
_track_b_bars = {}  # 品种 -> 最近一份 1h 原始K线（三批次阶段识别用）


def _lamp_of(track_a):
    """轨道A灯色：共振多=红 / 共振空=绿 / 其它=灰（涨红跌绿）。"""
    if track_a.get("resonance") and track_a.get("direction") == "long":
        return "red"
    if track_a.get("resonance") and track_a.get("direction") == "short":
        return "green"
    return "gray"


def _track_b_summary(tb):
    """double_bottom.evaluate 完整输出 → 落盘/卡片用摘要（plan 拆成 entry/stop/target1）。"""
    p = tb["pattern"]
    plan = tb["plan"] or {}
    return {
        "score": tb["score"], "direction": tb["direction"],
        "pattern": {"found": p["found"], "type": p["type"],
                    "L1": p["L1"]["price"] if p["L1"] else None,
                    "L2": p["L2"]["price"] if p["L2"] else None,
                    "neck": p["neck"]},
        "support": [{"price": l["price"], "score": l["score"]} for l in tb["levels"]["support"]],
        "resistance": [{"price": l["price"], "score": l["score"]} for l in tb["levels"]["resistance"]],
        "volume_oi": tb["volume_oi"],
        "entry": plan.get("entry"), "stop": plan.get("stop"), "target1": plan.get("target1"),
        "reasons": tb["reasons"],
    }


def _track_b_for(sym, kb):
    """轨道B摘要（按 config.GLOBAL_TRACKB_EVERY 节流重算，K线未收盘时评分不变）。"""
    _track_b_bars[sym] = kb          # 三批次阶段识别要用 1h 原始K线
    ent = _tb_throttle.get(sym) or {"n": 0, "summary": None}
    ent["n"] += 1
    if ent["summary"] is None or ent["n"] % max(1, config.GLOBAL_TRACKB_EVERY) == 0:
        ent["summary"] = _track_b_summary(double_bottom.evaluate(kb))
    _tb_throttle[sym] = ent
    return ent["summary"]


def _collecting_entry(name):
    """数据不足时的占位条目（总览页降级显示"数据收集中"）。"""
    return {
        "name": name, "price": None,
        "track_a": {"direction": "none", "resonance": False},
        "track_b": {"score": None, "direction": "wait",
                    "pattern": {"found": False, "type": None, "L1": None, "L2": None, "neck": None},
                    "support": [], "resistance": [],
                    "volume_oi": {"quadrant": "数据不足", "healthy": False}},
        "lamp": "gray", "entry": None, "stop": None, "target1": None,
        "reasons": ["数据收集中…"],
    }


def _set_overview(sym, entry):
    with _overview_lock:
        _overview[sym] = entry


def _global_pass(bar_getter, focus_track_a=None):
    """
    全局监控层：对全部预设品种跑 轨道A两腿雷达 + 轨道B评分，写入 _overview。
    bar_getter(sym, period) -> K线列表（可能为 []）。
    focus_track_a：聚焦品种的完整三腿结果 (direction, resonance)，有则覆盖两腿雷达（更准）。
    """
    for p in symbols.PRESETS:
        sym, name = p["symbol"], p["name"]
        try:
            kt = bar_getter(sym, config.GLOBAL_TREND_PERIOD)
            kf = bar_getter(sym, config.GLOBAL_FILTER_PERIOD)
            kb = bar_getter(sym, config.GLOBAL_TRACKB_PERIOD)
        except Exception:  # noqa: BLE001 单品种失败跳过，不拖垮全局
            continue
        if len(kt) < config.MA_SLOW + config.TREND_SLOPE_LOOKBACK or len(kb) < 40:
            _set_overview(sym, _collecting_entry(name))
            continue
        # 轨道A两腿雷达（全局层没有30秒触发腿，聚焦品种由主流程覆盖为三腿结果）
        direction = resonance.trend_direction(kt)
        f_ok = resonance.filter_ok(kf, direction) if direction != "none" else False
        track_a = {"direction": direction, "resonance": direction != "none" and f_ok}
        if focus_track_a and sym == config.SYMBOL:
            track_a = dict(focus_track_a)
        tb = _track_b_for(sym, kb)
        price = round(kf[-1]["close"] if kf else kt[-1]["close"], 2)
        _set_overview(sym, {
            "name": name, "price": price,
            "track_a": track_a,
            "track_b": {k: tb[k] for k in
                        ("score", "direction", "pattern", "support", "resistance", "volume_oi")},
            "lamp": _lamp_of(track_a),
            "entry": tb["entry"], "stop": tb["stop"], "target1": tb["target1"],
            "reasons": tb["reasons"],
        })


def _dump_signals_state():
    """全部品种快照落盘 state/signals_state.json（openclaw 契约）。原子写防半包。"""
    with _overview_lock:
        snap = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "symbols": dict(_overview)}
    try:
        d = os.path.join(user_settings.app_dir(), "state")
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, "signals_state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(d, "signals_state.json"))
    except Exception:  # noqa: BLE001 落盘失败不影响行情主流程
        pass


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


def _mock_pattern_series(n, base=7200):
    """
    生成一段带双底形态的模拟K线（MOCK 下演示轨道B评分用）：
    阴跌出左底 → 反弹出颈位 → 回落出右底(带锤子线) → 放量突破 → 回踩不破 → 再上行。
    持仓量(oi) 在后半程随价格上涨而增加，凑出"价升量增仓增"的健康象限。
    """
    l1, neck, l2 = base * 0.94, base * 1.00, base * 0.945
    # (占比, 目标价, 量倍数, oi倾向)。形态压缩在后半段，保证落在评分引擎回溯窗口内
    segs = [
        (0.42, base * 1.02, 1.0, 0),    # 前段缓跌铺垫
        (0.16, l1,          1.0, -1),   # 下跌出左底
        (0.12, neck,        1.1, 0),    # 反弹出颈位
        (0.12, l2,          0.9, -1),   # 回落出右底
        (0.08, base * 1.03, 2.2, 1),    # 放量突破颈位
        (0.04, base * 1.005, 1.0, 0),   # 回踩颈位上方(不破)
        (0.06, base * 1.06, 1.4, 1),    # 再次上行
    ]
    out, price, oi = [], base * 1.10, 200000
    for si, (frac, target, vm, oi_dir) in enumerate(segs):
        cnt = max(2, int(n * frac))
        step = (target - price) / cnt
        for j in range(cnt):
            o = price
            price += step + random.uniform(-base * 0.0008, base * 0.0008)
            c = price
            h = max(o, c) + random.uniform(0, base * 0.0008)
            low = min(o, c) - random.uniform(0, base * 0.0008)
            # 右底位置造一根锤子线（小实体+长下影，确定性满足反转K线判定）
            if si == 3 and j == cnt - 1:
                c = o + base * 0.0002
                price = c
                h = max(o, c) + base * 0.0003
                low = min(o, c) - base * 0.004
            volume = int(random.uniform(500, 1000) * vm)
            oi_delta = int(random.uniform(50, 400)) * oi_dir
            oi += oi_delta
            out.append({"open": round(o, 1), "high": round(h, 1),
                        "low": round(low, 1), "close": round(c, 1),
                        "volume": volume, "oi": oi_delta})
    return out[:n]


def _invert_bars(bars):
    """K线序列按均价镜像翻转（mock 双顶形态用：双底倒过来就是双顶）。"""
    if not bars:
        return bars
    mid = sum(b["close"] for b in bars) / len(bars) * 2
    return [{**b, "open": round(mid - b["open"], 1), "close": round(mid - b["close"], 1),
             "high": round(mid - b["low"], 1), "low": round(mid - b["high"], 1),
             "oi": -(b.get("oi") or 0)} for b in bars]


# MOCK 下注入轨道B形态的品种（方向各异，方便总览页演示不同灯色/评分）
_MOCK_PATTERN = {"SHFE.ag2608": "bottom", "SHFE.rb2610": "top", "DCE.jm2609": "bottom"}


def _mock_loop():
    """模拟全部预设品种：聚焦品种走完整面板管线，全部品种出全局快照。"""
    _set_boot("loading", 60, "生成模拟数据…")
    _set_state(connected=True, error=None)
    phase = 0.0
    first = True
    while True:
        # 用正弦让方向周期性在多/空/震荡之间切换，便于看灯变化
        phase += 0.15
        drift = math.sin(phase) * 0.6
        base = 7200
        _consume_symbol_request()
        combo = _consume_combo_request() or _current_combo()
        # ---- 聚焦品种：完整三腿 + 面板数据 ----
        kl_trend = _mock_series(base, config.KLINE_LENGTH, drift * 0.4, 3)
        kl_filter = _mock_series(kl_trend[-1]["close"], config.KLINE_LENGTH, drift * 0.5, 2)
        kl_trigger = _mock_series(kl_filter[-1]["close"], config.KLINE_LENGTH, drift * 0.6, 1)
        result = resonance.evaluate(kl_trend, kl_filter, kl_trigger, _combo_labels(combo))
        # 多周期K线：前端查看过的周期才生成（注入双底形态，便于演示轨道B评分）
        with _extra_lock:
            extra_ps = list(_extra_requests)
        for p in extra_ps:
            full = _mock_pattern_series(config.KLINE_LENGTH, base)
            with _extra_lock:
                _extra_data[p] = {"bars": _attach_mas(full), "full": full,
                                  "updated": time.time()}
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
        # ---- 全局层：全部预设品种快照（每品种相位错开，灯色各异）----
        idx_of = {p["symbol"]: i for i, p in enumerate(symbols.PRESETS)}

        def bar_getter(sym, period):
            i = idx_of.get(sym, 0)
            b = 2500 + i * 900
            kind = _MOCK_PATTERN.get(sym)
            if period == config.GLOBAL_TRACKB_PERIOD and kind:
                s = _mock_pattern_series(config.KLINE_LENGTH, b)
                return _invert_bars(s) if kind == "top" else s
            d = math.sin(phase + i * 1.3) * 0.6
            return _mock_series(b, config.KLINE_LENGTH, d * 0.5, max(1, b * 0.0004))

        _global_pass(bar_getter,
                     focus_track_a={"direction": result["direction"],
                                    "resonance": result["resonance"]})
        _dump_signals_state()
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

        _set_boot("loading", 60, "正在拉取K线…")
        # K线订阅缓存：(品种,周期)->serial。切换组合/品种/查看多周期时复用，避免重复订阅。
        # 订阅数估算：全局层 11品种×3周期=33 条（聚焦品种的与全局复用同key），
        # 聚焦品种额外只有 30秒触发腿 + 前端查看过的多周期（≤7条）。
        _serials = {}

        def serial(sym, period):
            key = (sym, period)
            if key not in _serials:
                _serials[key] = api.get_kline_serial(sym, period, config.KLINE_LENGTH)
            return _serials[key]

        combo = _current_combo()
        focus = config.SYMBOL
        kt = serial(focus, combo["trend"])
        kf = serial(focus, combo["filter"])
        kg = serial(focus, combo["trigger"])
        _set_state(connected=True, error=None, combo=combo["key"])

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
            # 聚焦品种切换：换订阅、重置首次加载与看门狗计时（旧订阅留在缓存，切回时秒用）
            new_sym = _consume_symbol_request()
            if new_sym is not None:
                focus = new_sym
                kt = serial(focus, combo["trend"])
                kf = serial(focus, combo["filter"])
                kg = serial(focus, combo["trigger"])
                first = True
                wait_start = time.time()
            # 周期组合切换：换成新订阅，重置首次加载与看门狗计时
            new_combo = _consume_combo_request()
            if new_combo is not None:
                combo = new_combo
                kt = serial(focus, combo["trend"])
                kf = serial(focus, combo["filter"])
                kg = serial(focus, combo["trigger"])
                first = True
                wait_start = time.time()
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
                        f"30秒内未取到足够K线（{combo['trend_label']}{len(kt_list)}根/{combo['trigger_label']}{len(kg_list)}根）。"
                        f"可能原因：①合约 {config.SYMBOL} 非当前主力/已换月 "
                        f"②同一天勤账号在别处登录（同账号不能多设备同时在线）"
                        f"③当前非交易时段且无历史缓存。"
                        f"请在配置页核对合约、确认账号未在别处使用后重试。"
                    )
                continue
            result = resonance.evaluate(kt_list, kf_list, kg_list, _combo_labels(combo))
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
            # 多周期K线（懒加载）：只订阅/刷新前端查看过的周期（聚焦品种）
            with _extra_lock:
                extra_ps = list(_extra_requests)
            for p in extra_ps:
                try:
                    bars = to_list(serial(focus, p))
                    if bars:
                        with _extra_lock:
                            _extra_data[p] = {"bars": _attach_mas(bars), "full": bars,
                                              "updated": time.time()}
                except Exception:  # noqa: BLE001 单个周期失败不影响主流程
                    pass
            # 全局层：全部预设品种快照 + 落盘（聚焦品种用三腿结果覆盖）
            _global_pass(lambda s, p: to_list(serial(s, p)),
                         focus_track_a={"direction": result["direction"],
                                        "resonance": result["resonance"]})
            _dump_signals_state()
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
    # 按合约从预设表解析 tick 参数（查不到保留 config.py 默认值）
    symbols.apply_symbol_params(config)
    config.MOCK_MODE = bool(s.get("mock_mode", False))
    _set_state(symbol=config.SYMBOL, mock=config.MOCK_MODE)


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
                           symbol_name=symbols.name_of(config.SYMBOL) or "",
                           presets=symbols.PRESETS,
                           poll_ms=config.POLL_INTERVAL_MS)


@app.route("/setup")
def setup():
    s = user_settings.load_settings()
    return render_template("setup.html",
                           tq_user=s.get("tq_user", ""),
                           symbol=s.get("symbol", config.SYMBOL),
                           presets=symbols.PRESETS)


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


@app.route("/api/symbols")
def api_symbols():
    """预设品种清单 + 当前聚焦品种（顶栏品种切换器用）。"""
    return jsonify({
        "symbols": [{"symbol": p["symbol"], "name": p["name"]} for p in symbols.PRESETS],
        "current": config.SYMBOL,
    })


@app.route("/api/symbol", methods=["POST"])
def api_symbol():
    """热切换聚焦品种：登记请求，行情线程换订阅、重置看门狗，无需重启。"""
    global _symbol_request
    data = request.get_json(force=True, silent=True) or {}
    sym = (data.get("symbol") or "").strip()
    if not sym:
        return jsonify({"ok": False, "error": "缺少 symbol"}), 400
    with _symbol_lock:
        _symbol_request = sym
    return jsonify({"ok": True, "symbol": sym})


@app.route("/api/overview")
def api_overview():
    """总览页卡片数据：全部预设品种快照（数据未就绪的品种给占位卡）。"""
    with _overview_lock:
        ov = dict(_overview)
    cards = [{"symbol": p["symbol"], **(ov.get(p["symbol"]) or _collecting_entry(p["name"]))}
             for p in symbols.PRESETS]
    # 聚焦品种是预设外的自定义合约时，也补一张卡
    known = [p["symbol"] for p in symbols.PRESETS]
    if config.SYMBOL not in known and config.SYMBOL in ov:
        cards.append({"symbol": config.SYMBOL, **ov[config.SYMBOL]})
    return jsonify({"symbols": cards, "current": config.SYMBOL})


@app.route("/api/periods")
def api_periods():
    """周期组合清单 + 当前生效项（前端渲染切换器用）。"""
    combos = [{k: c[k] for k in ("key", "name", "trend_label", "filter_label", "trigger_label")}
              for c in config.PERIOD_COMBOS]
    return jsonify({"combos": combos, "current": _current_combo()["key"]})


@app.route("/api/period", methods=["POST"])
def api_period():
    """切换共振周期组合：只登记请求，由行情线程换订阅并重算，无需重启。"""
    global _combo_request
    data = request.get_json(force=True, silent=True) or {}
    key = (data.get("key") or "").strip()
    combo = config.get_combo(key)
    if combo["key"] != key:
        return jsonify({"ok": False, "error": "未知周期组合"}), 400
    with _combo_lock:
        _combo_request = key
    return jsonify({"ok": True, "current": key})


@app.route("/api/kline")
def api_kline():
    """多周期K线（懒加载）：前端切到某周期才来登记；数据未就绪返回 loading。"""
    try:
        period = int(request.args.get("period", "0"))
    except ValueError:
        period = 0
    if period not in config.EXTRA_KLINE_SECONDS:
        return jsonify({"status": "error", "error": "不支持的周期"}), 400
    with _extra_lock:
        _extra_requests.add(period)
        data = _extra_data.get(period)
    if data:
        return jsonify({"status": "ok", "period": period,
                        "bars": data["bars"], "updated": data["updated"]})
    return jsonify({"status": "loading"})


@app.route("/api/double_bottom")
def api_double_bottom():
    """轨道B 双底评分：对指定周期K线跑评分（复用多周期懒加载订阅缓存）。"""
    try:
        period = int(request.args.get("period", "0"))
    except ValueError:
        period = 0
    if period not in config.EXTRA_KLINE_SECONDS:
        return jsonify({"status": "error", "error": "不支持的周期"}), 400
    with _extra_lock:
        _extra_requests.add(period)   # 顺便登记懒加载，数据线程会开始订阅该周期
        data = _extra_data.get(period)
    if not data:
        return jsonify({"status": "loading"})
    bars = data.get("full") or data["bars"]
    if len(bars) < 40:
        return jsonify({"status": "insufficient",
                        "message": f"K线不足（当前{len(bars)}根，需≥40根），稍候再试"})
    result = double_bottom.evaluate(bars)
    return jsonify({"status": "ok", "period": period,
                    "updated": data["updated"], "result": result})


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
