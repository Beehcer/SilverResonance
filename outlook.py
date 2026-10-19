"""短期走势的条件推演。纯函数，不下单、不使用未来数据或未完成 K 线确认结构。

最后一根始终视为未完成 K 线，仅用于显示现价。最近八根已完成 K 线为
观察段；其之前最多六十根为参考段，边界和 ATR 缓冲均固定于参考段，
观察段之后的波动不会反向改写本次分析中较早的突破事件。
输出中的三条路径是有条件的示意，不是价格预测、概率或交易指令。
"""

import math
import copy
import hashlib
from datetime import datetime, timedelta, timezone

from market_structure import build_market_structure


MIN_COMPLETED = 40
LOOKBACK = 120
OBSERVATION = 8
REFERENCE = 60
PIVOT_CONFIRM = 2


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _clean_bar(bar):
    if not isinstance(bar, dict):
        return None
    values = [bar.get(k) for k in ("open", "high", "low", "close")]
    if not all(_number(v) and v > 0 for v in values):
        return None
    o, h, low, c = map(float, values)
    if low > min(o, c) or h < max(o, c) or low > h:
        return None
    return {"open": o, "high": h, "low": low, "close": c}


def _rounded(value):
    return round(float(value), 8)


def _fmt(value):
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _period_label(period):
    if not _number(period) or period <= 0:
        return "本周期"
    if period % 86400 == 0:
        return f"{period / 86400:g}日"
    if period % 3600 == 0:
        return f"{period / 3600:g}小时"
    if period % 60 == 0:
        return f"{period / 60:g}分"
    return f"{period:g}秒"


def _pivots(bars, start):
    """左右各两根确认；相同价位平台只取第一次出现的极值。"""
    result = []
    m = PIVOT_CONFIRM
    for i in range(m, len(bars) - m):
        left, right = bars[i - m:i], bars[i + 1:i + m + 1]
        h, low = bars[i]["high"], bars[i]["low"]
        if h > max(b["high"] for b in left) and h >= max(b["high"] for b in right):
            result.append({"index": start + i, "price": _rounded(h), "kind": "high"})
        if low < min(b["low"] for b in left) and low <= min(b["low"] for b in right):
            result.append({"index": start + i, "price": _rounded(low), "kind": "low"})
    return result


def _atr(bars):
    trs = [max(b["high"] - b["low"], abs(b["high"] - prev["close"]),
               abs(b["low"] - prev["close"]))
           for prev, b in zip(bars, bars[1:])]
    return sum(trs[-14:]) / min(14, len(trs))


def _touch_count(bars, price, key, buffer):
    """连续贴边的多根 K 线只算一次触碰，避免把趋势误标为反复震荡。"""
    count, touching = 0, False
    for bar in bars:
        now = abs(bar[key] - price) <= buffer
        if now and not touching:
            count += 1
        touching = now
    return count


def _empty(price=None, reason="至少需要 40 根完整且有效的已完成 K 线（另加一根当前 K 线）"):
    return {
        "status": "insufficient",
        "state": {"key": "insufficient", "label": "数据不足", "bias": "neutral", "summary": reason},
        "price": price, "atr": None, "buffer": None,
        "levels": {"support": None, "resistance": None, "upper_target": None, "lower_target": None},
        "range": {"start_index": None, "end_index": None, "low": None, "high": None,
                  "kind": "reference", "label": "等待足够的已完成 K 线"},
        "pivots": [], "events": [], "scenarios": [],
        "rules": {"basis": "最后一根仅显示现价，不确认结构", "confirmation": "等待已完成 K 线",
                  "lookback": f"至少 {MIN_COMPLETED} 根已完成 K 线"},
    }


