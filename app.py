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


def _leg_marks(legs):
    """三腿状态缩成内嵌小标记：趋势✓ 过滤✓ 触发✗。"""
    def mark(key):
        return "✓" if legs.get(key, {}).get("ok") else "✗"
    return f"趋势{mark('trend')} 过滤{mark('filter')} 触发{mark('trigger')}"


def _rr_line(room_ticks, stop_ticks, stop_src):
    """价值层文案：掩体空间/结构止损 → RR。返回 (detail, ok)。"""
    if room_ticks is None:
        return f"止损{stop_src}；掩体无法估算（缺压力/支撑位）", False
    if not stop_ticks or stop_ticks <= 0:
        return f"掩体 {room_ticks} 跳；止损距离异常", False
    rr = room_ticks / stop_ticks
    return (f"掩体 {room_ticks} 跳 / 止损 {stop_ticks} 跳（{stop_src}）≈ RR {rr:.1f}",
            rr >= config.MIN_RR)


def _focus_track_b():
    """聚焦品种的轨道B摘要（全局层每轮已算好，直接取用）；未就绪返回 None。"""
    with _overview_lock:
        e = _overview.get(config.SYMBOL)
    if not e or e["track_b"].get("score") is None:
        return None
    return e


def _entry_check():
    """
    "我要开仓" 前的盘面自检：按交易风格分流 + 三层漏斗 + 四档结论。
    超短模式（scalp 组合）：时机(三频共振)/位置/价值 三层否决 + 轨道B加减分。
    波段模式（swing/position 组合）：结构(轨道B)/位置(强位)/价值(轨道B计划) 三层否决，
    三频共振退出否决项，只作入场时机参考。
    返回 JSON 含 "mode": "scalp"|"swing"，checks 每层一行（veto=否决层 / ref=参考层）。
    """
    st = get_state()
    res = st.get("result")
    combo = _current_combo()
    mode = "scalp" if combo["key"] == "scalp" else "swing"
    mode_label = ("超短模式" if mode == "scalp" else "波段模式") + \
        f"（{combo['name']} {combo['trend_label']}/{combo['filter_label']}/{combo['trigger_label']}）"
    if not res:
        return {"verdict": "wait", "mode": mode, "mode_label": mode_label,
                "title": "🟠 黄灯：无数据", "checks": [],
                "summary": "未取到行情，无法检查。"}
    if mode == "scalp":
        return _entry_check_scalp(res, mode_label)
    return _entry_check_swing(res, mode_label)


