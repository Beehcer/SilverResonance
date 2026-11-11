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
import queue
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, render_template, request, redirect, url_for, Response, stream_with_context

import config
import resonance
import symbols
import double_bottom
import entry_logic
import fundamentals
import user_settings
import outlook
import outlook_store
import trading
import trade_plan
import plan_board_demo as board_demo
import workspace_rules
import workspace_store
import workspace_api
from workspace_market import WorkspaceMarketCache, data_length_for_period

_trading_store = trading.TradingStore(os.path.join(user_settings.app_dir(), "state", "trading_state.json"))
_board_demo_store = board_demo.SharedPlanStore(os.path.join(user_settings.app_dir(), "state", "plan_board_demo.json"))
_structure_store = outlook_store.OutlookStore(os.path.join(user_settings.app_dir(), "state", "outlook_state.json"))
_mock_structure_store = outlook_store.OutlookStore()
_workspace_store = workspace_store.WorkspaceStore(os.path.join(user_settings.app_dir(), "state", "workspace.json"))
_workspace_market = WorkspaceMarketCache()
_workspace_quote_lock = threading.Lock()
_workspace_live_quotes = {}


def _resource_dir():
    """PyInstaller 打包后模板在 sys._MEIPASS，源码运行时用脚本目录。"""
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


app = Flask(__name__, template_folder=os.path.join(_resource_dir(), "templates"),
            static_folder=os.path.join(_resource_dir(), "static"))


@app.before_request
def _isolate_legacy_local_tools():
    """Shared browsers use workspace APIs, isolated from the host's ledger/setup."""
    if request.path in ("/", "/workspace") or request.path.startswith(("/api/workspace/", "/static/")):
        return None
    local = request.remote_addr in ("127.0.0.1", "::1", None)
    forwarded = bool(request.headers.get("Forwarded") or request.headers.get("X-Forwarded-For"))
    if not local or forwarded:
        return jsonify(status="error", message="此页面仅供主机管理，请使用共享工作台。"), 403
    return None

# 返回给前端画图的最大K线根数
CHART_BARS = 60
# 画图用的均线周期
CHART_MAS = (5, 20, 60)


def _attach_mas(full_bars):
    """
    用完整序列(full_bars)算 MA5/20/60 + VWAP + RSI + 摆动高低点结构，
    把这些值附到每根K线上，再返回最后 CHART_BARS 根。这样可见区间内
    三条均线/VWAP 都是完整的，不会因为切片后数据不足导致画不出。

    附加指标：
    - vwap : 观察窗口内的典型价成交量加权均价；未按交易日重置，不等同真实持仓成本。
    - rsi  : 14周期相对强弱，用于背离预警（价格新高但 RSI 未新高＝顶背离）。
    - swing: "H"=摆动高点 / "L"=摆动低点，用于画高低点结构、识别递减高点。
    """
    closes = [b["close"] for b in full_bars]
    highs = [b["high"] for b in full_bars]
    lows = [b["low"] for b in full_bars]
    vols = [b.get("volume", 0) for b in full_bars]

    # ---- MA ----
    for i, b in enumerate(full_bars):
        for p in CHART_MAS:
            if i + 1 >= p:
                b[f"ma{p}"] = round(sum(closes[i - p + 1:i + 1]) / p, 1)
            else:
                b[f"ma{p}"] = None

    # ---- 观察窗口累计 VWAP：零量不参与权重；首笔有效成交量前返回 None ----
    cum_pv = 0.0
    cum_v = 0.0
    for i, b in enumerate(full_bars):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        v = vols[i]
        v = v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 else 0
        cum_pv += typical * v
        cum_v += v
        b["vwap"] = round(cum_pv / cum_v, 1) if cum_v else None

    # ---- RSI(14)：Wilder 平滑 ----
    # 复用引擎的对齐实现：第14号K线已有首个RSI，后续涨跌逐根进入平滑。
    rsis = double_bottom._rsi_list(closes, period=14)
    for b, rsi in zip(full_bars, rsis):
        b["rsi"] = round(rsi, 1) if rsi is not None else None

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

    # 目标：优先轨道B计划，缺则丈量目标(2×颈位−L1，双向通用)/最近强位补齐
    t1 = (tbs or {}).get("target1") or round(2 * neck - L1, 2)
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
    "我要开仓" 决策面板：双轴评分模型（位置分 × 确定性分 → 象限决策）。
    唯一的"不做"区是双低象限（avoid）；位置分无法评估时降级为仅确定性分驱动。
    stage 字段（三批次阶段识别）保留为入场点坐标参考，不驱动决策。
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
                "stage_name": "等待中", "location_score": None, "certainty_score": 0,
                "quadrant": "standby", "quadrant_name": "观望", "direction": "none",
                "position_pct": 0, "stop": None, "entry_point_ref": "none",
                "regime": None,
                "scores_detail": {"location": [], "certainty": []},
                "ladder": {"L1": None, "L2": None, "neck": None, "price": None,
                           "stop": None, "target1": None, "target2": None},
                "wait_for": "轨道B数据收集中（需≥40根1h K线），稍候再试",
                "summary": "1h K线尚未收齐，无法做双轴评分。"}
    price = (res or {}).get("price") or kb[-1]["close"]
    ev = double_bottom.evaluate(kb)
    regime = entry_logic.detect_regime(kb)
    # 小周期验证：触发周期企稳 且 轨道A方向与目标方向一致（缺数据传 None 不扣分）
    target = {"double_bottom": "long", "double_top": "short"}.get(ev["pattern"].get("type"))
    small_tf = None
    if res and res.get("legs"):
        small_tf = _small_tf_verify(target, res.get("direction"),
                                    res["legs"].get("trigger", {}).get("ok"))
    out = entry_logic.decide(ev, price, atr=double_bottom._atr(kb),
                             small_tf_ok=small_tf, regime=regime)
    # 企稳直通道：decide 返回 standby/avoid 且轨道A三频共振时，直接给 probe（不等双底结构）
    if out["quadrant"] in ("standby", "avoid") and res:
        dp = entry_logic.direct_probe(price, atr=double_bottom._atr(kb) or (price or 1.0) * 0.005,
                                       resonance=res, klines=kb)
        if dp:
            dp["stage"] = "direct_probe"
            dp["stage_name"] = "企稳直通道"
            dp["ladder"] = {"L1": None, "L2": None, "neck": None, "price": price,
                            "stop": dp["stop"]["price"] if dp["stop"] else None,
                            "target1": None, "target2": None}
            dp["mode"] = mode
            dp["mode_label"] = mode_label
            return dp
    # stage（三批次阶段）仅作参考坐标；entry_point_ref 按约定由它推导
    stage_ref = _stage_advice(kb, _focus_track_b(), price)
    out["stage"] = stage_ref["stage"]
    out["stage_name"] = stage_ref["stage_name"]
    out["entry_point_ref"] = {"2b_probe": "2b", "retest_confirm": "retest",
                              "range_addon": "point3"}.get(stage_ref["stage"], "none")
    out["ladder"] = stage_ref["ladder"]
    if out["stop"]:
        out["ladder"]["stop"] = out["stop"]["price"]
    out["mode"] = mode
    out["mode_label"] = mode_label
    return out


# ---- mock 演示：四象限合成输入（/api/entry_check?demo=main|probe|confirm|avoid）----
_DEMO_REGIME = {
    "main":    {"regime": "trend_up", "er": 0.71, "slope": 0.011, "label": "趋势市·多"},
    "probe":   {"regime": "range", "er": 0.12, "slope": 0.0004, "label": "震荡市"},
    "confirm": {"regime": "trend_up", "er": 0.68, "slope": 0.009, "label": "趋势市·多"},
    "avoid":   {"regime": "range", "er": 0.09, "slope": -0.0002, "label": "震荡市"},
}