def build_outlook(bars, tick=1.0, period=900):
    """返回可解释的走势状态和三条条件路径；不修改输入或任何全局配置。

    index 均相对调用方传入的 bars。缺失/无效 OHLC 不会被静默跨越；
    volume、oi、dt 不参与本模型，允许缺失。调用方须按时间升序传入。
    """
    if not isinstance(bars, (list, tuple)) or not bars:
        return _empty()
    live_price = bars[-1].get("close") if isinstance(bars[-1], dict) else None
    live_price = float(live_price) if _number(live_price) and live_price > 0 else None
    if len(bars) - 1 < MIN_COMPLETED:
        return _empty(live_price)
    start = max(0, len(bars) - 1 - LOOKBACK)
    completed = [_clean_bar(b) for b in bars[start:-1]]
    if any(b is None for b in completed):
        return _empty(live_price, "已完成 K 线含缺失或无效 OHLC，暂不跨越缺口推演")
    price = live_price if live_price is not None else completed[-1]["close"]
    tick = float(tick) if _number(tick) and tick > 0 else 1.0
    split = len(completed) - OBSERVATION
    ref_start = max(0, split - REFERENCE)
    ref = completed[ref_start:split]
    # 观察段开始前固定尺度，禁止用稍后观察棒的波动抹掉较早的越界事件。
    atr = max(_atr(ref), tick)
    buffer = max(tick, math.ceil(atr * 0.2 / tick - 1e-12) * tick)
    high, low = max(b["high"] for b in ref), min(b["low"] for b in ref)
    width = high - low
    # 平直行情或最小跳动大于结构宽度时，不能把两个边界当成可区分的区间。
    usable_range = width > 2 * buffer
    path_length = sum(abs(b["close"] - a["close"]) for a, b in zip(ref, ref[1:]))
    er = abs(ref[-1]["close"] - ref[0]["close"]) / path_length if path_length else 0.0
    is_range = (usable_range and er < 0.45 and width <= 12 * atr
                and _touch_count(ref, high, "high", buffer) >= 2
                and _touch_count(ref, low, "low", buffer) >= 2)
    pivots = _pivots(completed, start)
    upper_gate, lower_gate = high + buffer, low - buffer
    events = []
    state = "inside"
    breakout_index = None
    failed_index = None
    consecutive = 0

    def event(i, text, kind):
        events.append({"index": start + i, "price": _rounded(completed[i]["close"]),
                       "text": text, "kind": kind})

    def breakout(i, up):
        event(i, f"收盘{'站上' if up else '跌破'} {_fmt(upper_gate if up else lower_gate)}",
              "up" if up else "down")
        return "up_break" if up else "down_break", i, 1

    for i in range(split, len(completed)):
        bar, c = completed[i], completed[i]["close"]
        if state in ("up_break", "up_hold", "up_extension"):
            if c < high - buffer:
                event(i, f"上破后收回 {_fmt(high - buffer)} 下方", "neutral")
                state, failed_index, consecutive = "up_failed", i, 0
            else:
                consecutive = consecutive + 1 if c > upper_gate else 0
                if i > breakout_index and high - buffer <= bar["low"] <= upper_gate and c > upper_gate:
                    if state != "up_hold":
                        event(i, f"回踩 {_fmt(high)} 附近后收盘守住", "up")
                    state = "up_hold"
                elif consecutive >= 2 and c >= high + max(2 * atr, width * 0.5):
                    state = "up_extension"
                elif c <= upper_gate or state == "up_extension":
                    # 延伸必须仍远离边界；回踩守住也必须当前仍收在门槛外。
                    state = "up_break"
        elif state in ("down_break", "down_hold", "down_extension"):
            if c > low + buffer:
                event(i, f"下破后收回 {_fmt(low + buffer)} 上方", "neutral")
                state, failed_index, consecutive = "down_failed", i, 0
            else:
                consecutive = consecutive + 1 if c < lower_gate else 0
                if i > breakout_index and lower_gate <= bar["high"] <= low + buffer and c < lower_gate:
                    if state != "down_hold":
                        event(i, f"反抽 {_fmt(low)} 附近后收盘受阻", "down")
                    state = "down_hold"
                elif consecutive >= 2 and c <= low - max(2 * atr, width * 0.5):
                    state = "down_extension"
                elif c >= lower_gate or state == "down_extension":
                    state = "down_break"
        if state in ("inside", "up_failed", "down_failed"):
            if c > upper_gate:
                state, breakout_index, consecutive = breakout(i, True)
            elif c < lower_gate:
                state, breakout_index, consecutive = breakout(i, False)
            elif failed_index is not None and i - failed_index >= 3:
                state = "inside"

    last_close = completed[-1]["close"]
    extension = max(width, 2 * atr, 4 * tick)
    extension_basis = "前段高度的参考延伸" if extension == width else "2倍ATR或4跳的参考延伸"
    upper_pivots = [p["price"] for p in pivots
                    if p["kind"] == "high" and p["price"] > max(high, last_close) + buffer]
    lower_pivots = [p["price"] for p in pivots
                    if p["kind"] == "low" and p["price"] < min(low, last_close) - buffer]
    if upper_pivots:
        upper_target, upper_basis = min(upper_pivots), "此前已确认摆动高点"
    else:
        steps = max(1, math.floor((last_close - high) / extension) + 1)
        upper_target = math.ceil((high + steps * extension) / tick - 1e-12) * tick
        upper_basis = extension_basis
    if lower_pivots:
        lower_target, lower_basis = max(lower_pivots), "此前已确认摆动低点"
    else:
        steps = max(1, math.floor((low - last_close) / extension) + 1)
        lower_target = math.floor((low - steps * extension) / tick + 1e-12) * tick
        lower_basis = extension_basis
    if lower_target <= 0:
        lower_target = None
    names = {
        "inside": ("区间内" if is_range else "参考带内", "neutral"),
        "up_break": ("上破待回踩", "long"), "down_break": ("下破待反抽", "short"),
        "up_hold": ("回踩守住", "long"), "down_hold": ("反抽受阻", "short"),
        "up_failed": ("上破收回", "neutral"), "down_failed": ("下破收回", "neutral"),
        "up_extension": ("上行延伸", "long"), "down_extension": ("下行延伸", "short"),
    }
    label, bias = names[state]
    summaries = {
        "inside": f"已完成K线尚未有效越过 {_fmt(lower_gate)}–{_fmt(upper_gate)}；保持中性，等待边界确认。",
        "up_break": f"此前收盘曾站上 {_fmt(upper_gate)}，最新收盘 {_fmt(last_close)}；等待回踩 {_fmt(high)} 守住的确认，不把突破等同续涨。",
        "down_break": f"此前收盘曾跌破 {_fmt(lower_gate)}，最新收盘 {_fmt(last_close)}；等待反抽 {_fmt(low)} 受阻的确认，不把跌破等同续跌。",
        "up_hold": f"上破后回踩 {_fmt(high)} 附近并收盘站回 {_fmt(upper_gate)} 上方；续行仍取决于该位保持。",
        "down_hold": f"下破后反抽 {_fmt(low)} 附近，收盘仍低于 {_fmt(lower_gate)}；下行情景取决于该位继续受阻。",
        "up_failed": f"此前上破后，收盘已回到 {_fmt(high - buffer)} 下方；上破情景失效，并不自动构成做空信号。",
        "down_failed": f"此前下破后，收盘已回到 {_fmt(low + buffer)} 上方；下破情景失效，并不自动构成做多信号。",
        "up_extension": f"连续收盘位于 {_fmt(upper_gate)} 上方且已远离边界；等待回踩，参考延伸不是确定目标。",
        "down_extension": f"连续收盘位于 {_fmt(lower_gate)} 下方且已远离边界；等待反抽，参考延伸不是确定目标。",
    }
    if state == "up_break" and last_close <= upper_gate:
        label, bias = "回踩待确认", "neutral"
        summaries[state] = (f"上破后最新收盘 {_fmt(last_close)} 回到 {_fmt(high - buffer)}–{_fmt(upper_gate)} 确认带；"
                            f"须重新收于 {_fmt(upper_gate)} 上方才确认守住，收于 {_fmt(high - buffer)} 下方则上破失效。")
    elif state == "down_break" and last_close >= lower_gate:
        label, bias = "反抽待确认", "neutral"
        summaries[state] = (f"下破后最新收盘 {_fmt(last_close)} 回到 {_fmt(lower_gate)}–{_fmt(low + buffer)} 确认带；"
                            f"须重新收于 {_fmt(lower_gate)} 下方才确认受阻，收于 {_fmt(low + buffer)} 上方则下破失效。")
    if not events:
        event(len(completed) - 1, f"收盘 {_fmt(last_close)}，尚无边界突破确认", "neutral")
    tf = _period_label(period)
    mid = (high + low) / 2
    inner_high, inner_low = (high - buffer, low + buffer) if usable_range else (high, low)
    up_condition = (f"已完成{tf}K线收于 {_fmt(upper_gate)} 上方，后续回踩 "
                    f"{_fmt(high - buffer)}–{_fmt(upper_gate)} 并再次收于 {_fmt(upper_gate)} 上方；"
                    f"{upper_basis} {_fmt(upper_target)}。")
    down_condition = (f"已完成{tf}K线收于 {_fmt(lower_gate)} 下方，后续反抽 "
                      f"{_fmt(lower_gate)}–{_fmt(low + buffer)} 并再次收于 {_fmt(lower_gate)} 下方；"
                      + (f"{lower_basis} {_fmt(lower_target)}。" if lower_target is not None else "下方无有效参考目标。"))
    return {
        "status": "ok", "state": {"key": state, "label": label, "bias": bias, "summary": summaries[state]},
        "price": _rounded(price), "atr": _rounded(atr), "buffer": _rounded(buffer),
        "levels": {"support": _rounded(low), "resistance": _rounded(high),
                   "upper_target": _rounded(upper_target),
                   "lower_target": _rounded(lower_target) if lower_target is not None else None},
        "range": {"start_index": start + ref_start, "end_index": start + split - 1,
                  "low": _rounded(low), "high": _rounded(high),
                  "kind": "range" if is_range else "reference",
                  "label": "前段震荡区间" if is_range else "前段参考带（未确认震荡）"},
        "pivots": pivots, "events": events[-3:],
        "scenarios": [
            {"key": "up", "label": "上破后回踩续行（条件）", "condition": up_condition,
             "invalidation": f"完成K线收于 {_fmt(high - buffer)} 下方，上行情景失效。",
             "target": _rounded(upper_target),
             "path": list(map(_rounded, [price, upper_gate, high, upper_gate, upper_target]))},
            {"key": "down", "label": "下破后反抽续行（条件）", "condition": down_condition,
             "invalidation": f"完成K线收于 {_fmt(low + buffer)} 上方，下行情景失效。",
             "target": _rounded(lower_target) if lower_target is not None else None,
             "path": list(map(_rounded, [price, lower_gate, low, lower_gate]
                              + ([lower_target] if lower_target is not None else [])))},
            {"key": "range", "label": "区间往返（条件）" if is_range else "参考带内观察（条件）",
             "condition": f"完成K线维持 {_fmt(lower_gate)}–{_fmt(upper_gate)} 内，尚无越界确认；路径仅示意往返。",
             "invalidation": f"完成K线收于 {_fmt(upper_gate)} 上方或 {_fmt(lower_gate)} 下方，此情景失效。",
             "target": None, "path": list(map(_rounded, [price, mid, inner_high, inner_low, mid]))},
        ],
        "rules": {
            "basis": "三条路径均为条件示意，无概率；最后一根仅提供现价，结构与标注只用已完成K线。",
            "confirmation": f"缓冲 {_fmt(buffer)} 由参考段ATR固定（至少1跳）；回踩/反抽须由后续完成K线确认且当前仍守在门槛外；摆动点右侧确认2根。",
            "lookback": f"回看 {len(completed)} 根完成K线；前段 {len(ref)} 根定边界，最近 {OBSERVATION} 根仅观察，不改写本次边界。",
        },
    }