def _entry_check_scalp(res, mode_label):
    """超短模式：时机/位置/价值三层一票否决，轨道B共振层只加减分。"""
    direction = res.get("direction", "none")
    resonance = res.get("resonance", False)
    price = res.get("price")
    legs = res.get("legs", {})
    sr = (res.get("sr") or {}).get("trigger") or (res.get("sr") or {}).get("trend") or {}
    checks = []

    # 1. 时机层（一票否决）：三频共振（三腿缩成内嵌小标记，不再单列三行）
    checks.append({"ok": bool(resonance), "layer": "veto", "name": "时机 · 三频共振",
                   "detail": f"{_leg_marks(legs)}（{'三腿就位' if resonance else '未全部就位'}）"})

    # 2. 位置层（一票否决）：逼近关键位警告
    warn = _proximity_warning(price, sr)
    checks.append({"ok": warn is None, "layer": "veto", "name": "位置 · 远离压力/支撑",
                   "detail": warn["text"] if warn else "现价未贴近关键位"})

    # 3. 价值层（一票否决）：盈亏比，止损取结构位（多头=触发周期最近摆动低点，空头镜像）；
    #    找不到结构位时回退固定跳数并在文案注明
    near_res, res_ticks, near_sup, sup_ticks = _nearest_levels(price, sr) if (price and sr) else (None, None, None, None)
    rr_txt, rr_ok = "方向不明，无法估算", False
    tick = config.PRICE_TICK or 1.0
    if direction == "long":
        if near_sup:
            stop_ticks = max(1, round((price - near_sup["price"]) / tick))
            stop_src = f"结构位 {near_sup['price']}（摆动低点）"
        else:
            stop_ticks = config.DEFAULT_STOP_TICKS
            stop_src = f"默认{config.DEFAULT_STOP_TICKS}跳（未找到结构位）"
        rr_txt, rr_ok = _rr_line(res_ticks, stop_ticks, stop_src)
    elif direction == "short":
        if near_res:
            stop_ticks = max(1, round((near_res["price"] - price) / tick))
            stop_src = f"结构位 {near_res['price']}（摆动高点）"
        else:
            stop_ticks = config.DEFAULT_STOP_TICKS
            stop_src = f"默认{config.DEFAULT_STOP_TICKS}跳（未找到结构位）"
        rr_txt, rr_ok = _rr_line(sup_ticks, stop_ticks, stop_src)
    checks.append({"ok": rr_ok, "layer": "veto", "name": f"价值 · 盈亏比≥{config.MIN_RR}",
                   "detail": rr_txt})

    # 4. 共振层（加减分，非否决）：轨道B同向≥55 升档；背离或<40 降档
    tb = _focus_track_b()
    tb_score = tb["track_b"]["score"] if tb else None
    tb_dir = tb["track_b"]["direction"] if tb else None
    if tb is None:
        checks.append({"ok": None, "layer": "ref", "name": "共振 · 轨道B",
                       "detail": "轨道B数据收集中，不参与定档"})
        tb_adj = 0
    elif tb_score >= 55 and direction != "none" and tb_dir == direction:
        checks.append({"ok": True, "layer": "ref", "name": "共振 · 轨道B",
                       "detail": f"同向加持（{tb_score}分）→ 升档"})
        tb_adj = 1
    elif tb_score < 40 or (tb_dir not in (None, "wait") and direction != "none" and tb_dir != direction):
        checks.append({"ok": False, "layer": "ref", "name": "共振 · 轨道B",
                       "detail": f"背离/低分（{tb_score}分 {tb_dir or '--'}）→ 降档"})
        tb_adj = -1
    else:
        checks.append({"ok": None, "layer": "ref", "name": "共振 · 轨道B",
                       "detail": f"中性（{tb_score}分 {tb_dir or '--'}），不加减"})
        tb_adj = 0

    # 定档
    if warn is not None:
        verdict, title = "stop", "🔴 红灯：贴近关键位（追单陷阱）"
        summary = warn["text"] + " 等回踩确认，别追当根。"
    else:
        fails = []
        if not resonance:
            fails.append("时机·三频共振")
        if not rr_ok:
            fails.append("价值·盈亏比")
        if fails:
            verdict, title = "wait", "🟠 黄灯：条件不足，建议观望"
            summary = "未通过：" + "、".join(fails) + "。超短模式任一否决层不满足都不该进。"
        elif tb_adj < 0:
            verdict, title = "wait", "🟠 黄灯：轨道B背离，降档观望"
            summary = "否决层虽全过，但轨道B背离/低分，逆势超短胜率差。"
        elif tb_adj > 0:
            verdict, title = "go-strong", "🟢 强绿灯：条件齐备，重点看"
            summary = "共振就位、位置安全、结构止损盈亏比达标，且轨道B同向加持。仍须你手动确认结构止损位。"
        else:
            verdict, title = "go-weak", "🟡 弱绿灯：可进，建议仓位减半"
            summary = "否决层全过但轨道B未同向确认，建议降半仓试探。"
    return {"verdict": verdict, "title": title, "mode": "scalp", "mode_label": mode_label,
            "direction": direction, "price": price, "checks": checks, "summary": summary}