_DEMO_EV = {
    # 双高：贴 L2 + 证据齐全
    "main": ({"found": True, "type": "double_bottom",
              "L1": {"price": 6800.0, "index": 10}, "L2": {"price": 6820.0, "index": 30},
              "neck": 7000.0,
              "confirmations": ["L2反转K线", "回踩不破颈位", "RSI底背离"],
              "rsi_divergence": True},
             80, "long", 3, True, 6825.0, True),
    # 位置高+确定性低：同位置，证据薄弱
    "probe": ({"found": True, "type": "double_bottom",
               "L1": {"price": 6800.0, "index": 10}, "L2": {"price": 6820.0, "index": 30},
               "neck": 7000.0, "confirmations": ["L2反转K线"], "rsi_divergence": False},
              40, "wait", 0, False, 6825.0, None),
    # 确定性高+位置低：证据齐全但突破后延伸
    "confirm": ({"found": True, "type": "double_bottom",
                 "L1": {"price": 6800.0, "index": 10}, "L2": {"price": 6820.0, "index": 30},
                 "neck": 7000.0,
                 "confirmations": ["L2反转K线", "放量突破颈位", "回踩不破颈位", "RSI底背离"],
                 "rsi_divergence": True},
                80, "long", 3, True, 7100.0, True),
    # 双低：中间位 + 证据薄弱
    "avoid": ({"found": True, "type": "double_bottom",
               "L1": {"price": 6800.0, "index": 10}, "L2": {"price": 6820.0, "index": 30},
               "neck": 7000.0, "confirmations": ["L2反转K线"], "rsi_divergence": False},
              40, "wait", 0, False, 6970.0, None),
}


def _demo_entry_check(kind):
    """mock 下按象限名合成 evaluate 输入，走真实 decide() 引擎（演示/联调用）。"""
    pattern, score, direction, mom, healthy, price, small_tf = _DEMO_EV[kind]
    ev = {"score": score, "direction": direction, "pattern": pattern,
          "levels": {"support": [], "resistance": []},
          "momentum": {"macd": mom >= 1, "rsi": mom >= 2, "ma_slope": mom >= 3,
                       "resonance_count": mom},
          "volume_oi": {"quadrant": "演示", "healthy": healthy},
          "plan": {"entry": price, "stop": 6790.0, "target1": 7200.0, "target2": None}
          if direction != "wait" else None,
          "reasons": []}
    out = entry_logic.decide(ev, price, atr=20.0, small_tf_ok=small_tf,
                             regime=_DEMO_REGIME[kind])
    out.update({"mode": "demo", "mode_label": f"演示模式（{kind} 象限合成数据）",
                "stage": "standby", "stage_name": "演示",
                "ladder": {"L1": 6800.0, "L2": 6820.0, "neck": 7000.0, "price": price,
                           "stop": out["stop"]["price"] if out["stop"] else None,
                           "target1": 7200.0, "target2": None}})
    return out