# 增量接口独立于上面的兼容快照接口；状态只包含可JSON保存的普通值。
TRACKING_VERSION = 1
PLATFORM_FORMING = 4
PLATFORM_CONFIRM = 2
TRACKING_RULE = ("边界与ATR按代冻结；只处理新增完成K线。突破/失败之后至少4根独立整理，"
                 "宽度≤旧ATR的3倍、相邻区间重叠≥较小棒幅的25%、上下沿各2次触碰；"
                 "另需连续2根完成棒收在候选内且影线不超候选1个缓冲，才接替旧结构。")


def _tracking_id(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:20]


def _tracking_time(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        zone = timezone(timedelta(hours=8))
        dt = dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        return None


def _tracking_event(state, at, kind, label, detail, price=None, structure_id=None):
    sid = structure_id or state["active"]["id"]
    item = {"id": _tracking_id(sid, at, kind, label), "at": at, "kind": kind,
            "label": label, "detail": detail, "structure_id": sid, "price": price}
    if not any(e["id"] == item["id"] for e in state["events"]):
        state["events"].append(item)
        state["events"].sort(key=lambda e: (e["at"], e["id"]))
        state["events"] = state["events"][-32:]


def _tracking_archive(active, at, reason, status):
    return {"id": active["id"], "label": active["label"], "low": active["low"],
            "high": active["high"], "start_time": active["start_time"],
            "confirmed_at": active["confirmed_at"], "end_time": at,
            "end_reason": reason, "status": status, "phase": active["phase"]}


def _tracking_active(symbol, period, ref, atr, buffer, kind, label, confirmed_at, reason, pivots, tick):
    low, high = min(b["low"] for b in ref), max(b["high"] for b in ref)
    extension = max(high - low, 2 * atr, 4 * tick)
    old = [p for p in pivots if p["confirmed_time"] <= ref[-1]["at"]]
    upper = [p["price"] for p in old if p["kind"] == "high" and p["price"] > high + buffer]
    lower = [p["price"] for p in old if p["kind"] == "low" and p["price"] < low - buffer]
    up = min(upper) if upper else math.ceil((high + extension) / tick - 1e-12) * tick
    down = max(lower) if lower else math.floor((low - extension) / tick + 1e-12) * tick
    fingerprint = [(b["at"], b["open"], b["high"], b["low"], b["close"]) for b in ref]
    return {"id": _tracking_id(symbol, period, confirmed_at, low, high, tick, fingerprint),
            "label": label, "phase": "inside", "phase_label": "新平台内" if kind == "platform" else "参考带内",
            "low": _rounded(low), "high": _rounded(high), "start_time": ref[0]["at"],
            "end_time": ref[-1]["at"], "confirmed_at": confirmed_at, "reason": reason,
            "kind": kind, "atr": _rounded(atr), "buffer": _rounded(buffer),
            "upper_target": _rounded(up), "lower_target": _rounded(down) if down > 0 else None,
            "upper_basis": "此前已确认摆动高点" if upper else "冻结结构高度/ATR的参考延伸",
            "lower_basis": "此前已确认摆动低点" if lower else "冻结结构高度/ATR的参考延伸",
            "upper_touched": False, "lower_touched": False, "breakout_at": None,
            "consecutive": 0, "eligible_after": None, "eligible_kind": None}


def _tracking_add_pivots(state):
    recent = state["seen"][-5:]
    if len(recent) < 5:
        return
    for p in _pivots(recent, 0):
        item = {"time": recent[p["index"]]["at"], "confirmed_time": recent[-1]["at"],
                "price": p["price"], "kind": p["kind"]}
        if not any(q["time"] == item["time"] and q["kind"] == item["kind"] for q in state["pivots"]):
            state["pivots"].append(item)
    state["pivots"] = state["pivots"][-240:]


def _tracking_phase(state, bar):
    a, c, at = state["active"], bar["close"], bar["at"]
    high, low, buffer, atr = a["high"], a["low"], a["buffer"], a["atr"]
    before, phase = a["phase"], a["phase"]
    fresh_trigger = None
    if phase.startswith("up_") and phase != "up_failed":
        if c < high - buffer:
            phase, fresh_trigger = "up_failed", "failed"
        else:
            a["consecutive"] = a["consecutive"] + 1 if c > high + buffer else 0
            if at > a["breakout_at"] and high - buffer <= bar["low"] <= high + buffer and c > high + buffer:
                phase = "up_hold"
            elif a["consecutive"] >= 2 and c >= high + max(2 * atr, (high - low) * .5):
                phase = "up_extension"
            elif c <= high + buffer or phase == "up_extension":
                phase = "up_break"
    elif phase.startswith("down_") and phase != "down_failed":
        if c > low + buffer:
            phase, fresh_trigger = "down_failed", "failed"
        else:
            a["consecutive"] = a["consecutive"] + 1 if c < low - buffer else 0
            if at > a["breakout_at"] and low - buffer <= bar["high"] <= low + buffer and c < low - buffer:
                phase = "down_hold"
            elif a["consecutive"] >= 2 and c <= low - max(2 * atr, (high - low) * .5):
                phase = "down_extension"
            elif c >= low - buffer or phase == "down_extension":
                phase = "down_break"
    if phase in ("inside", "up_failed", "down_failed"):
        if c > high + buffer:
            phase, fresh_trigger = "up_break", "up"
        elif c < low - buffer:
            phase, fresh_trigger = "down_break", "down"
    if fresh_trigger:
        a["eligible_after"], a["eligible_kind"] = at, fresh_trigger
        a["consecutive"] = 0 if fresh_trigger == "failed" else 1
        if fresh_trigger != "failed":
            a["breakout_at"] = at
        if state["candidate"]:
            _tracking_event(state, at, "candidate_cancelled", "新平台候选取消",
                            "旧结构出现新的突破/失败事实，原候选不再累计确认。", c)
        state["candidate"], state["forming"] = None, []
    a["phase"] = phase
    labels = {"inside": "新平台内" if a["kind"] == "platform" else "参考带内",
              "up_break": "上破待回踩", "down_break": "下破待反抽",
              "up_hold": "回踩守住", "down_hold": "反抽受阻",
              "up_extension": "上行延伸", "down_extension": "下行延伸",
              "up_failed": "上破收回", "down_failed": "下破收回"}
    a["phase_label"] = labels[phase]
    if phase != before:
        _tracking_event(state, at, "phase", labels[phase],
                        f"完成K线收盘 {_fmt(c)}；本代边界仍为 {_fmt(low)}–{_fmt(high)}。", c)
    # 只记录完成棒触及事实；同一根可能同时触及两侧，绝不推断先后与收益。
    for side, key, touched in (("上方", "upper_target", "upper_touched"), ("下方", "lower_target", "lower_touched")):
        target = a[key]
        if target is not None and not a[touched] and bar["low"] <= target <= bar["high"]:
            a[touched] = True
            _tracking_event(state, at, "reference_touch", f"{side}参考位触及",
                            f"完成K线范围覆盖参考位 {_fmt(target)}；仅记录触及，不推断同棒先后、盈利或预测命中。", target)


def _tracking_platform(state, bar):
    a, at = state["active"], bar["at"]
    if not a["eligible_after"] or at <= a["eligible_after"]:
        return
    candidate = state["candidate"]
    if candidate:
        low, high, buf = candidate["low"], candidate["high"], candidate["buffer"]
        valid = low <= bar["close"] <= high and bar["low"] >= low - buf and bar["high"] <= high + buf
        if not valid:
            _tracking_event(state, at, "candidate_cancelled", "新平台候选取消",
                            f"后续完成棒未守在候选 {_fmt(low)}–{_fmt(high)} 内，或影线超出缓冲 {_fmt(buf)}；旧边界保留。", bar["close"])
            state["candidate"], state["forming"] = None, []
            return
        candidate["confirmations"] += 1
        candidate["bars"] += 1
        if candidate["confirmations"] < PLATFORM_CONFIRM:
            return
        reason = (f"独立整理4根形成 {_fmt(low)}–{_fmt(high)}，后续连续2根完成K线守住候选且影线未超缓冲；新平台接替。")
        state["previous"].append(_tracking_archive(a, at, reason, "replaced"))
        state["previous"] = state["previous"][-8:]
        state["active"] = _tracking_active(state["symbol"], state["period"], candidate["formation"],
                                            candidate["atr"], buf, "platform", "已确认新整理平台", at,
                                            reason, state["pivots"], state["tick"])
        state["candidate"], state["forming"] = None, []
        state["changed_at"], state["change_reason"] = at, reason
        _tracking_event(state, at, "structure_replaced", "新平台确认并接替", reason, bar["close"])
        return  # 该确认棒不能再被当成新平台的突破棒。
    state["forming"] = (state["forming"] + [bar])[-PLATFORM_FORMING:]
    group = state["forming"]
    if len(group) < PLATFORM_FORMING:
        return
    low, high = min(b["low"] for b in group), max(b["high"] for b in group)
    width = high - low
    atr = max(_atr(group), state["tick"])
    buf = max(state["tick"], math.ceil(atr * .2 / state["tick"] - 1e-12) * state["tick"])
    if width <= 2 * buf or width > 3 * a["atr"]:
        return
    if any(min(x["high"], y["high"]) - max(x["low"], y["low"]) < .25 * min(x["high"] - x["low"], y["high"] - y["low"])
           for x, y in zip(group, group[1:])):
        return
    touch = min(a["buffer"], width * .25)
    if sum(high - b["high"] <= touch for b in group) < 2 or sum(b["low"] - low <= touch for b in group) < 2:
        return
    kind = a["eligible_kind"]
    located = ((kind == "up" and low > a["high"] + a["buffer"])
               or (kind == "down" and high < a["low"] - a["buffer"])
               or (kind == "failed" and low >= a["low"] - a["buffer"] and high <= a["high"] + a["buffer"]
                   and width <= .75 * (a["high"] - a["low"])))
    if not located:
        return
    reason = (f"4根独立完成棒形成窄幅重叠区 {_fmt(low)}–{_fmt(high)}，上下沿各至少2根触碰；"
              f"还需连续2根完成棒收在该区内且影线不超 {_fmt(buf)} 缓冲。")
    state["candidate"] = {"id": _tracking_id(a["id"], group[0]["at"], at, low, high),
                          "label": "新整理平台候选", "status": "forming", "low": _rounded(low), "high": _rounded(high),
                          "start_time": group[0]["at"], "end_time": at, "bars": 4, "required_bars": 6,
                          "reason": reason, "atr": _rounded(atr), "buffer": _rounded(buf),
                          "confirmations": 0, "formation": copy.deepcopy(group)}
    _tracking_event(state, at, "candidate", "新平台形成待确认", reason, bar["close"])


def _tracking_scenarios(state, price):
    a, candidate = state["active"], state["candidate"]
    low, high, buf, atr = a["low"], a["high"], a["buffer"], a["atr"]
    mid, close = (low + high) / 2, state["seen"][-1]["close"]
    phase = a["phase"]
    extension = max(high - low, 2 * atr, 4 * state["tick"])
    up, down = a["upper_target"], a["lower_target"]
    up_basis, down_basis = a["upper_basis"], a["lower_basis"]
    if a["upper_touched"] or up <= close:
        up = _rounded(high + max(1, math.floor((close - high) / extension) + 1) * extension)
        up_basis = "原上方参考位已触及；下一档仅参考延伸"
    if a["lower_touched"] or (down is not None and down >= close):
        down = _rounded(low - max(1, math.floor((low - close) / extension) + 1) * extension)
        down = down if down > 0 else None
        down_basis = "原下方参考位已触及；下一档仅参考延伸"

    def path(key, direction, label, condition, invalidation, target, points, basis="条件示意，无确定目标"):
        return {"key": key, "direction": direction, "short_label": label, "label": label + "（条件）",
                "status": "conditional", "status_label": "条件路径", "condition": condition,
                "invalidation": invalidation, "target": target, "target_basis": basis,
                "path": [_rounded(v) for v in [price] + points if v is not None]}

    up_path = path("up_retest", "up", "上破后回踩续行",
                   f"完成K线站上 {_fmt(high + buf)}，后续回踩 {_fmt(high)} 后再收上 {_fmt(high + buf)}。",
                   f"完成K线收于 {_fmt(high - buf)} 下方则条件失效。", up, [high, high + buf, up], up_basis)
    down_path = path("down_retest", "down", "下破后反抽续行",
                     f"完成K线跌破 {_fmt(low - buf)}，后续反抽 {_fmt(low)} 后再收下 {_fmt(low - buf)}。",
                     f"完成K线收于 {_fmt(low + buf)} 上方则条件失效。", down, [low, low - buf, down], down_basis)
    range_path = path("range", "range", "平台内往返" if a["kind"] == "platform" else "参考带内往返",
                      f"完成K线保持 {_fmt(low - buf)}–{_fmt(high + buf)} 内，边界仍需观察。",
                      f"收盘越过 {_fmt(low - buf)} 或 {_fmt(high + buf)} 则该情景失效。", None, [mid, high - buf, low + buf, mid])
    result = [up_path, down_path, range_path]
    if phase.startswith("up_") and phase != "up_failed":
        direct = path("up_shallow", "up", "直接延伸或浅回调",
                      f"完成K线继续守在 {_fmt(high + buf)} 上方，局部回调仍保持该门槛；不要求一定回到旧边界。",
                      f"收盘回到 {_fmt(high + buf)} 下方，直接延伸条件暂停。", up,
                      [max(high + buf, close - atr * .5), max(close, high + 2 * buf), up], up_basis)
        failure = path("up_failure_return", "range", "上破失败回区间",
                       f"若完成K线收回 {_fmt(high - buf)} 下方，转为观察原带 {_fmt(low)}–{_fmt(high)}，不自动等同下跌趋势。",
                       f"重新收上 {_fmt(high + buf)} 则回区间情景失效。", None, [high - buf, mid, low + buf])
        result = [direct, up_path, failure]
    elif phase.startswith("down_") and phase != "down_failed":
        direct = path("down_shallow", "down", "直接下延或浅反弹",
                      f"完成K线继续守在 {_fmt(low - buf)} 下方，局部反弹仍受此门槛约束。",
                      f"收盘回到 {_fmt(low - buf)} 上方，直接下延条件暂停。", down,
                      [min(low - buf, close + atr * .5), min(close, low - 2 * buf), down], down_basis)
        failure = path("down_failure_return", "range", "下破失败回区间",
                       f"若完成K线收回 {_fmt(low + buf)} 上方，观察原带 {_fmt(low)}–{_fmt(high)}，不自动等同上涨趋势。",
                       f"重新收下 {_fmt(low - buf)} 则回区间情景失效。", None, [low + buf, mid, high - buf])
        result = [direct, down_path, failure]
    elif phase in ("up_failed", "down_failed"):
        range_path.update(key="failed_rebalance", short_label="失败后区间再平衡", label="失败后区间再平衡（条件）")
        result = [range_path, up_path, down_path]
    if candidate:
        cl, ch = candidate["low"], candidate["high"]
        platform = path("new_platform", "range", "新平台待确认", candidate["reason"],
                        f"收盘离开 {_fmt(cl)}–{_fmt(ch)} 或影线超出 {_fmt(candidate['buffer'])} 缓冲，候选取消。",
                        None, [(cl + ch) / 2, ch, cl, (cl + ch) / 2])
        platform.update(status="forming", status_label=f"已完成{candidate['bars']}/6根")
        if phase in ("up_break", "up_hold", "up_extension", "down_break", "down_hold", "down_extension"):
            result[1] = platform  # 保留独立失败路径，不显示两条同方向延续加一个整理。
        elif phase in ("up_failed", "down_failed"):
            result[0] = platform  # 替换区间再平衡，保留上下两侧分支。
        else:
            result[-1] = platform
    return result


def _tracking_render(state, bars, base):
    analysis = copy.deepcopy(base)
    a, close = state["active"], state["seen"][-1]["close"]
    times = {_tracking_time(b.get("dt")): i for i, b in enumerate(bars) if isinstance(b, dict)}
    price = bars[-1].get("close") if isinstance(bars[-1], dict) else None
    price = price if _number(price) and price > 0 else close
    low, high, buf, phase = a["low"], a["high"], a["buffer"], a["phase"]
    bias = "long" if phase.startswith("up_") and phase != "up_failed" else "short" if phase.startswith("down_") and phase != "down_failed" else "neutral"
    label = a["phase_label"]
    if phase == "up_break" and close <= high + buf:
        bias, label = "neutral", "回踩待确认"
    if phase == "down_break" and close >= low - buf:
        bias, label = "neutral", "反抽待确认"
    summary = (f"{label}；冻结边界 {_fmt(low)}–{_fmt(high)}，最新完成收盘 {_fmt(close)}；"
               f"确认门槛 {_fmt(low - buf)} / {_fmt(high + buf)}。")
    if phase.endswith("failed"):
        summary += "原突破已收回，不自动构成反向信号。"
    if state["candidate"]:
        summary += f" 新平台候选已完成 {state['candidate']['bars']}/6 根，旧边界继续有效。"
    analysis.update(status="ok", state={"key": phase, "label": label, "bias": bias, "summary": summary},
                    price=_rounded(price), atr=a["atr"], buffer=buf)
    analysis["range"] = {"start_index": times.get(a["start_time"], -1), "end_index": times.get(a["end_time"], -1),
                         "start_time": a["start_time"], "end_time": a["end_time"], "low": low, "high": high,
                         "kind": "range" if a["kind"] in ("range", "platform") else "reference", "label": a["label"]}
    analysis["levels"] = {"support": low, "resistance": high, "upper_target": a["upper_target"], "lower_target": a["lower_target"]}
    analysis["pivots"] = [{**p, "index": times.get(p["time"], -1)} for p in state["pivots"]]
    mapped_events = [{"index": times[e["at"]], "price": e["price"], "text": e["label"],
                      "kind": "up" if e["label"].startswith("上") else "down" if e["label"].startswith("下") else "neutral"}
                     for e in state["events"] if e["at"] in times and e["price"] is not None]
    analysis["events"] = mapped_events[-3:]
    analysis["scenarios"] = _tracking_scenarios(state, price)
    public_candidate = {k: v for k, v in state["candidate"].items() if k != "formation"} if state["candidate"] else None
    analysis["structure_tracking"] = {"active": copy.deepcopy(a), "previous": copy.deepcopy(state["previous"]),
                                      "candidate": copy.deepcopy(public_candidate), "events": copy.deepcopy(state["events"]),
                                      "changed_at": state["changed_at"], "change_reason": state["change_reason"],
                                      "update_rule": TRACKING_RULE, "rebuild_reason": state.get("rebuild_reason")}
    analysis["rules"] = {"basis": "只用完成棒增量确认；实时价仅改变现价与路径起点。条件路径无概率、无交易指令。",
                         "confirmation": TRACKING_RULE + "同棒触及不推断先后；参考位触及不代表收益。",
                         "lookback": "当前窗口移动不重定边界；只有新平台确认或数据明确重建才更换本代锚点。"}
    # 独立的可视化证据层；不替换冻结结构、交易计划或原条件路径。
    analysis["market_structure"] = build_market_structure(
        bars, tick=state["tick"], period=state["period"],
        reference={**analysis["range"], "buffer": buf, "atr": a["atr"]})
    return analysis


def advance_outlook(bars, previous=None, symbol="", tick=1.0, period=1800):
    """冻结结构并增量处理完成棒，返回 (analysis, 可JSON持久化state)。

    增量身份依赖dt；休市时间不补造K线。无重叠/已知棒丢失、修订、回退或
    参数变化时明确重建；乱序/重复/无时间输入不排序猜测，暂停并等待有效序列。
    previous 和 bars 均不会被修改；重复输入和仅live变化不会改变持久状态。
    """
    base = build_outlook(bars, tick=tick, period=period)
    prior = copy.deepcopy(previous) if isinstance(previous, dict) else None

    def unavailable(reason):
        out = _empty(base.get("price"), reason)
        state = prior or {"version": TRACKING_VERSION, "symbol": symbol,
                          "period": period if _number(period) else None, "tick": tick if _number(tick) else None,
                          "active": None, "previous": [], "events": [], "candidate": None}
        state["pending_rebuild"] = reason
        out["structure_tracking"] = {"active": copy.deepcopy(state.get("active")),
                                      "previous": copy.deepcopy(state.get("previous", [])), "candidate": None,
                                      "events": copy.deepcopy(state.get("events", [])), "changed_at": None,
                                      "change_reason": reason, "rebuild_reason": reason, "update_rule": TRACKING_RULE}
        return out, state

    if not _number(tick) or tick <= 0 or not _number(period) or period <= 0:
        return unavailable("tick/周期无效，等待有效参数后重建")
    if base["status"] != "ok":
        return unavailable(base["state"]["summary"])
    records = []
    for b in bars[:-1]:
        clean, at = _clean_bar(b), _tracking_time(b.get("dt") if isinstance(b, dict) else None)
        if clean is None or at is None:
            return unavailable("完成K线缺少有效OHLC或dt，无法确认增量身份；等待完整数据重建")
        records.append({**clean, "at": at})
    live_time = _tracking_time(bars[-1].get("dt") if isinstance(bars[-1], dict) else None)
    if any(x["at"] >= y["at"] for x, y in zip(records, records[1:])) or (live_time is not None and live_time <= records[-1]["at"]):
        return unavailable("时间戳重复或乱序，暂停跟踪；待按时间升序恢复后重建")
    reason = None
    valid_prior = (prior and prior.get("active") and prior.get("seen") and prior.get("version") == TRACKING_VERSION)
    if prior and not valid_prior:
        reason = "跟踪状态缺失或版本变化，重新建立基线"
    elif valid_prior:
        if (prior.get("symbol"), prior.get("period"), prior.get("tick")) != (symbol, period, tick):
            reason = "品种、周期或最小跳动变化，重新建立基线"
        elif prior.get("pending_rebuild"):
            reason = "数据恢复后重建：" + prior["pending_rebuild"]
        elif records[-1]["at"] < prior["seen"][-1]["at"]:
            reason = "完成K线时间回退，重新建立基线"
        else:
            known = {b["at"]: b for b in prior["seen"]}
            incoming = {b["at"]: b for b in records}
            last = prior["seen"][-1]["at"]
            if last not in incoming or any(records[0]["at"] <= at <= last and at not in incoming for at in known):
                reason = "完成序列断档或失去共同锚点，重新建立基线"
            elif any(prior["seen"][0]["at"] <= at <= last and at not in known for at in incoming):
                reason = "历史完成K线被补入，重新建立基线"
            elif any(at in known and b != known[at] for at, b in incoming.items()):
                reason = "已完成OHLC发生修订，重新建立基线"
    if not valid_prior or reason:
        first, end = base["range"]["start_index"], base["range"]["end_index"]
        ref = records[first:end + 1]
        history = copy.deepcopy(prior.get("previous", [])) if prior else []
        old_events = copy.deepcopy(prior.get("events", [])) if prior else []
        if prior and prior.get("active"):
            archived_at = max(records[-1]["at"], (prior.get("seen") or records)[-1]["at"])
            history.append(_tracking_archive(prior["active"], archived_at, reason, "rebuilt"))
        state = {"version": TRACKING_VERSION, "symbol": symbol, "period": period, "tick": tick,
                 "previous": history[-8:], "events": old_events[-32:], "candidate": None, "forming": [],
                 "seen": copy.deepcopy(records[:end + 1]), "pivots": [], "rebuild_reason": reason,
                 "changed_at": ref[-1]["at"], "change_reason": reason or "初始参考段完成，冻结边界并回放观察段"}
        for p in _pivots(state["seen"], 0):
            state["pivots"].append({"time": state["seen"][p["index"]]["at"],
                                     "confirmed_time": state["seen"][p["index"] + PIVOT_CONFIRM]["at"],
                                     "price": p["price"], "kind": p["kind"]})
        state["active"] = _tracking_active(symbol, period, ref, base["atr"], base["buffer"], base["range"]["kind"],
                                            base["range"]["label"], ref[-1]["at"], state["change_reason"], state["pivots"], tick)
        if reason and prior and prior.get("active"):
            state["active"]["id"] = _tracking_id(state["active"]["id"], prior["active"]["id"], reason)
        _tracking_event(state, ref[-1]["at"], "rebuild" if reason else "structure_created",
                        "数据重建" if reason else "初始结构确认", state["change_reason"])
        new = records[end + 1:]
    else:
        state = prior
        new = [b for b in records if b["at"] > state["seen"][-1]["at"]]
    for bar in new:
        state["seen"].append(copy.deepcopy(bar))
        _tracking_add_pivots(state)
        _tracking_phase(state, bar)
        _tracking_platform(state, bar)
        state["seen"] = state["seen"][-240:]
    state["seen"] = state["seen"][-240:]
    state["pivots"] = state["pivots"][-240:]
    return _tracking_render(state, bars, base), state