def _entry_check_swing(res, mode_label):
    """波段模式：结构(轨道B)/位置(强位)/价值(轨道B计划) 三层否决，三频共振仅参考。"""
    price = res.get("price")
    legs = res.get("legs", {})
    resonance = res.get("resonance", False)
    tick = config.PRICE_TICK or 1.0
    tb = _focus_track_b()
    checks = []

    # 1. 结构层（一票否决）：轨道B评分与方向（≥75 强势通过；55-74 弱档通过；<55 否决）
    tb_score = tb["track_b"]["score"] if tb else None
    direction = tb["track_b"]["direction"] if tb else "wait"
    if tb is None:
        s_ok, s_strong, s_txt = False, False, "轨道B数据收集中，稍候再试"
    elif tb_score >= config.DB_SCORE_GO:
        s_ok, s_strong = True, True
        s_txt = f"{tb_score}分 ≥{config.DB_SCORE_GO}，方向{'做多' if direction == 'long' else '做空'}（强势）"
    elif tb_score >= config.DB_SCORE_WAIT:
        s_ok, s_strong = True, False
        s_txt = f"{tb_score}分（55-74 中性），方向{'做多' if direction == 'long' else '做空' if direction == 'short' else '不明'}"
    else:
        s_ok, s_strong = False, False
        s_txt = f"{tb_score}分 <{config.DB_SCORE_WAIT}，结构未成立"
    checks.append({"ok": s_ok, "layer": "veto", "name": "结构 · 轨道B评分", "detail": s_txt})

    # 2. 位置层（一票否决）：距轨道B强位（score≥70）过近不追
    pos_ok, pos_txt, pos_stop = True, "现价距强支撑/压力有安全距离", False
    if tb and direction != "wait" and price:
        tbd = tb["track_b"]
        pool, sign = (tbd["resistance"], 1) if direction == "long" else (tbd["support"], -1)
        strong = [l for l in pool if l["score"] >= config.DB_LEVEL_STRONG
                  and (l["price"] - price) * sign > 0]
        if strong:
            near = min(strong, key=lambda l: abs(l["price"] - price))
            dist = round(abs(near["price"] - price) / tick)
            side = "强压力" if direction == "long" else "强支撑"
            if dist <= config.PROXIMITY_TICKS:
                pos_ok, pos_stop = False, True
                pos_txt = f"距{side} {near['price']}（强度{near['score']}）仅 {dist} 跳，追单风险区"
            else:
                pos_txt = f"距{side} {near['price']}（强度{near['score']}）尚有 {dist} 跳"
    checks.append({"ok": pos_ok, "layer": "veto", "name": "位置 · 强位距离", "detail": pos_txt})

    # 3. 价值层（一票否决）：轨道B plan 的 entry/stop/target1 算盈亏比
    entry, stop, t1 = (tb.get("entry"), tb.get("stop"), tb.get("target1")) if tb else (None, None, None)
    if entry and stop and t1 and direction != "wait":
        risk, reward = abs(entry - stop), abs(t1 - entry)
        rr = reward / risk if risk else 0
        rr_ok = rr >= config.MIN_RR
        rr_txt = f"计划 入场{entry} 止损{stop} 目标{t1} ≈ RR {rr:.1f}"
    else:
        rr_ok, rr_txt = False, "轨道B未给出交易计划（评分未达标或缺目标位）"
    checks.append({"ok": rr_ok, "layer": "veto", "name": f"价值 · 盈亏比≥{config.MIN_RR}",
                   "detail": rr_txt})

    # 4. 微调层（参考，不影响档位）：三频共振只作入场时机提示
    ref_txt = "超短共振已就位，可直接进" if resonance else "超短共振未就位，可等回调或分批"
    checks.append({"ok": None, "layer": "ref", "name": "时机 · 超短共振（参考）",
                   "detail": f"{_leg_marks(legs)} — {ref_txt}"})

    # 定档
    if pos_stop:
        verdict, title = "stop", "🔴 红灯：贴近关键位（追单陷阱）"
        summary = pos_txt + "。等回踩确认，别追。"
    elif not (s_ok and pos_ok and rr_ok):
        fails = []
        if not s_ok:
            fails.append("结构·轨道B")
        if not pos_ok:
            fails.append("位置·强位距离")
        if not rr_ok:
            fails.append("价值·盈亏比")
        verdict, title = "wait", "🟠 黄灯：条件不足，建议观望"
        summary = "未通过：" + "、".join(fails) + "。波段模式以轨道B结构为准，三频共振不卡否决。"
    elif s_strong:
        verdict, title = "go-strong", "🟢 强绿灯：条件齐备，重点看"
        summary = "轨道B结构强势、位置安全、计划盈亏比达标。止损止盈按轨道B计划执行。"
    else:
        verdict, title = "go-weak", "🟡 弱绿灯：可进，建议仓位减半"
        summary = "否决层全过但轨道B评分中性（55-74），建议降半仓试探。"
    return {"verdict": verdict, "title": title, "mode": "swing", "mode_label": mode_label,
            "direction": direction, "price": price, "checks": checks, "summary": summary}

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