# 共享状态：数据线程写，Flask 读。用锁保护。
_state_lock = threading.Lock()
_state = {
    "connected": False,
    "connection_status": "disconnected",
    "feed_checked_at": 0,
    "feed_received_at": 0,
    "mock": config.MOCK_MODE,
    "symbol": config.SYMBOL,
    "combo": config.DEFAULT_COMBO,  # 当前共振周期组合 key
    "result": None,       # resonance.evaluate 的输出
    "generation": 0,
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
        if kwargs.get("connected") is False:
            kwargs.setdefault("result", None)
            kwargs.setdefault("connection_status", "disconnected")
        _state.update(kwargs)
        _state["updated"] = time.time()


def get_state():
    with _state_lock:
        return dict(_state)


def _publish_focus_result(sym, combo, result):
    """身份与结果一起发布；切换中不得把旧品种价格挂到新标题下。"""
    with _state_lock:
        if sym != config.SYMBOL or combo["key"] != _state.get("combo"):
            return
        tagged = dict(result, symbol=sym, combo=combo["key"], generation=_state["generation"])
        _state.update(result=tagged, updated=time.time())


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
            state = get_state()
            _set_state(combo=combo["key"], result=None, generation=state.get("generation", 0) + 1)
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
    state = get_state()
    _set_state(symbol=req, result=None, generation=state.get("generation", 0) + 1)
    with _extra_lock:
        _extra_data.clear()               # 多周期缓存属于旧品种，清掉等前端重拉
    return req


# ---------------- 全局监控层（全品种快照 + openclaw 落盘契约） ----------------
_overview_lock = threading.Lock()
_overview = {}   # 品种代码 -> 快照条目（结构即 signals_state.json 的每品种条目）
_tb_throttle = {}  # 品种 -> {"n": 已跑轮数, "summary": 轨道B摘要缓存}（节流用）
_track_b_bars = {}  # 品种 -> 最近一份 1h 原始K线（三批次阶段识别用）
_track_b_regime = {}  # 品种 -> 市场状态（detect_regime 输出，随轨道B节流重算）

# 条件推演和30分计划共用同一代结构；辅助共振与评分仍独立。
OUTLOOK_PERIODS = (180, 900, 1800, 3600)
_outlook_lock = threading.Lock()
_outlook_data = {}  # (symbol, period) -> 原始K线快照，按品种隔离防止切换串图


def _bar_timestamp(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone(timedelta(hours=8))).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _snapshot_times(previous, unchanged, bars, period, now):
    """检查缓存不代表新成交；首次载入的旧K线也不能获得交易时效。"""
    market_updated = now
    last_start = _bar_timestamp(bars[-1].get("dt"))
    if last_start is not None:
        # 日线的起点不是收盘时刻；没有交易日历，不生成日线名义收盘时间。
        market_updated = (last_start if last_start > now else
                          min(now, last_start + period if period < 86400 else last_start))
    if previous and unchanged:
        return {"updated": now, "changed_at": previous.get("changed_at", previous.get("market_updated", 0)),
                "market_updated": previous.get("market_updated", 0)}
    return {"updated": now, "changed_at": now, "market_updated": market_updated}


def _chart_freshness(data, period, state=None, now=None):
    """展示新鲜度与交易时效分开；不从无成交推断休市或网络中断。"""
    state = get_state() if state is None else state
    now = time.time() if now is None else now
    data = data or {}
    bars = data.get("full") or data.get("bars") or []
    checked = data.get("updated", 0)
    market = data.get("market_updated", 0)
    connected = bool(state.get("connected"))
    checked_recent = bool(checked and 0 <= now - checked <= 20)
    realtime = bool(connected and bars and checked_recent and market and 0 <= now - market <= 20)
    if not connected:
        status, label = "disconnected", "连接中断，等待恢复"
    elif not bars:
        status, label = "loading", "正在加载最新可用K线"
    elif not checked_recent:
        status, label = "stale", "缓存刷新中断，等待重取"
    elif realtime:
        status, label = "receiving", "正在接收行情更新"
    else:
        status, label = "latest_available", "最新可用K线，暂未见新变动"
    last_time = bars[-1].get("dt") if bars else None
    start = _bar_timestamp(last_time)
    last_state = "unknown"
    if start is not None and period < 86400 and now >= start:
        last_state = "forming" if now < start + period else "awaiting_next"
    return {"status": status, "label": label, "checked_at": checked,
            "changed_at": data.get("changed_at", 0), "market_updated": market,
            "feed_checked_at": state.get("feed_checked_at", 0),
            "feed_received_at": state.get("feed_received_at", 0),
            "connection_status": state.get("connection_status", "unknown" if connected else "disconnected"),
            "last_bar_time": last_time, "last_bar_state": last_state,
            "last_completed_time": bars[-2].get("dt") if len(bars) > 1 else None,
            "live_bar_included": bool(bars), "session": "unknown", "realtime": realtime}


def _cache_outlook_bars(sym, period, bars):
    if period not in OUTLOOK_PERIODS or not bars:
        return
    fields = ("open", "high", "low", "close", "volume", "oi", "dt")
    view = [{k: b[k] for k in fields if k in b} for b in bars[-120:]]
    now = time.time()
    if config.MOCK_MODE and all(not b.get("dt") for b in view):
        # 模拟行情没有交易所时间，仅给它独立、明确的模拟时间轴。
        # 随机历史改写会显示重建原因，且不写入真实结构档案。
        last_start = int(now // period) * period
        for i, b in enumerate(view):
            stamp = last_start - (len(view) - 1 - i) * period
            b["dt"] = datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    with _outlook_lock:
        previous = _outlook_data.get((sym, period))
        clocks = _snapshot_times(previous, previous and previous["bars"] == view, view, period, now)
        _outlook_data[(sym, period)] = {"bars": view, **clocks}


def _cache_chart_bars(sym, period, bars):
    """替换独立快照，保留末根；完成棒裁切仅由分析模块处理。"""
    if not bars or sym != config.SYMBOL:
        return
    full = [dict(bar) for bar in bars]
    chart = _attach_mas([dict(bar) for bar in full])
    with _extra_lock:
        previous = _extra_data.get(period)
        if previous and previous.get("symbol") != sym:
            previous = None
        clocks = _snapshot_times(previous, previous and previous.get("full") == full,
                                 full, period, time.time())
        _extra_data[period] = {"symbol": sym, "bars": chart, "full": full, **clocks}
    _cache_outlook_bars(sym, period, full)


def _requested_chart_periods():
    with _extra_lock:
        # 主战场独立于辅助共振组合，首次加载尚未完成也持续订阅。
        return sorted(_extra_requests | {180, 1800})


def _refresh_focus_charts(sym, bar_getter):
    for period in _requested_chart_periods():
        try:
            _cache_chart_bars(sym, period, bar_getter(sym, period))
        except Exception:  # 单周期失败保留旧快照，检查时间不续期，API显式显示停更。
            continue


def _tracked_outlook(sym, bars, period=1800):
    """Called under _outlook_lock to serialize cached snapshots and advancement."""
    preset = symbols.find(sym)
    store = _mock_structure_store if config.MOCK_MODE else _structure_store
    return store.analyze(bars[-120:], symbol=sym,
                         tick=preset["tick"] if preset else config.PRICE_TICK, period=period)


def _outlook_summary(sym, bars, period=1800):
    with _outlook_lock:
        data = _outlook_data.get((sym, period))
        analysis = _tracked_outlook(sym, data["bars"] if data else bars, period)
    state = analysis.get("state") or {}
    market = analysis.get("market_structure") or {}
    market_phase = market.get("phase") or {}
    return {"state": state.get("label", "数据收集中"),
            "summary": state.get("summary", analysis.get("message", "等待足够的已完成K线")),
            "market_phase": market_phase.get("label"), "market_summary": market_phase.get("summary"),
            "period": period, "structure_id": (analysis.get("structure_tracking", {}).get("active") or {}).get("id"),
            "preparation": {"reference": market.get("reference"), "observation_zone": market.get("observation_zone"),
                            "key_levels": {key: (market.get("key_levels") or {}).get(key) for key in
                                           ("current_support", "current_resistance")},
                            "basis_time": market.get("decision_bar_time")}
            if market.get("status") == "ok" else None}


def _registered_prices():
    if not get_state().get("connected"):
        return {}
    with _outlook_lock:
        return {sym: data["bars"][-1]["close"] for (sym, period), data in _outlook_data.items()
                if period == 180 and data.get("bars") and _chart_freshness(data, period)["realtime"]}


def _structure_presentation(sym, primary, small, analysis, plan=None, risk=None):
    """30m展示快照；仅传已有结构证据，不替代点位编号或修改交易准入。"""
    if not primary or not isinstance(analysis, dict) or analysis.get("status") != "ok":
        return None
    tracking = analysis.get("structure_tracking") or {}
    active = tracking.get("active") or {}
    state = analysis.get("state") or {}
    market_updated = min(primary.get("market_updated", 0), (small or {}).get("market_updated", 0))
    freshness = _chart_freshness(primary, 1800)
    realtime = bool(freshness["realtime"] and _chart_freshness(small, 180)["realtime"])
    completed = [{key: bar.get(key) for key in ("dt", "open", "high", "low", "close")}
                 for bar in primary.get("bars", [])[-7:-1]]
    return {"symbol": sym, "period": 1800, "structure_id": active.get("id"),
            "phase": active.get("phase") or state.get("key"), "state_label": state.get("label"),
            "summary": state.get("summary"), "range": analysis.get("range") or {},
            "buffer": analysis.get("buffer"), "atr": analysis.get("atr"),
            "candidate": tracking.get("candidate"), "events": (tracking.get("events") or [])[-3:],
            "market_structure": analysis.get("market_structure") or {}, "freshness": freshness,
            "completed": completed, "reference_end": (analysis.get("range") or {}).get("end_time"),
            "updated": primary.get("updated", 0), "market_updated": market_updated,
            "realtime": realtime, "price": analysis.get("price"),
            "plan": plan if realtime and plan else {}, "risk": risk if realtime and risk else {}}


def _trade_bundle(sym, mode="confirmed", record=False):
    identity = {"symbol": sym, "period": 1800, "generation": get_state().get("generation", 0)}
    preset = symbols.find(sym)
    if not preset or mode not in ("confirmed", "early"):
        return dict(status="error", message="未知品种或入场模式", **identity)
    if _trading_store.load_error:
        return dict(status="error", message=_trading_store.load_error, **identity)
    with _outlook_lock:
        primary = _outlook_data.get((sym, 1800))
        small = _outlook_data.get((sym, 180))
        analysis = _tracked_outlook(sym, primary["bars"]) if primary else None
    identity["structure_context"] = _structure_presentation(sym, primary, small, analysis)
    identity["freshness"] = _chart_freshness(primary, 1800)
    if not primary or not small:
        return dict(status="loading", message="正在收集30分结构与3分确认K线", **identity)
    updated = min(primary["updated"], small["updated"])
    if not get_state().get("connected"):
        return dict(status="error", message="行情刷新中断，暂停交易计划", **identity)
    if any(_chart_freshness(data, period)["status"] == "stale" for data, period in ((primary, 1800), (small, 180))):
        return dict(status="error", message="K线缓存刷新中断，暂停交易计划", **identity)
    if not all(_chart_freshness(data, period)["realtime"] for data, period in ((primary, 1800), (small, 180))):
        return dict(status="error", message="行情超过20秒未变动（可能休市或停更），暂停实时计划", **identity)
    if analysis and analysis.get("status") == "error":
        return dict(status="error", message=analysis.get("message", "结构记录不可用"), **identity)
    plan = trade_plan.build_plan(primary["bars"], small_bars=small["bars"],
                                 tick=preset["tick"], mode=mode, live_price=small["bars"][-1]["close"],
                                 analysis=analysis)
    structure = (analysis.get("structure_tracking") or {}).get("active") or {}
    plan["structure_id"] = structure.get("id")
    plan["structure_phase"] = structure.get("phase")
    plan["technical_status"] = plan["status"]
    with _trading_store.lock:
        settings = _trading_store.settings(sym)
        risk = trading.risk_for_plan(plan, settings, preset, _trading_store.open_positions(), _trading_store.idea(sym))
        positions = _trading_store.positions_view(sym, _registered_prices())["positions"]
    if plan["status"] == "candidate":
        if any(p["stop_breached"] for p in positions):
            plan.update(status="risk_blocked", label="已有持仓触及止损", wait_for="先核对交易端止损与实际持仓，再考虑新增风险")
            risk["max_lots"] = 0
            for row in risk["rows"]:
                row["allowed"] = False
        elif not risk["max_lots"]:
            plan.update(status="risk_blocked", label="风险未通过" if risk["fee_configured"] else "风险未核算",
                        wait_for=risk["reason"])
        elif risk["net_rr"] is None or risk["net_rr"] < config.MIN_RR:
            plan.update(status="risk_blocked", label="净收益空间不足", wait_for=f"扣除费用和滑点预留后，收益风险比需至少{config.MIN_RR:g}")
            risk["max_lots"] = 0
            for row in risk["rows"]:
                row["allowed"] = False
        else:
            plan.update(status="ready", label="提前试仓条件已满足" if mode == "early" else "确认条件已满足")
    if record:
        try:
            _trading_store.record_signal(sym, plan, risk, plan.get("decision_bar_time"))
        except (OSError, ValueError):
            return dict(status="error", message="计划记录保存失败，请检查本地存储", **identity)
    identity["structure_context"] = _structure_presentation(sym, primary, small, analysis, plan, risk)
    return dict(status="ok", updated=updated, plan=plan, risk=risk, settings=settings,
                positions=positions, **identity)


def _plan_decision(bundle):
    if bundle.get("status") != "ok":
        return _decision_standby(bundle.get("message", "计划数据收集中"))
    plan, risk = bundle["plan"], bundle["risk"]
    ready = plan["status"] == "ready"
    return {"quadrant": ("probe" if plan["mode"] == "early" else "confirm") if ready else "standby",
            "position_pct": 0, "location_score": None, "certainty_score": 0,
            "direction": plan["direction"], "stabilized": ready, "trigger_met": ready,
            "stop": {"price": plan["stop"], "basis": "30分计划失效"} if plan.get("stop") else None,
            "wait_for": plan["wait_for"], "label": plan["label"], "plan_status": plan["status"],
            "max_lots": risk["max_lots"] if ready else 0, "period": 1800, "mode": plan["mode"],
            "summary": plan["summary"]}


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
    """轨道B摘要 + 市场状态（按 config.GLOBAL_TRACKB_EVERY 节流重算，K线未收盘时不变）。
    返回 (摘要, 本轮是否重算)；evaluate 全量输出缓存在 _tb_throttle[sym]["ev"]，
    供决策块复用（避免每轮重复跑评分引擎）。"""
    kb = kb[:-1]  # SDK末根未完成，结构评分只使用完成棒。
    _track_b_bars[sym] = kb
    ent = _tb_throttle.get(sym) or {"n": 0, "summary": None, "ev": None, "trigger_met": False}
    stamp = tuple(tuple(b.get(k) for k in ("dt", "open", "high", "low", "close", "volume", "oi")) for b in kb)
    refreshed = ent["summary"] is None or ent.get("stamp") != stamp
    if refreshed:
        preset = symbols.find(sym)
        ent["ev"] = double_bottom.evaluate(kb, tick=preset["tick"] if preset else config.PRICE_TICK)
        ent["summary"] = _track_b_summary(ent["ev"])
        _track_b_regime[sym] = entry_logic.detect_regime(kb)
        ent["stamp"] = stamp
    _tb_throttle[sym] = ent
    return ent["summary"], refreshed


# ---------------- 双轴决策块（signals_state 契约，与 /api/entry_check 同源） ----------------

def _small_tf_verify(target, tf_direction, tf_trigger_ok):
    """小周期企稳验证口径（/api/entry_check 与全局层共用）：
    轨道A方向与目标方向一致 且 触发周期企稳通过（resonance.trigger_ok）。
    缺数据返回 None（引擎不扣分）；无目标方向返回 None。"""
    if not target or tf_direction is None or tf_trigger_ok is None:
        return None
    return bool(tf_direction == target and tf_trigger_ok)


def _decision_block(d, stabilized, trigger_met):
    """entry_logic.decide 输出 → signals_state 每品种 decision 决策块（openclaw 契约）。
    字段名与 /api/entry_check 响应对齐：quadrant/position_pct/location_score/
    certainty_score/stop{price,basis}/wait_for；stabilized=小周期企稳，trigger_met=阶段触发。"""
    stop = d.get("stop")
    return {
        "quadrant": d["quadrant"],
        "position_pct": d["position_pct"],
        "location_score": d["location_score"],
        "certainty_score": d["certainty_score"],
        "direction": d["direction"],
        "stabilized": bool(stabilized),
        "trigger_met": bool(trigger_met),
        "stop": {"price": stop["price"], "basis": stop["basis"]} if stop else None,
        "wait_for": d.get("wait_for"),
    }


def _decision_standby(wait_for):
    """数据不足/异常降级：standby + wait_for，永不抛异常。"""
    return {"quadrant": "standby", "position_pct": 0, "location_score": None,
            "certainty_score": 0, "direction": "none", "stabilized": False,
            "trigger_met": False, "stop": None, "wait_for": wait_for}


def _decision_for(sym, price, tf_direction, tf_trigger_ok):
    """全局层每品种决策块：复用 entry_logic.decide（与 /api/entry_check 同引擎同口径）。
    ev/regime/trigger_met 随轨道B节流节奏缓存；位置/确定性分每轮按最新价重算（decide 为纯函数，开销小）。"""
    try:
        ent = _tb_throttle.get(sym) or {}
        ev, kb = ent.get("ev"), _track_b_bars.get(sym)
        if not ev or not kb or len(kb) < 40:
            return _decision_standby("轨道B数据收集中（需≥40根1h K线）")
        target = {"double_bottom": "long", "double_top": "short"}.get(ev["pattern"].get("type"))
        small_tf = _small_tf_verify(target, tf_direction, tf_trigger_ok)
        d = entry_logic.decide(ev, price, atr=double_bottom._atr(kb),
                               small_tf_ok=small_tf, regime=_track_b_regime.get(sym))
        return _decision_block(d, stabilized=bool(small_tf),
                               trigger_met=ent.get("trigger_met", False))
    except Exception as e:  # noqa: BLE001 单品种决策失败降级 standby，不断整轮落盘
        return _decision_standby(f"决策计算异常：{type(e).__name__}")


def _collecting_entry(name, sym=None):
    """数据不足时的占位条目（总览页降级显示"数据收集中"）。"""
    return {
        "name": name, "price": None,
        "track_a": {"direction": "none", "resonance": False},
        "track_b": {"score": None, "direction": "wait",
                    "pattern": {"found": False, "type": None, "L1": None, "L2": None, "neck": None},
                    "support": [], "resistance": [],
                    "volume_oi": {"quadrant": "数据不足", "healthy": False}},
        "regime": None,
        "lamp": "gray", "entry": None, "stop": None, "target1": None,
        "decision": _decision_standby("数据收集中…"),
        "fundamentals": fundamentals.state_block(sym) if sym else None,
        "reasons": ["数据收集中…"],
    }


def _set_overview(sym, entry):
    with _overview_lock:
        _overview[sym] = entry


def _global_pass(bar_getter, focus_track_a=None, focus_trigger=None):
    """
    全局监控层：对全部预设品种跑 轨道A两腿雷达 + 轨道B评分 + 双轴决策块，写入 _overview。
    bar_getter(sym, period) -> K线列表（可能为 []）。
    focus_track_a：聚焦品种的完整三腿结果 (direction, resonance)，有则覆盖两腿雷达（更准）。
    focus_trigger：聚焦品种触发腿 {"direction", "trigger_ok"}（与 /api/entry_check 小周期验证同源）。
    """
    for p in symbols.PRESETS:
        sym, name = p["symbol"], p["name"]
        try:
            kt = bar_getter(sym, config.GLOBAL_TREND_PERIOD)
            kf = bar_getter(sym, config.GLOBAL_FILTER_PERIOD)
            kb = bar_getter(sym, config.GLOBAL_TRACKB_PERIOD)
        except Exception:  # noqa: BLE001 单品种失败跳过，不拖垮全局
            continue
        for period, bars in ((config.GLOBAL_TREND_PERIOD, kt),
                             (config.GLOBAL_FILTER_PERIOD, kf),
                             (config.GLOBAL_TRACKB_PERIOD, kb)):
            _cache_outlook_bars(sym, period, bars)
        if len(kt) < config.MA_SLOW + config.TREND_SLOPE_LOOKBACK or len(kb) < 40:
            _set_overview(sym, _collecting_entry(name, sym))
            continue
        # 轨道A两腿雷达（全局层没有30秒触发腿，聚焦品种由主流程覆盖为三腿结果）
        direction = resonance.trend_direction(kt[:-1])
        f_ok = resonance.filter_ok(kf[:-1], direction) if direction != "none" else False
        track_a = {"direction": direction, "resonance": direction != "none" and f_ok, "scope": "两周期方向过滤"}
        if focus_track_a and sym == config.SYMBOL:
            track_a = dict(focus_track_a, scope="聚焦三周期辅助")
        tb, tb_fresh = _track_b_for(sym, kb)
        price = round(kf[-1]["close"] if kf else kt[-1]["close"], 2)
        # 小周期企稳（3分级别，trigger_ok 同一口径）：聚焦品种用完整三腿触发腿结果覆盖
        tf_direction = direction
        tf_trigger_ok = resonance.trigger_ok(kf[:-1], direction, tick=p["tick"]) if (kf and direction != "none") else None
        if focus_trigger and sym == config.SYMBOL:
            tf_direction = focus_trigger.get("direction")
            tf_trigger_ok = focus_trigger.get("trigger_ok")
        # 阶段触发（企稳K线/缩量回踩/波动收缩）：随轨道B节流节奏重算，避免每轮跑形态识别
        ent = _tb_throttle.get(sym) or {}
        if tb_fresh:
            try:
                stage = _stage_advice(kb[:-1], {"target1": tb["target1"], "target2": None,
                                           "track_b": tb}, price)
                trig = stage.get("trigger")
                ent["trigger_met"] = bool(trig and trig.get("met"))
            except Exception:  # noqa: BLE001 触发判定失败按未触发降级
                ent["trigger_met"] = False
            _tb_throttle[sym] = ent
        bundle = _trade_bundle(sym, record=True)
        plan = bundle.get("plan", {})
        _set_overview(sym, {
            "name": name, "price": price,
            "track_a": track_a,
            "track_b": {k: tb[k] for k in
                        ("score", "direction", "pattern", "support", "resistance", "volume_oi")},
            "regime": _track_b_regime.get(sym),
            "lamp": _lamp_of(track_a),
            "entry": plan.get("entry"), "stop": plan.get("stop"), "target1": plan.get("target"),
            "decision": _plan_decision(bundle),
            "fundamentals": fundamentals.state_block(sym),
            "reasons": tb["reasons"],
            "outlook": _outlook_summary(sym, kt, config.GLOBAL_TREND_PERIOD),
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


def _mock_trading_times(count, period, now):
    now = int(now or time.time())
    local = datetime.fromtimestamp(now, timezone(timedelta(hours=8)))
    days = max(30, int(count * period / 9000) + 30)
    first_day = local.date() - timedelta(days=days)
    stamps = []
    sessions = ((9, 0, 10, 15), (10, 30, 11, 30), (13, 30, 15, 0), (21, 0, 23, 0))
    for offset in range(days + 1):
        day = first_day + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for start_h, start_m, end_h, end_m in sessions:
            start = int(datetime(day.year, day.month, day.day, start_h, start_m,
                                 tzinfo=timezone(timedelta(hours=8))).timestamp())
            end = int(datetime(day.year, day.month, day.day, end_h, end_m,
                               tzinfo=timezone(timedelta(hours=8))).timestamp())
            for stamp in range(start, end, period):
                if stamp <= now:
                    stamps.append(stamp)
    return stamps[-count:]


def _mock_timestamped_series(base, period, drift, vol, now=None, count=None):
    length = count or config.KLINE_LENGTH
    if length <= 1000:
        rows = _mock_series(base, length, drift, vol)
    else:
        rows = []
        price = base
        oi = 200000
        for index in range(length):
            target = base * (1 + .035 * math.sin(index / 300))
            open_price = price
            price += (target - price) * .006 + random.uniform(-vol, vol)
            close = price
            high = max(open_price, close) + random.uniform(0, vol)
            low = min(open_price, close) - random.uniform(0, vol)
            oi_delta = int((close - open_price) * 200 + random.uniform(-400, 400))
            oi += oi_delta
            rows.append({"open": round(open_price, 1), "high": round(high, 1),
                         "low": round(low, 1), "close": round(close, 1),
                         "volume": int(abs(close-open_price)*300 + random.uniform(200, 1500)),
                         "oi": oi_delta})
    times = _mock_trading_times(len(rows), period, now)
    for index, row in enumerate(rows):
        row["dt"] = datetime.fromtimestamp(times[index], timezone.utc).isoformat()
    return rows


def _mock_aggregate_series(series, period):
    grouped = []
    group_dt = None
    for bar in series:
        stamp = datetime.fromisoformat(bar["dt"]).timestamp()
        if period == 86400:
            day = datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).date().isoformat()
            current_dt = day + "T00:00:00+08:00"
        else:
            current_dt = datetime.fromtimestamp(int(stamp // period) * period, timezone.utc).isoformat()
        if current_dt != group_dt:
            grouped.append({"dt": current_dt, "open": bar["open"], "high": bar["high"],
                            "low": bar["low"], "close": bar["close"],
                            "volume": bar.get("volume", 0), "oi": bar.get("oi", 0)})
            group_dt = current_dt
        else:
            grouped[-1].update(high=max(grouped[-1]["high"], bar["high"]),
                               low=min(grouped[-1]["low"], bar["low"]), close=bar["close"],
                               volume=grouped[-1]["volume"] + bar.get("volume", 0),
                               oi=bar.get("oi", grouped[-1]["oi"]))
    return grouped[-data_length_for_period(period):]


def _mock_continuous_series(series, drift, vol, advance=False):
    if not series:
        return _mock_series(2500, config.KLINE_LENGTH, drift, vol)
    if advance:
        last_dt = series[-1].get("dt")
        previous_dt = series[-2].get("dt") if len(series) > 1 else None
        step = ((datetime.fromisoformat(last_dt) - datetime.fromisoformat(previous_dt)).total_seconds()
                if last_dt and previous_dt else None)
        series.pop(0)
        rows = _mock_series(series[-1]["close"], 1, drift, vol)
        if last_dt and step:
            rows[0]["dt"] = (datetime.fromisoformat(last_dt) + timedelta(seconds=step)).isoformat()
        series.extend(rows)
        return series
    last = series[-1]
    close = round(max(vol, last["close"] + drift * .12 + random.uniform(-vol, vol) * .35), 1)
    last.update(close=close, high=round(max(last["high"], last["open"], close), 1),
                low=round(min(last["low"], last["open"], close), 1),
                volume=int(abs(close - last["open"]) * 300 + random.uniform(200, 1500)),
                oi=last["oi"] + int((close - last["open"]) * 20 + random.uniform(-30, 30)))
    return series


def _mock_loop():
    """模拟全部预设品种：聚焦品种走完整面板管线，全部品种出全局快照。"""
    _set_boot("loading", 60, "生成模拟数据…")
    _set_state(connected=True, connection_status="simulated", error=None)
    phase = 0.0
    first = True
    workspace_series = {}
    workspace_ticks = {}
    while True:
        _note_feed_poll(received=True)
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
        result = resonance.evaluate(kl_trend[:-1], kl_filter[:-1], kl_trigger[:-1], _combo_labels(combo), tick=config.PRICE_TICK)
        result["price"] = kl_trigger[-1]["close"]
        # 多周期K线：前端查看过的周期才生成（注入双底形态，便于演示轨道B评分）
        for p in _requested_chart_periods():
            full = _mock_pattern_series(config.KLINE_LENGTH, base)
            _cache_chart_bars(config.SYMBOL, p, full)
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
        _publish_focus_result(config.SYMBOL, combo, result)
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
                                    "resonance": result["resonance"]},
                     focus_trigger={"direction": result["direction"],
                                    "trigger_ok": result["legs"]["trigger"]["ok"]})
        for sym in _workspace_market.requested():
            i = idx_of.get(sym, 0)
            volatility = max(1, (2500 + i * 900) * .0004)
            series = workspace_series.get(sym)
            if not series:
                series = _mock_timestamped_series(2500 + i * 900, 180,
                                                  math.sin(phase + i * 1.3) * .5, volatility,
                                                  count=86400)
                workspace_series[sym] = series
            workspace_ticks[sym] = workspace_ticks.get(sym, 0) + 1
            _mock_continuous_series(series, math.sin(phase + i * 1.3) * .6,
                                    volatility, advance=workspace_ticks[sym] % 12 == 0)

        def workspace_bar_getter(sym, period):
            return _mock_aggregate_series(workspace_series[sym], period)

        _workspace_market.refresh(workspace_bar_getter, simulated=True)
        _dump_signals_state()
        if first:
            _set_boot("ready", 100, "就绪")
            first = False
        time.sleep(1)


# ---------------- TQSDK 真实数据 ----------------
class _KlineSubscriptions:
    """一个行情会话内每个键只订阅一次；HTTP刷新只重读已订阅serial。"""
    def __init__(self, api):
        self.api = api
        self.serials = {}

    def get(self, sym, period):
        key = (sym, period)
        if key not in self.serials:
            self.serials[key] = self.api.get_kline_serial(sym, period, data_length_for_period(period))
        return self.serials[key]

    def market_changed(self):
        return any(self.api.is_changing(serial) for serial in self.serials.values())


def _serial_rows(df):
    """Read a subscribed SDK frame without changing its retained history."""
    rows = []
    opens, highs = df["open"].tolist(), df["high"].tolist()
    lows, closes = df["low"].tolist(), df["close"].tolist()
    volumes = df["volume"].tolist() if "volume" in df else [0] * len(closes)
    open_ois = df["open_oi"].tolist() if "open_oi" in df else [0] * len(closes)
    close_ois = df["close_oi"].tolist() if "close_oi" in df else [0] * len(closes)
    timestamps = df["datetime"].tolist()
    for o, h, low, c, v, ooi, coi, stamp in zip(opens, highs, lows, closes, volumes, open_ois, close_ois, timestamps):
        if math.isnan(c):
            continue
        volume = 0 if isinstance(v, float) and math.isnan(v) else int(v)
        oi_delta = 0
        if not (isinstance(coi, float) and math.isnan(coi)) and not (isinstance(ooi, float) and math.isnan(ooi)):
            oi_delta = int(coi - ooi)
        rows.append({"open": float(o), "high": float(h), "low": float(low), "close": float(c),
                     "volume": volume, "oi": oi_delta,
                     "dt": datetime.fromtimestamp(int(stamp) / 1e9, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")})
    return rows


def _cache_workspace_quotes(quotes):
    """Quote-list prices are independent of expensive legacy radar analysis."""
    checked = time.time() * 1000
    snapshot = {}
    for symbol, quote in quotes.items():
        price = getattr(quote, "last_price", None)
        valid = (isinstance(price, (int, float)) and not isinstance(price, bool)
                 and math.isfinite(price) and price > 0)
        stamp = getattr(quote, "datetime", None)
        snapshot[symbol] = {"price": float(price) if valid else None,
                            "at": checked, "bar_time": stamp if isinstance(stamp, str) and stamp else None}
    with _workspace_quote_lock:
        _workspace_live_quotes.clear()
        _workspace_live_quotes.update(snapshot)


def _workspace_tqsdk_session(api, notifications):
    """Production uses the same SDK session, quotes plus requested chart periods.

    Desktop radar/scoring and its state dumps are not workspace dependencies.
    Pump provider notifications on every update; materialize full chart history
    at most once a second so tick bursts cannot monopolize the Python process.
    """
    codes = [preset["symbol"] for preset in symbols.PRESETS]
    quotes = dict(zip(codes, api.get_quote_list(codes)))
    subscriptions = _KlineSubscriptions(api)
    _cache_workspace_quotes(quotes)
    _set_state(connected=True, connection_status="unknown", error=None)
    _set_boot("ready", 100, "真实报价已连接，图表按查看品种加载")
    next_charts = 0.0
    while True:
        # get() reuses each (contract, period); adding a viewer never opens a
        # second SDK session or subscribes large histories for all other codes.
        for symbol in _workspace_market.requested():
            for period in (86400, 1800, 180):
                try:
                    subscriptions.get(symbol, period)
                except Exception:
                    pass  # An unavailable period remains loading/stale.
        try:
            received = api.wait_update(deadline=time.time() + 1)
        except TimeoutError:
            received = False
        changed = bool(received and (any(api.is_changing(quote, ["last_price", "datetime"])
                                        for quote in quotes.values()) or subscriptions.market_changed()))
        _note_feed_poll(received=received, notifications=notifications.get_notifies(), market_changed=changed)
        _cache_workspace_quotes(quotes)
        now = time.monotonic()
        if now >= next_charts:
            _workspace_market.refresh(lambda symbol, period: _serial_rows(subscriptions.get(symbol, period)))
            # Schedule from completion, not from the previous tick: slow history
            # conversion must not create a catch-up loop that starves HTTP work.
            next_charts = time.monotonic() + 1


def _note_feed_poll(received=False, notifications=(), market_changed=False):
    """SDK事件泵存活、业务更新和行情连接通知是三个独立证据。"""
    now = time.time()
    with _state_lock:
        _state["feed_checked_at"] = now
        if received:
            _state["feed_received_at"] = now
        codes = {n.get("code") for n in notifications if n.get("conn_id") == "md"}
        # TqNotify的同批通知顺序无严格保证；断开证据优先，后续行情更新再恢复。
        lost = bool(codes & {2019112910, 2019112911})
        recovered = bool(codes & {2019112901, 2019112902}) or market_changed
        if lost:
            _state.update(connected=False, connection_status="reconnecting", result=None,
                          error="行情连接中断，SDK正在自动恢复")
        elif recovered:
            _state.update(connected=True, connection_status="connected", error=None)


def _tqsdk_loop():
    try:
        from tqsdk import TqApi, TqAuth, TqNotify
    except ImportError:
        _set_boot("error", 0, "未安装 tqsdk")
        _set_state(connected=False, error="未安装 tqsdk，请先 pip install -r requirements.txt")
        return

    try:
        _set_boot("connecting", 25, "正在连接天勤行情…")
        auth = None
        if config.TQ_USER:
            auth = TqAuth(config.TQ_USER, config.TQ_PASSWORD)
        api = TqApi(auth=auth, debug=False, disable_print=True)
        notifications = TqNotify(api)
    except Exception as e:  # noqa: BLE001
        _set_boot("error", 0, f"连接天勤失败：{e}")
        _set_state(connected=False, error=f"连接天勤失败: {e}")
        return

    try:
        # 使用行情源的实际主力映射；联网失败时保留有效缓存/日历回退。
        _set_boot("loading", 35, "正在更新主力合约…")
        try:
            symbols.refresh_from_api(api)
        except Exception as e:
            print(f"主力合约更新失败，沿用已有预设（{type(e).__name__}）", flush=True)
        config.SYMBOL = symbols.rollover_if_expired(config.SYMBOL)
        symbols.apply_symbol_params(config)
        _set_state(symbol=config.SYMBOL)
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
                raise ValueError(
                    f"合约 {config.SYMBOL} 已到期且未找到同品种新主力。"
                    f"请在配置页改选一个当前主力合约（下拉中的品种已自动换月）。"
                )
            if not dt and not has_price:
                raise ValueError("查无此合约（无行情数据）")
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"合约 {config.SYMBOL} 无法识别或已过期。"
                f"请在配置页改成当前主力合约（如 SHFE.ag2510），或核对代码格式。原始信息：{e}"
            )

        if app.config.get("WORKSPACE_PRODUCTION"):
            _workspace_tqsdk_session(api, notifications)
            return

        _set_boot("loading", 60, "正在拉取K线…")
        # K线订阅缓存：(品种,周期)->serial。切换组合/品种/查看多周期时复用，避免重复订阅。
        # 订阅数估算：全局层 12品种×3周期=36 条（聚焦品种的与全局复用同key），
        # 聚焦品种额外只有 30秒触发腿 + 前端查看过的多周期（≤7条）。
        subscriptions = _KlineSubscriptions(api)
        serial = subscriptions.get

        combo = _current_combo()
        focus = config.SYMBOL
        kt = serial(focus, combo["trend"])
        kf = serial(focus, combo["filter"])
        kg = serial(focus, combo["trigger"])
        _set_state(connected=True, connection_status="unknown", error=None, combo=combo["key"])

        to_list = _serial_rows

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
            # 提前登记焦点30m/3m及查看过的周期；warmup不足也不能挡住图表刷新。
            for period in _requested_chart_periods():
                serial(focus, period)
            # Each member can inspect their own contract; never mutate the legacy focus.
            for workspace_symbol in _workspace_market.requested():
                for workspace_period in (86400, 1800, 180):
                    try:
                        serial(workspace_symbol, workspace_period)
                    except Exception:
                        pass  # The unavailable period remains visibly unavailable.
            # deadline超时返回False，不代表断网/休市；SDK自己恢复断开的连接。
            try:
                received = api.wait_update(deadline=time.time() + 5)
            except TimeoutError:
                received = False
            _note_feed_poll(received=received, notifications=notifications.get_notifies(),
                            market_changed=bool(received and subscriptions.market_changed()))
            _refresh_focus_charts(focus, lambda s, p: to_list(serial(s, p)))
            _workspace_market.refresh(lambda s, p: to_list(serial(s, p)))
            if not get_state().get("connected"):
                continue
            kt_list, kf_list, kg_list = to_list(kt), to_list(kf), to_list(kg)
            # 首次要等三周期都有足够数据
            if first and (len(kt_list) < config.MA_SLOW or len(kg_list) < config.MA_FAST):
                # 保留同一会话继续等待/重连，不因辅助组合加载慢关闭正常的图表订阅。
                if time.time() - wait_start > 30:
                    _set_boot("loading", 60,
                              f"仍在等待辅助周期K线（{combo['trend_label']}{len(kt_list)}根/"
                              f"{combo['trigger_label']}{len(kg_list)}根）；图表独立刷新，连接恢复后自动续取")
                continue
            result = resonance.evaluate(kt_list[:-1], kf_list[:-1], kg_list[:-1], _combo_labels(combo), tick=config.PRICE_TICK)
            result["price"] = kg_list[-1]["close"] if kg_list else None
            for period, bars in ((combo["trend"], kt_list), (combo["filter"], kf_list),
                                 (combo["trigger"], kg_list)):
                _cache_outlook_bars(focus, period, bars)
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
            _publish_focus_result(focus, combo, result)
            # 全局层：全部预设品种快照 + 落盘（聚焦品种用三腿结果覆盖）
            _global_pass(lambda s, p: to_list(serial(s, p)),
                         focus_track_a={"direction": result["direction"],
                                        "resonance": result["resonance"]},
                         focus_trigger={"direction": result["direction"],
                                        "trigger_ok": result["legs"]["trigger"]["ok"]})
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
        # 自动换月：用户配置的合约若已不在预设表（说明已到期），
        # 静默换成同品种当前主力，避免每次都死在"合约已到期"。
        config.SYMBOL = symbols.rollover_if_expired(s["symbol"])
    # 按合约从预设表解析 tick 参数（查不到保留 config.py 默认值）
    symbols.apply_symbol_params(config)
    config.MOCK_MODE = bool(s.get("mock_mode", False))
    _set_state(symbol=config.SYMBOL, mock=config.MOCK_MODE)


def start_data_thread():
    """启动行情线程 + 基本面后台线程（各只启动一次）。"""
    global _data_thread
    with _thread_lock:
        if _data_thread and _data_thread.is_alive():
            return
        target = _mock_loop if config.MOCK_MODE else _tqsdk_loop
        _data_thread = threading.Thread(target=target, daemon=True)
        _data_thread.start()
    fundamentals.start()


# ---------------- 路由 ----------------
@app.route("/")
@app.route("/workspace")
def workspace_index():
    return render_template("workspace.html")


@app.route("/monitor")
def index():
    # 未配置 → 去配置页
    if not user_settings.is_configured():
        return redirect(url_for("setup"))
    return render_template("index.html",
                           symbol=config.SYMBOL,
                           symbol_name=symbols.name_of(config.SYMBOL) or "",
                           presets=symbols.PRESETS,
                           poll_ms=config.POLL_INTERVAL_MS)


@app.route("/board-demo")
def plan_board_demo():
    return render_template("plan_board_demo.html")


@app.route("/api/board-demo/plans/<symbol>", methods=["GET", "PUT"])
def api_board_demo_plan(symbol):
    try:
        if request.method == "GET":
            return jsonify(status="ok", plan=_board_demo_store.get(symbol))
        if request.content_length and request.content_length > 2 * 1024 * 1024:
            return jsonify(status="error", message="计划变更过大"), 413
        payload = request.get_json(silent=True)
        plan, conflicts = _board_demo_store.apply(symbol, payload, request.headers.get("X-Board-Client", ""))
        if conflicts:
            return jsonify(status="conflict", message="服务器对象已被其他人修改", plan=plan, conflicts=conflicts), 409
        return jsonify(status="ok", plan=plan)
    except ValueError as exc:
        return jsonify(status="error", message=str(exc)), 400
    except OSError:
        return jsonify(status="error", message="共享计划保存失败"), 500


@app.route("/api/board-demo/events/<symbol>")
def api_board_demo_events(symbol):
    def events():
        subscriber = _board_demo_store.subscribe(symbol)
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield "event: plan\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            _board_demo_store.unsubscribe(symbol, subscriber)

    try:
        _board_demo_store.get(symbol)
    except ValueError as exc:
        return jsonify(status="error", message=str(exc)), 400
    return Response(stream_with_context(events()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
    # POST：保存配置 → 应用 → 启动行情线程（symbol 保留兼容：空则取预设第一个）
    data = request.get_json(force=True, silent=True) or {}
    user_settings.save_settings({
        "tq_user": (data.get("tq_user") or "").strip(),
        "tq_password": (data.get("tq_password") or "").strip(),
        "symbol": (data.get("symbol") or "").strip() or symbols.PRESETS[0]["symbol"],
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
    if not symbols.find(sym) and sym != config.SYMBOL:
        return jsonify({"ok": False, "error": "未知品种"}), 400
    with _symbol_lock:
        _symbol_request = sym
    return jsonify({"ok": True, "symbol": sym})


@app.route("/api/overview")
def api_overview():
    """总览页卡片数据：全部预设品种快照（数据未就绪的品种给占位卡）。"""
    with _overview_lock:
        ov = dict(_overview)
    cards = [{"symbol": p["symbol"], **(ov.get(p["symbol"]) or _collecting_entry(p["name"], p["symbol"]))}
             for p in symbols.PRESETS]
    # 聚焦品种是预设外的自定义合约时，也补一张卡
    known = [p["symbol"] for p in symbols.PRESETS]
    if config.SYMBOL not in known and config.SYMBOL in ov:
        cards.append({"symbol": config.SYMBOL, **ov[config.SYMBOL]})
    state = get_state()
    connected = state.get("connected", False)
    for card in cards:
        with _outlook_lock:
            data = _outlook_data.get((card["symbol"], 1800))
            small = _outlook_data.get((card["symbol"], 180))
        primary_freshness = _chart_freshness(data, 1800, state=state)
        small_freshness = _chart_freshness(small, 180, state=state)
        display_available = primary_freshness["status"] in ("receiving", "latest_available")
        realtime = primary_freshness["realtime"] and small_freshness["realtime"]
        # Quiet but actively checked history remains useful for preparation. The legacy
        # stale flag continues to block trading; it no longer means "hide the structure".
        card.update(freshness=primary_freshness, confirmation_freshness=small_freshness,
                    display_available=display_available, realtime=realtime, stale=not realtime,
                    view_mode="live" if realtime else "preparation" if display_available else "unavailable")
        if display_available:
            # Use the same immutable 30m snapshot for the displayed reference price and time.
            card["price"] = data["bars"][-1]["close"]
            card["price_bar_time"] = primary_freshness["last_bar_time"]
            # The slower auxiliary warmup must not hide otherwise usable 30m history.
            if not card.get("outlook") or card["outlook"].get("period") != 1800:
                card["outlook"] = _outlook_summary(card["symbol"], data["bars"])
        if not realtime:
            message = ("最新可用结构可用于夜盘 / 次日预案；开盘后重新核对行情、触发与风险"
                       if display_available else primary_freshness["label"])
            decision = _decision_standby(message)
            decision.update(plan_status="preparation" if display_available else "unavailable",
                            label="预案观察 · 实时确认暂停" if display_available else primary_freshness["label"], max_lots=0)
            card.update(decision=decision, lamp="gray")
    return jsonify({"symbols": cards, "current": config.SYMBOL, "connected": connected,
                    "generation": get_state().get("generation", 0)})


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
    sym = request.args.get("symbol", config.SYMBOL)
    identity = {"symbol": sym, "generation": get_state().get("generation", 0)}
    try:
        period = int(request.args.get("period", "0"))
    except ValueError:
        period = 0
    if period not in config.EXTRA_KLINE_SECONDS:
        return jsonify({"status": "error", "error": "不支持的周期"}), 400
    identity["period"] = period
    if sym != config.SYMBOL:
        return jsonify(status="loading", freshness=_chart_freshness(None, period), **identity)
    with _extra_lock:
        _extra_requests.add(period)
        data = _extra_data.get(period)
    if data and data.get("symbol") != sym:
        data = None
    freshness = _chart_freshness(data, period)
    identity.update(freshness=freshness, refresh_requested=request.args.get("refresh") == "1")
    if data and data.get("symbol") == sym:
        if freshness["status"] in ("stale", "disconnected"):
            return jsonify(status="error", message="行情刷新中断", **identity)
        return jsonify(status="ok", bars=data["bars"], updated=data["updated"],
                       market_updated=data.get("market_updated", 0), realtime=freshness["realtime"], **identity)
    return jsonify(status="loading", **identity)


@app.route("/api/double_bottom")
def api_double_bottom():
    """轨道B 双底评分：对指定周期K线跑评分（复用多周期懒加载订阅缓存）。"""
    sym = request.args.get("symbol", config.SYMBOL)
    identity = {"symbol": sym, "generation": get_state().get("generation", 0)}
    try:
        period = int(request.args.get("period", "0"))
    except ValueError:
        period = 0
    if period not in config.EXTRA_KLINE_SECONDS:
        return jsonify({"status": "error", "error": "不支持的周期"}), 400
    identity["period"] = period
    if sym != config.SYMBOL:
        return jsonify(status="loading", **identity)
    with _extra_lock:
        _extra_requests.add(period)   # 顺便登记懒加载，数据线程会开始订阅该周期
        data = _extra_data.get(period)
    identity["freshness"] = _chart_freshness(data if data and data.get("symbol") == sym else None, period)
    if not data or data.get("symbol") != sym:
        return jsonify(status="loading", **identity)
    if identity["freshness"]["status"] in ("stale", "disconnected"):
        return jsonify(status="error", message="行情刷新中断", **identity)
    bars = (data.get("full") or data["bars"])[:-1]
    if len(bars) < 40:
        return jsonify(status="insufficient", message=f"完成K线不足（当前{len(bars)}根，需≥40根）", **identity)
    preset = symbols.find(sym)
    result = double_bottom.evaluate(bars, tick=preset["tick"] if preset else config.PRICE_TICK)
    return jsonify(status="ok", updated=data["updated"], result=result, **identity)


@app.route("/api/outlook")
def api_outlook():
    """阶段自适应条件推演；完成K线推进结构，盘中现价只更新路径。"""
    sym = (request.args.get("symbol") or config.SYMBOL).strip()
    try:
        period = int(request.args.get("period", "1800"))
    except ValueError:
        period = 0
    identity = {"symbol": sym, "period": period}
    if period not in OUTLOOK_PERIODS:
        return jsonify(status="error", message="不支持的推演周期", **identity), 400
    preset = symbols.find(sym)
    if not preset and sym != config.SYMBOL:
        return jsonify(status="error", message="未知品种", **identity), 400
    with _outlook_lock:
        data = _outlook_data.get((sym, period))
    if sym == config.SYMBOL:
        with _extra_lock:
            _extra_requests.add(period)
    freshness = _chart_freshness(data, period)
    identity.update(freshness=freshness,
                    refresh_requested=sym == config.SYMBOL and request.args.get("refresh") == "1")
    if not data:
        message = "正在加载该周期K线…" if sym == config.SYMBOL else "请先聚焦该品种以加载此周期"
        return jsonify(status="loading", message=message, **identity)
    if freshness["status"] in ("stale", "disconnected"):
        return jsonify(status="error", stale=True, updated=data["updated"],
                       message="行情刷新中断，推演已暂停", **identity)
    with _outlook_lock:
        # 分析与图表使用同一份不可变缓存引用；并发新快照留给下一轮请求。
        chart_bars = data["bars"][-120:]
        analysis = _tracked_outlook(sym, chart_bars, period)
    market_updated = data.get("market_updated", data["updated"])
    return jsonify(status=analysis["status"], updated=data["updated"], market_updated=market_updated,
                   message=analysis.get("message"),
                   realtime=freshness["realtime"],
                   bars=chart_bars, analysis=analysis, **identity)


@app.route("/api/entry_check")
def api_entry_check():
    bundle = _trade_bundle(request.args.get("symbol", config.SYMBOL), request.args.get("mode", "confirmed"))
    if bundle["status"] != "ok":
        return jsonify(bundle)
    plan = bundle["plan"]
    return jsonify({**bundle, **_plan_decision(bundle), "mode_label": "30分主结构 / 3分确认",
                    "quadrant_name": plan["label"], "stage": plan["setup"], "stage_name": plan["label"],
                    "entry_point_ref": None, "scores_detail": [],
                    "ladder": {"price": plan["entry"], "stop": plan["stop"], "target1": plan["target"],
                               "target2": None, "L1": None, "L2": None, "neck": None}})


@app.route("/api/trade_plan")
def api_trade_plan():
    return jsonify(_trade_bundle(request.args.get("symbol", config.SYMBOL),
                                 request.args.get("mode", "confirmed"), record=True))


def _trading_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("请求须包含JSON对象")
    return payload


@app.route("/api/trading/settings", methods=["GET", "POST"])
def api_trading_settings():
    sym = request.args.get("symbol", config.SYMBOL)
    try:
        payload = _trading_payload() if request.method == "POST" else {}
        sym = payload.get("symbol", sym)
        if not symbols.find(sym):
            raise ValueError("未知品种")
        settings = _trading_store.update_settings(payload, sym) if request.method == "POST" else _trading_store.settings(sym)
        return jsonify(ok=True, symbol=sym, settings=settings)
    except (ValueError, OSError) as exc:
        return jsonify(ok=False, symbol=sym, error=str(exc), message=str(exc)), 400


@app.route("/api/trading/idea", methods=["POST"])
def api_trading_idea():
    sym = config.SYMBOL
    try:
        payload = _trading_payload()
        sym = payload.get("symbol", sym)
        if not symbols.find(sym):
            raise ValueError("未知品种")
        return jsonify(ok=True, symbol=sym, idea=_trading_store.new_idea(sym))
    except (ValueError, OSError) as exc:
        return jsonify(ok=False, symbol=sym, error=str(exc), message=str(exc)), 400


@app.route("/api/positions", methods=["GET", "POST"])
def api_positions():
    sym = request.args.get("symbol", config.SYMBOL)
    try:
        payload = _trading_payload() if request.method == "POST" else {}
        sym = payload.get("symbol", sym)
        preset = symbols.find(sym)
        if not preset:
            raise ValueError("未知品种")
        if _trading_store.load_error:
            raise ValueError(_trading_store.load_error)
        if request.method == "POST":
            _trading_store.register(payload, preset)
        view = _trading_store.positions_view(sym, _registered_prices())
        risk = trading.risk_for_plan({}, _trading_store.settings(sym), preset,
                                    _trading_store.open_positions(), _trading_store.idea(sym))
        warning = "该计划登记风险已超过上限，请核对交易端持仓" if risk["open_risk"] + risk["realized_loss"] > risk["budget"] else ""
        return jsonify(ok=True, symbol=sym, warning=warning, **view)
    except (ValueError, OSError) as exc:
        return jsonify(ok=False, symbol=sym, error=str(exc), message=str(exc)), 400


@app.route("/api/positions/<position_id>", methods=["POST"])
def api_position_update(position_id):
    sym = config.SYMBOL
    try:
        payload = _trading_payload()
        sym = payload.get("symbol", sym)
        result = _trading_store.update_position(position_id, payload)
        sym = result["symbol"]
        return jsonify(ok=True, symbol=sym, **_trading_store.positions_view(sym, _registered_prices()))
    except (ValueError, OSError) as exc:
        return jsonify(ok=False, symbol=sym, error=str(exc), message=str(exc)), 400


@app.route("/api/trading/journal")
def api_trading_journal():
    sym = request.args.get("symbol", config.SYMBOL)
    return jsonify(ok=not bool(_trading_store.load_error), symbol=sym, signals=_trading_store.signals(sym),
                   **_trading_store.positions_view(sym, _registered_prices()))


@app.route("/api/fundamentals")
def api_fundamentals():
    """基本面：?symbol=X 该品种的库存/基差/席位动向（缺省聚焦品种）。不阻塞，读后台缓存。"""
    sym = (request.args.get("symbol") or config.SYMBOL).strip()
    return jsonify({"symbol": sym, "meta": fundamentals.meta(), **fundamentals.get(sym)})


def _workspace_symbols_provider():
    return [{key: preset[key] for key in ("symbol", "name", "tick", "tick_value") if key in preset}
            for preset in symbols.PRESETS]


def _workspace_market_provider(symbol):
    preset = symbols.find(symbol)
    if not preset:
        raise ValueError("未知实际合约")
    state = get_state()
    market = _workspace_market.snapshot(symbol, preset, state, simulated=config.MOCK_MODE)
    market["setup_required"] = state.get("boot_phase") == "need_config"
    if market["setup_required"]:
        market["message"] = "主机尚未配置行情来源 · 主机可打开 /setup 完成配置，协作成员无需行情账号"
    return market


def _workspace_quotes_provider():
    """Read the existing radar cache; do not open extra large chart subscriptions."""
    result = []
    state = get_state()
    if app.config.get("WORKSPACE_PRODUCTION"):
        with _workspace_quote_lock:
            cached = {key: dict(value) for key, value in _workspace_live_quotes.items()}
        return [{"symbol": preset["symbol"], "tick": preset.get("tick"),
                 "price": cached.get(preset["symbol"], {}).get("price"),
                 "at": cached.get(preset["symbol"], {}).get("at"),
                 "bar_time": cached.get(preset["symbol"], {}).get("bar_time"),
                 "source": "live", "connected": bool(state.get("connected"))}
                for preset in symbols.PRESETS]
    for preset in symbols.PRESETS:
        symbol = preset["symbol"]
        with _outlook_lock:
            data = _outlook_data.get((symbol, 180)) or _outlook_data.get((symbol, 1800))
            rows = (data or {}).get("bars") or []
            last = dict(rows[-1]) if rows else None
            checked = (data or {}).get("updated")
        result.append({"symbol": symbol, "price": last.get("close") if last else None,
                       "tick": preset.get("tick"), "at": checked * 1000 if checked else None,
                       "source": "mock" if config.MOCK_MODE else "live",
                       "connected": bool(state.get("connected")), "bar_time": last.get("dt") if last else None})
    return result


workspace_api.register_workspace(app, _workspace_store, _workspace_market_provider,
                                 _workspace_symbols_provider, workspace_rules.rule_manifest,
                                 workspace_rules.evaluate_plan, _workspace_quotes_provider)


def _open_browser_later():
    time.sleep(1.0)
    try:
        browser_host = "127.0.0.1" if config.HOST == "0.0.0.0" else config.HOST
        webbrowser.open(f"http://{browser_host}:{config.PORT}")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    if "--share" in sys.argv:
        config.HOST = "0.0.0.0"
    apply_user_settings()
    # 已配置 → 直接起行情线程；未配置 → 等用户在配置页提交后再起
    if user_settings.is_configured():
        start_data_thread()
    else:
        _set_boot("need_config", 0, "首次使用，请先填写天勤账号")
    threading.Thread(target=_open_browser_later, daemon=True).start()
    print(f"面板已启动: http://{config.HOST}:{config.PORT}  (MOCK_MODE={config.MOCK_MODE})")
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)
