"""完成 K 线上的趋势线与条件路径。独立展示层，不决定交易准入。

最后一根始终是 live，用于现价、条件有效性与路径起点。趋势线须有左右
各两根确认的两个枢轴；线的缓冲在第二锚点可知时冻结，事件从之后才开始。
reference 可传 outlook.range 加其 atr/buffer；本模块不更改该冻结结构。
"""

import hashlib
import math
from datetime import datetime, timezone, timedelta


VERSION = "market-structure-v3"
LOOKBACK = 120
MIN_COMPLETED = 12
PIVOT_WING = 2
MIN_ANCHOR_GAP = 4
MAX_ANCHOR_GAP = 60


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value):
    return _number(value) and value > 0


def _round(value):
    return round(float(value), 8)


def _fmt(value):
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _id(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:16]


def _stamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone(timedelta(hours=8)))
        return result.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _clean(raw, index):
    if not isinstance(raw, dict) or not all(_positive(raw.get(k)) for k in ("open", "high", "low", "close")):
        return None
    o, h, low, c = [float(raw[k]) for k in ("open", "high", "low", "close")]
    if h < max(o, c) or low > min(o, c) or low > h:
        return None
    if any(raw.get(k) is False for k in ("complete", "completed", "closed", "is_complete", "final")) or raw.get("unfinished") is True:
        return None
    return {"index": index, "time": raw.get("dt"), "open": o, "high": h, "low": low, "close": c}


def _atr(bars, tick):
    if not bars:
        return tick
    tr = [bars[0]["high"] - bars[0]["low"]]
    tr.extend(max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
              for a, b in zip(bars, bars[1:]))
    return max(tick, sum(tr[-14:]) / len(tr[-14:]))


def _buffer(atr, tick):
    return max(tick, math.ceil(.2 * atr / tick - 1e-12) * tick)


def _rules():
    return {"basis": "最后一根只显示现价；所有枢轴、破线、回测和影线证据来自完成K线。",
            "trend_line": "左右各2根确认枢轴；两锚点间隔4–60根，第二锚点确认之后才检查破线，缓冲固定于该时刻。",
            "consolidation": "至少3根破位后的完成棒缺少收盘延续且窄幅重叠，才标整理线索；不等于正式震荡或原冻结结构换代。",
            "path": "路径只表示条件先后，无未来时间、概率或交易许可；新时段直接跳空并远离观察带时不追随，等待回测证据。"}


def _empty(period, price, reason):
    return {"status": "insufficient", "version": VERSION, "period": period, "price": price,
            "decision_bar_time": None, "completed_index": None, "atr": None, "buffer": None,
            "reference": None, "phase": {"key": "insufficient", "label": "结构证据不足", "direction": "neutral",
            "bias": "neutral", "summary": reason, "detail": reason}, "pivots": [], "trend_lines": [],
            "observation_zone": None, "candidate": None, "clues": [], "events": [], "scenarios": [],
            "key_levels": None, "selected_scenario": None, "live_watch": None, "rules": _rules()}


def _pivots(bars):
    result = []
    for j in range(PIVOT_WING, len(bars) - PIVOT_WING):
        b, left, right = bars[j], bars[j - 2:j], bars[j + 1:j + 3]
        for kind, key in (("high", "high"), ("low", "low")):
            extreme = b[key]
            valid = (extreme > max(x[key] for x in left) and extreme >= max(x[key] for x in right)) if kind == "high" else (
                extreme < min(x[key] for x in left) and extreme <= min(x[key] for x in right))
            if valid:
                confirmed = bars[j + 2]
                result.append({"id": _id("pivot", b["time"] or b["index"], kind, extreme), "index": b["index"],
                               "time": b["time"], "price": _round(extreme), "kind": kind,
                               "confirmed_index": confirmed["index"], "confirmed_time": confirmed["time"]})
    return result


def _event(bar, kind, label, detail, price=None, line_id=None):
    return {"id": _id(line_id or "reference", kind, bar["time"] or bar["index"], price),
            "kind": kind, "label": label, "detail": detail, "index": bar["index"], "time": bar["time"],
            "price": _round(bar["close"] if price is None else price), "confirmed": True, "line_id": line_id}


def _line_value(line, index):
    a, b = line["anchors"]
    return a["price"] + (b["price"] - a["price"]) * (index - a["index"]) / (b["index"] - a["index"])


def _trend_lines(bars, pivots, tick):
    lines, events = [], []
    first, last = bars[0]["index"], bars[-1]
    for kind, pkind in (("resistance", "high"), ("support", "low")):
        relevant = [p for p in pivots if p["kind"] == pkind]
        selected = None
        for second in reversed(relevant):
            for anchor in reversed(relevant):
                gap = second["index"] - anchor["index"]
                slope_ok = second["price"] <= anchor["price"] - tick if kind == "resistance" else second["price"] >= anchor["price"] + tick
                if not MIN_ANCHOR_GAP <= gap <= MAX_ANCHOR_GAP or not slope_ok:
                    continue
                known = second["confirmed_index"]
                scale = _atr(bars[:known - first + 1], tick)
                line = {"id": _id("line", kind, anchor["id"], second["id"]), "kind": kind,
                        "direction": "down" if kind == "resistance" else "up",
                        "label": "下降压力线" if kind == "resistance" else "上升支撑线",
                        "anchors": [{k: p[k] for k in ("index", "time", "price")} for p in (anchor, second)],
                        "confirmed_index": known, "confirmed_time": second["confirmed_time"],
                        "buffer": _round(_buffer(scale, tick)), "atr": _round(scale)}
                # 不能在确认时已经位于被突破的一侧，再倒画一条“此前可知”的线。
                formation = bars[anchor["index"] - first:known - first + 1]
                if any(b["close"] > _line_value(line, b["index"]) + line["buffer"] if kind == "resistance" else
                       b["close"] < _line_value(line, b["index"]) - line["buffer"] for b in formation):
                    continue
                if _line_value(line, last["index"]) <= tick:
                    continue
                selected = line
                break
            if selected:
                break
        if selected is None:
            continue
        line, status, broke, last_signal = selected, "intact", None, None
        buf = line["buffer"]
        for b in bars[line["confirmed_index"] - first + 1:]:
            value, c = _line_value(line, b["index"]), b["close"]
            broken = c > value + buf if kind == "resistance" else c < value - buf
            returned = c < value - buf if kind == "resistance" else c > value + buf
            if status in ("intact", "reclaimed") and broken:
                status, broke = "broken", b
                event = _event(b, "line_break_up" if kind == "resistance" else "line_break_down",
                               "完成收盘上破压力线" if kind == "resistance" else "完成收盘下破支撑线",
                               f"收盘 {_fmt(c)} 越过当棒线值 {_fmt(value)} 与固定缓冲 {_fmt(buf)}；只确认破线，不自动确认反转。", value, line["id"])
                events.append(event)
                last_signal = event
            elif status in ("broken", "retested"):
                if returned:
                    status = "reclaimed"
                    event = _event(b, "line_reclaimed", "破线后重返原侧",
                                   f"完成收盘回到线值 {_fmt(value)} 的原侧；此前破线情景需重新评估。", value, line["id"])
                    events.append(event)
                    last_signal = event
                else:
                    touched = value - buf <= b["low"] <= value + buf if kind == "resistance" else value - buf <= b["high"] <= value + buf
                    response = c > b["open"] if kind == "resistance" else c < b["open"]
                    if touched and broken and response and b["index"] > broke["index"]:
                        if status != "retested":
                            event = _event(b, "line_retest_up" if kind == "resistance" else "line_retest_down",
                                           "破线后回踩守在线上" if kind == "resistance" else "破线后反抽收在线下",
                                           f"后续完成棒触及 {_fmt(value - buf)}–{_fmt(value + buf)} 并收回突破侧；仍不是交易许可。", value, line["id"])
                            events.append(event)
                            last_signal = event
                        status = "retested"
                    elif not broken:
                        status = "broken"  # 留在缓冲带内时不沿用“当前回测守住”。
        line.update(start_index=line["anchors"][0]["index"], start_time=line["anchors"][0]["time"],
                    start_price=line["anchors"][0]["price"], end_index=last["index"], end_time=last["time"],
                    end_price=_round(_line_value(line, last["index"])), current_price=_round(_line_value(line, last["index"])),
                    status=status, break_index=broke["index"] if broke else None, break_time=broke["time"] if broke else None,
                    last_signal=last_signal)
        lines.append(line)
    return lines, events


def _reference(bars, supplied, tick):
    first, last = bars[0], bars[-1]
    valid = isinstance(supplied, dict) and _positive(supplied.get("low")) and _positive(supplied.get("high")) and supplied["high"] > supplied["low"]
    if valid:
        r = supplied
        end_time, end_index = r.get("end_time"), r.get("end_index")
        if end_time is not None:
            matched = next((b["index"] for b in bars if b["time"] == end_time), None)
            stamp, first_stamp = _stamp(end_time), _stamp(first["time"])
            if matched is not None:
                end_index = matched
            elif stamp is not None and first_stamp is not None and stamp < first_stamp:
                end_index = first["index"] - 1
            else:
                return None  # 时间无法定位，不能拿未来或不明参考确认过去。
        elif not isinstance(end_index, int) or isinstance(end_index, bool):
            return None
        if end_index > last["index"]:
            return None
        before = [b for b in bars if b["index"] <= end_index]
        atr = float(r["atr"]) if _positive(r.get("atr")) else _atr(before, tick)
        buffer = float(r["buffer"]) if _positive(r.get("buffer")) else _buffer(atr, tick)
        start_time = r.get("start_time")
        start_index = next((b["index"] for b in bars if b["time"] == start_time), -1) if start_time else r.get("start_index", -1)
        return {"low": _round(r["low"]), "high": _round(r["high"]), "kind": "range" if r.get("kind") == "range" else "reference",
                "source": "frozen_reference", "start_index": start_index, "end_index": end_index,
                "start_time": start_time, "end_time": end_time, "atr": _round(atr), "buffer": _round(buffer),
                "scale_basis": "传入冻结尺度" if _positive(r.get("buffer")) else "参考段尺度" if before else "锚点已离窗，仅用最小跳动尺度"}
    split = max(8, len(bars) - 8)
    ref = bars[max(0, split - 60):split]
    atr = _atr(ref, tick)
    return {"low": _round(min(b["low"] for b in ref)), "high": _round(max(b["high"] for b in ref)),
            "kind": "reference", "source": "local_reference", "start_index": ref[0]["index"], "end_index": ref[-1]["index"],
            "start_time": ref[0]["time"], "end_time": ref[-1]["time"], "atr": _round(atr), "buffer": _round(_buffer(atr, tick)),
            "scale_basis": "局部参考段尺度；不替换原冻结结构"}


def _pause_candidate(bars, broke, up, atr, buf, source):
    following = [b for b in bars if b["index"] > broke["index"]]
    group = following[-4:]
    if len(group) < 3:
        return None
    lo, hi = min(b["low"] for b in group), max(b["high"] for b in group)
    no_progress = max(b["close"] for b in following) <= broke["close"] + buf if up else min(b["close"] for b in following) >= broke["close"] - buf
    overlap = all(min(a["high"], b["high"]) - max(a["low"], b["low"]) >= .25 * min(a["high"] - a["low"], b["high"] - b["low"])
                  for a, b in zip(group, group[1:]))
    if not no_progress or not overlap or not 0 < hi - lo <= 2.5 * atr:
        return None
    return {"key": "no_follow_through", "label": "延续不足，整理线索待确认", "source": source,
            "low": _round(lo), "high": _round(hi), "start_index": group[0]["index"], "end_index": group[-1]["index"],
            "start_time": group[0]["time"], "end_time": group[-1]["time"], "bars": len(group), "required_bars": 4,
            "reason": "至少3根后续完成棒未在收盘上继续远离突破收盘，且相邻范围重叠、总宽不超2.5倍确认时ATR；仅是整理线索，不确认震荡。"}


def _reference_state(bars, ref):
    low, high, buf, atr = ref["low"], ref["high"], ref["buffer"], ref["atr"]
    state, broke, events, candidate = "inside", None, [], None
    for j, b in enumerate(bars):
        if b["index"] <= ref["end_index"]:
            continue
        c = b["close"]
        if state.startswith("up_") and state != "up_reclaimed" and c < high - buf:
            state = "up_reclaimed"
            events.append(_event(b, state, "上破后收回参考带", f"完成收盘低于 {_fmt(high - buf)}；不自动等同反向趋势。"))
        elif state.startswith("down_") and state != "down_reclaimed" and c > low + buf:
            state = "down_reclaimed"
            events.append(_event(b, state, "下破后收回参考带", f"完成收盘高于 {_fmt(low + buf)}；不自动等同反向趋势。"))
        elif state.startswith("up_") and state != "up_reclaimed" or state.startswith("down_") and state != "down_reclaimed":
            up = state.startswith("up_")
            boundary = high if up else low
            outside = c > boundary + buf if up else c < boundary - buf
            touched = boundary - buf <= b["low"] <= boundary + buf if up else boundary - buf <= b["high"] <= boundary + buf
            response = c > b["open"] and j and c > bars[j - 1]["close"] if up else c < b["open"] and j and c < bars[j - 1]["close"]
            if touched and outside and response and b["index"] > broke["index"]:
                next_state = "up_retest" if up else "down_retest"
                if state != next_state:
                    events.append(_event(b, next_state, "回踩后完成收盘守住" if up else "反抽后完成收盘受阻",
                                         f"后续完成棒触及 {_fmt(boundary - buf)}–{_fmt(boundary + buf)}，并收回突破侧；仅为回测事实。"))
                state = next_state
            else:
                step = max(buf, .5 * atr)
                extended = c > broke["close"] + step and bars[j - 1]["close"] > broke["close"] + step if up else c < broke["close"] - step and bars[j - 1]["close"] < broke["close"] - step
                state = ("up_" if up else "down_") + ("continuation" if extended and outside else "break")
        if state in ("inside", "up_reclaimed", "down_reclaimed"):
            if c > high + buf:
                state, broke = "up_break", b
                events.append(_event(b, state, "完成收盘越过参考上沿", f"收盘高于 {_fmt(high + buf)}；等待后续回踩与延续证据。"))
            elif c < low - buf:
                state, broke = "down_break", b
                events.append(_event(b, state, "完成收盘越过参考下沿", f"收盘低于 {_fmt(low - buf)}；等待后续反抽与延续证据。"))
    if broke and state not in ("up_reclaimed", "down_reclaimed", "inside"):
        up = state.startswith("up_")
        candidate = _pause_candidate(bars, broke, up, atr, buf, ref["source"])
        if candidate:
            state = ("up_" if up else "down_") + "pause"
            events.append(_event(bars[-1], "no_follow_through", candidate["label"], candidate["reason"]))
    return state, broke, candidate, events


def _phase(key, label, direction, detail):
    return {"key": key, "label": label, "direction": direction, "bias": {"up": "long", "down": "short"}.get(direction, "neutral"),
            "summary": detail, "detail": detail}


def _focus(ref_state, reference, lines, candidate):
    if ref_state != "inside":
        prefix = ref_state.split("_")[0]
        direction = "neutral" if ref_state.endswith("reclaimed") else prefix
        labels = {"break": "上破待回踩" if prefix == "up" else "下破待反抽",
                  "retest": "回踩守住观察" if prefix == "up" else "反抽受阻观察",
                  "continuation": "上行收盘延续观察" if prefix == "up" else "下行收盘延续观察",
                  "reclaimed": "上破收回，重新观察" if prefix == "up" else "下破收回，重新观察",
                  "pause": "破位后延续不足，可能整理"}
        label = labels[ref_state.split("_", 1)[1]]
        detail = candidate["reason"] if candidate else f"基于既有参考 {_fmt(reference['low'])}–{_fmt(reference['high'])} 的完成K线事实；不等同文档点位或入场许可。"
        return _phase(ref_state, label, direction, detail), None
    if lines:
        line = max(lines, key=lambda x: ((x["last_signal"] or {}).get("index", x["confirmed_index"]), x["confirmed_index"]))
        broken_direction = "up" if line["kind"] == "resistance" else "down"
        if line["status"] == "reclaimed":
            return _phase("line_reclaimed", "参考带内：局部破线收回", "neutral", f"局部破线情景已收回，回到原参考 {_fmt(reference['low'])}–{_fmt(reference['high'])} 内观察；外层结构并未因此反转。"), line
        if line["status"] in ("broken", "retested"):
            label = ("局部下降压力上破，待回踩" if broken_direction == "up" else "局部回升支撑被破，待反抽") if line["status"] == "broken" else (
                "局部破线后回踩守住" if broken_direction == "up" else "局部破线后反抽受阻")
            return _phase("line_" + line["status"] + "_" + broken_direction, "参考带内：" + label, broken_direction,
                          f"完成收盘尚未突破外层参考 {_fmt(reference['low'])}–{_fmt(reference['high'])}；当前仅是局部趋势线变化，收回该线也不等于大级别反转。"), line
        return _phase("line_intact_" + line["direction"], "下降压力线内观察" if line["direction"] == "down" else "上升支撑线内观察",
                      line["direction"], "两个已确认枢轴提供倾斜参考；仍须观察后续破线或回测，不能据此确定文档编号。"), line
    return _phase("inside", "区间内观察" if reference["kind"] == "range" else "参考带内，方向未确认", "range" if reference["kind"] == "range" else "neutral",
                  "尚无满足双枢轴条件的趋势线或完成收盘越界；未下跌不等于正式震荡。"), None


def _zone(phase, ref, line, bars, tick):
    direction = phase["direction"]
    if direction not in ("up", "down"):
        return None
    if line:
        boundary, buf = line["current_price"], line["buffer"]
        source, basis = "trend_line", line["label"] + "在最后完成棒的投影；随每根完成棒重新计算线值"
    else:
        boundary = ref["high"] if direction == "up" else ref["low"]
        buf, source, basis = ref["buffer"], ref["source"], "破位参考边界与原固定缓冲；不代替计划止损"
    return {"low": _round(max(tick, math.floor((boundary - buf) / tick + 1e-12) * tick)),
            "high": _round(math.ceil((boundary + buf) / tick - 1e-12) * tick),
            "label": "回踩观察带" if direction == "up" else "反弹观察带", "kind": "pullback" if direction == "up" else "rebound",
            "basis": basis, "source": source, "start_index": bars[-1]["index"], "end_index": bars[-1]["index"],
            "start_time": bars[-1]["time"], "end_time": bars[-1]["time"]}


def _structure_levels(bars, pivots, ref, price, zone, tick):
    """局部有效摆动与外层边界分层；不把每个两根枢轴都当作目标。

    摆动幅度只使用该枢轴左右已完成的两根确认棒。仅列最近40根内的摆动，
    近价簇合并；外层冻结边界独立保留，不被局部线覆盖。
    """
    first, last = bars[0]["index"], bars[-1]["index"]
    pools = {"low": [], "high": []}
    for p in pivots:
        if p["index"] < last - 40:
            continue
        j = p["index"] - first
        left, right = bars[j - 2:j], bars[j + 1:j + 3]
        if len(left) < 2 or len(right) < 2:
            continue
        strength = min(max(b["high"] for b in left) - p["price"], max(b["high"] for b in right) - p["price"]) if p["kind"] == "low" else min(
            p["price"] - min(b["low"] for b in left), p["price"] - min(b["low"] for b in right))
        atr = _atr(bars[:j + 3], tick)
        if strength < max(2 * tick, .5 * atr):
            continue
        pools[p["kind"]].append({"id": p["id"], "price": p["price"], "label": "局部历史支撑参考" if p["kind"] == "low" else "局部历史压力参考",
                                  "source": "confirmed_swing", "tier": "local", "index": p["index"], "time": p["time"],
                                  "confirmed_time": p["confirmed_time"], "strength": _round(strength),
                                  "basis": "近40根内、左右各2根确认，双侧摆动幅度不少于2跳或确认时0.5倍ATR；历史价位可能已被穿越，仅作分层观察"})
    cluster = max(tick, .25 * ref["atr"])
    for side, values in pools.items():
        merged = []
        for level in sorted(values, key=lambda x: x["price"]):
            if merged and level["price"] - merged[-1]["price"] <= cluster:
                if (level["strength"], level["index"]) > (merged[-1]["strength"], merged[-1]["index"]):
                    merged[-1] = level
            else:
                merged.append(level)
        pools[side] = merged
    outer = []
    for side in ("low", "high"):
        level = {"id": _id("outer", side, ref[side], ref["end_time"] or ref["end_index"]), "price": ref[side],
                 "label": "外层参考下沿" if side == "low" else "外层参考上沿", "source": ref["source"], "tier": "outer",
                 "index": ref["end_index"], "time": ref["end_time"], "basis": "原参考段边界；局部破线不等于该边界已破"}
        outer.append(level)
        pools[side] = [p for p in pools[side] if abs(p["price"] - level["price"]) > tick / 2] + [level]
    supports = sorted((p for p in pools["low"] if p["price"] < price), key=lambda x: x["price"], reverse=True)
    resistances = sorted((p for p in pools["high"] if p["price"] > price), key=lambda x: x["price"])
    return {"current_support": supports[0] if supports else None, "current_resistance": resistances[0] if resistances else None,
            "supports": supports, "resistances": resistances, "outer_low": outer[0], "outer_high": outer[1],
            "local_rebound": dict(zone) if zone and zone["source"] == "trend_line" else None,
            "all_supports": sorted(pools["low"], key=lambda x: x["price"], reverse=True),
            "all_resistances": sorted(pools["high"], key=lambda x: x["price"]),
            "basis": "局部摆动参考、外层参考边界与局部回测线分开；已在现价另一侧的价位不能作为该方向目标。"}


def _scenarios(phase, zone, ref, candidate, levels, price, last, tick):
    direction, buf = phase["direction"], ref["buffer"]
    scope = phase.get("scope", "reference")
    local = scope == "local"
    min_room = max(2 * tick, .25 * ref["atr"])
    outside_outer = local and (price < ref["low"] - buf or price > ref["high"] + buf)

    def select_target(side, gate, lower, upper):
        cutoff = max(price, gate, upper) if side == "up" else min(price, gate, lower)
        pool = levels["all_resistances"] if side == "up" else levels["all_supports"]
        eligible = [p for p in pool if (p["price"] - cutoff >= min_room - 1e-9 if side == "up" else cutoff - p["price"] >= min_room - 1e-9)]
        if eligible:
            return min(eligible, key=lambda p: p["price"]) if side == "up" else max(eligible, key=lambda p: p["price"]), "waiting"
        old_cutoff = max(last["close"], gate, upper) if side == "up" else min(last["close"], gate, lower)
        old = [p for p in pool if (p["price"] - old_cutoff >= min_room - 1e-9 if side == "up" else old_cutoff - p["price"] >= min_room - 1e-9)]
        passed = any(price >= p["price"] if side == "up" else price <= p["price"] for p in old)
        return None, "target_passed" if passed else "no_target"

    def branch(key, role, side, label, conditions, invalidation, points, gate, lower, upper, no_chase, priority,
               invalid=False, observing=False):
        target, status = select_target(side, gate, lower, upper)
        if invalid or outside_outer:
            status = "invalid"
        elif target and observing:
            status = "observing"
        drawable = target is not None and status in ("waiting", "observing")
        labels = {"waiting": "等待条件完成", "observing": "盘中观察，尚未完成确认", "invalid": "当前前提已失效，暂停路径",
                  "target_passed": "原参考位已被现价越过，暂停路径", "no_target": "缺少有足够间距的有效参考位"}
        if outside_outer:
            labels["invalid"] = "盘中已越过外层边界，局部路径暂停"
        validity = {"key": "live_premise", "label": "当前价仍满足本路径前提", "met": not invalid and not outside_outer, "source": "live"}
        target_condition = {"key": "target_side", "label": "参考位位于现价与触发区的正确方向且留有间距", "met": target is not None, "source": "live"}
        clean_points = []
        for value in [price] + points + ([target["price"]] if target else []):
            if _positive(value) and (not clean_points or abs(value - clean_points[-1]) > 1e-9):
                clean_points.append(_round(value))
        continuations = []
        if drawable:
            previous = target["price"]
            pool = levels["all_resistances"] if side == "up" else levels["all_supports"]
            for level in sorted(pool, key=lambda p: p["price"], reverse=side == "down"):
                distance = level["price"] - previous if side == "up" else previous - level["price"]
                if distance < max(min_room, buf + tick) - 1e-9:
                    continue
                threshold = previous + buf if side == "up" else max(tick, previous - buf)
                condition = f"先完成收盘{'高于' if side == 'up' else '低于'} {_fmt(threshold)}，越过 {_fmt(previous)} 的缓冲侧且后续未收回，才观察 {_fmt(level['price'])}"
                continuations.append({"from": _round(previous), "price": level["price"], "trigger": _round(threshold),
                                      "condition": condition, "confirmed": False, "source": dict(level)})
                previous = level["price"]
                if len(continuations) == 2:
                    break
        milestones = []
        if drawable:
            if len(clean_points) == 4:
                labels_at = ["现价", "回测观察带" if role == "retest" else "完成收盘越界", "完成受阻/守住" if role == "retest" else "回测守住收回侧", "首个参考位"]
            else:
                labels_at = ["现价"] + ["条件节点"] * (len(clean_points) - 2) + ["首个参考位"]
            milestones = [{"price": p, "label": labels_at[i], "confirmed": False} for i, p in enumerate(clean_points)]
        return {"key": key, "role": role, "scope": scope, "direction": side, "label": label, "status": status,
                "status_label": labels[status], "drawable": drawable, "priority": priority,
                "condition": "；".join(c["label"] for c in conditions), "conditions": [validity] + conditions + [target_condition],
                "invalidation": invalidation, "no_chase": no_chase, "trigger": {"price": _round(gate), "low": _round(lower), "high": _round(upper)},
                "target": target["price"] if drawable else None, "target_source": dict(target) if drawable else None,
                "target_basis": target["label"] + "：" + target["basis"] + "；只作逐层观察，不表示预测命中" if drawable else labels[status],
                "levels": [dict(p) for p in (levels["resistances"] if side == "up" else levels["supports"])],
                "path": clean_points if drawable else [], "milestones": milestones, "continuations": continuations,
                "at_reference": (f"若 {_fmt(target['price'])} 附近未继续{'上涨' if side == 'up' else '下跌'}，先观察守住/受阻及多根重叠；不直接判定反转或震荡。" if drawable else "等待新的有效参考位")}

    def future(key, label):
        return {"key": key, "label": label, "met": None, "source": "future"}

    def consolidation():
        active = bool(candidate and candidate["low"] <= price <= candidate["high"] and not outside_outer)
        status = "observing" if active else "invalid" if candidate else "waiting"
        condition = (f"已完成棒仅形成候选 {_fmt(candidate['low'])}–{_fmt(candidate['high'])}；后续仍须完成重叠、边界触碰与确认" if candidate else
                     "尚未形成有完成K线依据的整理区；等待至少3根窄幅重叠和后续边界证据，不从趋势线观察带拼出震荡区")
        path = [price, candidate["low"], candidate["high"]] if active else []
        return {"key": "consolidation", "role": "consolidation", "scope": "local", "direction": "range", "label": "整理候选继续观察" if candidate else "整理待形成（无可画区间）",
                "status": status, "status_label": "候选未确认" if active else "现价已离开候选，暂停路径" if candidate else "等待整理证据",
                "drawable": active, "priority": 1 if active else 4, "condition": condition,
                "conditions": [{"key": "candidate", "label": "已有完成棒整理候选", "met": candidate is not None, "source": "completed"},
                               {"key": "inside_candidate", "label": "现价仍处于候选范围", "met": active if candidate else None, "source": "live"}],
                "invalidation": f"现价或后续完成棒离开 {_fmt(candidate['low'])}–{_fmt(candidate['high'])}，暂停带内往返示意" if candidate else "未形成之前没有可沿用的整理上下沿",
                "no_chase": "单根不延续或长上影只提供线索，不能确认震荡。", "trigger": None,
                "target": None, "target_source": None, "target_basis": "整理候选边界，不是方向目标" if candidate else "无整理边界依据",
                "levels": [], "path": [_round(p) for p in path]}

    if direction in ("up", "down") and zone:
        up, lo, hi = direction == "up", zone["low"], zone["high"]
        gate = hi + tick if up else max(tick, lo - tick)
        no_chase = f"直接{'高开' if up else '低开'}并远离 {_fmt(lo)}–{_fmt(hi)} 时不追随、不把跳空视为确认；回测路径必须等价格实际返回及完成收盘证据。"
        invalid = price < lo if up else price > hi
        retest = branch("retest_" + direction, "retest", direction, "回踩守住后再看上行" if up else "回抽受阻后再看下行",
                        [future("return", f"先回测 {_fmt(lo)}–{_fmt(hi)}"), future("response", f"完成收盘{'高于' if up else '低于'} {_fmt(hi if up else lo)}，后续再{'上越' if up else '下越'} {_fmt(gate)}")],
                        f"现价{'低于' if up else '高于'} {_fmt(lo if up else hi)} 时暂停此路径；完成棒收回后重新分析", [(lo + hi) / 2, gate], gate, lo, hi,
                        no_chase, 1 if lo <= price <= hi else 2, invalid=invalid, observing=lo <= price <= hi)
        direct_gate = last["high"] + tick if up else max(tick, last["low"] - tick)
        direct = branch("direct_" + direction, "direct", direction, "直接上行，只观察不追" if up else "直接下行，只观察不追",
                        [future("no_retest", "若未先回测而继续同向运行"), future("extension", f"后续完成收盘{'高于' if up else '低于'}最近完成棒{'高点' if up else '低点'}外侧 {_fmt(direct_gate)}，才增加延续证据")],
                        f"现价{'低于' if up else '高于'} {_fmt(lo if up else hi)}，直接延续前提暂停", [], direct_gate, min(lo, direct_gate), max(hi, direct_gate),
                        "本分支承认价格可能直接延续；只观察逐层参考位，不提供追价许可。", 2 if lo <= price <= hi else 1, invalid=invalid,
                        observing=price > direct_gate if up else price < direct_gate)
        reverse = "down" if up else "up"
        reverse_gate = max(tick, lo - tick) if up else hi + tick
        reclaim_label = ("越过局部线→参考带内观察" if phase["key"].startswith("line_intact_") else "收回局部破位→参考带内观察") if local else "收回破位→原参考带内观察"
        reclaim = branch("reclaim_" + reverse, "reclaim", reverse, reclaim_label,
                         [future("reclaim", f"先完成收盘{'低于' if up else '高于'} {_fmt(lo if up else hi)}，再回测该区域守住收回侧"),
                          future("hierarchy", f"随后仅逐层观察参考带 {_fmt(ref['low'])}–{_fmt(ref['high'])} 内的{'支撑' if up else '压力'}；局部收回不代表外层反转")],
                         f"重新回到 {_fmt(hi if up else lo)} {'上方' if up else '下方'}且后续确认，原收回路径须重新核对", [reverse_gate, lo if up else hi], reverse_gate, lo, hi,
                         "先等待完成收回和回测；不把单次越线当作反转成立。", 3, observing=price < lo if up else price > hi)
        return [retest, direct, reclaim, consolidation()]
    result = []
    for side in ("up", "down"):
        up = side == "up"
        boundary = ref["high"] if up else ref["low"]
        gate = boundary + buf + tick if up else max(tick, boundary - buf - tick)
        result.append(branch("break_retest_" + side, "breakout", side, "外层上破后回踩观察" if up else "外层下破后反抽观察",
                             [future("break", f"先完成收盘{'高于' if up else '低于'} {_fmt(gate)}"), future("retest", f"之后回测 {_fmt(boundary)} 附近并完成守住/受阻确认")],
                             f"完成棒收回 {_fmt(boundary)} 原侧，突破路径失效", [gate, boundary], gate, max(tick, boundary - buf), boundary + buf,
                             "直接跳空远离外层边界不追随，等待回测。", 2))
    return result + [consolidation()]


def _live_watch(lines, live, index, price):
    for line in sorted(lines, key=lambda item: item["confirmed_index"], reverse=True):
        value, buf = _line_value(line, index), line["buffer"]
        if value <= 0:
            continue
        beyond = price > value + buf if line["kind"] == "resistance" else price < value - buf
        returned = price < value - buf if line["kind"] == "resistance" else price > value + buf
        if line["status"] in ("intact", "reclaimed") and beyond:
            label, direction = "盘中越线，未收盘确认", "up" if line["kind"] == "resistance" else "down"
        elif line["status"] in ("broken", "retested") and returned:
            label, direction = "盘中回到原侧，未收盘确认", "down" if line["kind"] == "resistance" else "up"
        else:
            continue
        return {"active": True, "label": label, "detail": "仅比较末根现价与已知趋势线投影；不改变完成K线阶段、事件或编号。",
                "direction": direction, "line_id": line["id"], "index": index, "time": live.get("dt") if isinstance(live, dict) else None,
                "price": _round(price), "line_price": _round(value), "confirmed": False}
    return None


def _select_scenario(scenarios, phase, zone, price):
    """默认相关性不能在同向参考耗尽时偷偷改成尚未发生的反向路径。"""
    by_role = {s["role"]: s for s in scenarios}
    candidate = by_role.get("consolidation")
    if candidate and candidate["drawable"]:
        return candidate["key"]
    if phase["direction"] not in ("up", "down") or not zone:
        return None
    retest, direct, reclaim = [by_role.get(role) for role in ("retest", "direct", "reclaim")]
    if zone["low"] <= price <= zone["high"] and retest and retest["status"] != "invalid":
        return retest["key"]
    on_reclaim_side = price < zone["low"] if phase["direction"] == "up" else price > zone["high"]
    if on_reclaim_side and reclaim and reclaim["status"] != "invalid":
        return reclaim["key"]
    if direct and direct["status"] != "invalid":
        return direct["key"]  # no_target / target_passed 仍保留同向文字，不强画反弹。
    return None


def build_market_structure(bars, tick=1.0, period=1800, reference=None):
    """返回 JSON 安全的可视化证据；不修改输入，不读写状态或连接行情。

    indices 相对原 bars；时间为输入 dt（缺失时 None）。reference 若提供时间，
    它必须能定位于缓存或位于缓存之前；已离窗参考最好同时传入其冻结 atr/buffer。
    """
    price = bars[-1].get("close") if isinstance(bars, (list, tuple)) and bars and isinstance(bars[-1], dict) else None
    price = _round(price) if _positive(price) else None
    if not _positive(tick) or not _positive(period) or not isinstance(bars, (list, tuple)) or len(bars) < MIN_COMPLETED + 1:
        return _empty(period if _positive(period) else None, price, "至少需要12根有效完成K线，另加1根当前K线；周期和跳动必须有效。")
    start = max(0, len(bars) - 1 - LOOKBACK)
    completed = [_clean(b, i) for i, b in enumerate(bars[start:-1], start)]
    if any(b is None for b in completed):
        return _empty(period, price, "完成K线含缺失OHLC或未完成标记；不跨越无效棒计算趋势线。")
    times = [b["time"] for b in completed]
    if any(t is not None for t in times):
        stamps = [_stamp(t) for t in times]
        if any(t is None for t in stamps) or any(b <= a for a, b in zip(stamps, stamps[1:])):
            return _empty(period, price, "完成K线时间缺失、重复或乱序，暂停结构推演。")
    price = completed[-1]["close"] if price is None else price
    ref = _reference(completed, reference, tick)
    if ref is None or ref["high"] - ref["low"] <= 2 * ref["buffer"]:
        return _empty(period, price, "参考边界时间不可定位或宽度不足以区分上下沿。")
    pivots = _pivots(completed)
    lines, line_events = _trend_lines(completed, pivots, tick)
    state, broke, candidate, events = _reference_state(completed, ref)
    phase, focus_line = _focus(state, ref, lines, candidate)
    if not candidate and focus_line and focus_line["status"] in ("broken", "retested"):
        line_break = next((b for b in completed if b["index"] == focus_line["break_index"]), None)
        if line_break:
            candidate = _pause_candidate(completed, line_break, focus_line["kind"] == "resistance", focus_line["atr"], focus_line["buffer"], "trend_line")
            if candidate:
                phase = _phase("line_pause_" + phase["direction"], "参考带内：局部破线后延续不足", phase["direction"], candidate["reason"])
                events.append(_event(completed[-1], "line_no_follow_through", candidate["label"], candidate["reason"], line_id=focus_line["id"]))
    phase.update(scope="local" if focus_line else "reference", reference_state=state)
    zone = _zone(phase, ref, focus_line, completed, tick)
    clues = []
    observed_break = next((b for b in completed if b["index"] == focus_line["break_index"]), None) if focus_line else broke
    if observed_break and not candidate and phase["direction"] in ("up", "down"):
        following = [b for b in completed if b["index"] > observed_break["index"]]
        scale_buffer = focus_line["buffer"] if focus_line else ref["buffer"]
        if 1 <= len(following) <= 2:
            no_progress = max(b["close"] for b in following) <= observed_break["close"] + scale_buffer if phase["direction"] == "up" else min(b["close"] for b in following) >= observed_break["close"] - scale_buffer
            if no_progress:
                clues.append({"key": "early_no_follow_through", "label": "暂未延续，整理证据不足",
                              "detail": f"破位后仅有{len(following)}根完成棒，收盘尚未继续远离破位价；不足以确认整理，仍需后续多棒重叠与边界证据。",
                              "index": completed[-1]["index"], "time": completed[-1]["time"], "price": _round(completed[-1]["close"]), "confirmed": True})
    for j in range(max(1, len(completed) - 3), len(completed)):
        b, prev = completed[j], completed[j - 1]
        width, body = b["high"] - b["low"], abs(b["close"] - b["open"])
        upper = b["high"] - max(b["open"], b["close"])
        if width > 0 and upper >= max(2 * tick, 2 * body, .45 * width):
            clues.append({"key": "upper_wick", "label": "长上影线索", "detail": "高位曾被触及后回收；单根影线不代表看空概率、反弹受阻确认或趋势反转。",
                          "index": b["index"], "time": b["time"], "price": _round(b["high"]), "confirmed": True})
        a, z = _stamp(prev["time"]), _stamp(b["time"])
        if a is not None and z is not None and z - a > 1.5 * period and abs(b["open"] - prev["close"]) > max(ref["buffer"], .5 * ref["atr"]):
            down = b["open"] < prev["close"]
            clues.append({"key": "session_gap_down" if down else "session_gap_up", "label": "时段间低开线索" if down else "时段间高开线索",
                          "detail": "仅记录新时段完成棒的开盘差；直接跳空远离观察带不替代回测证据。", "index": b["index"],
                          "time": b["time"], "price": _round(b["open"]), "confirmed": True})
    events = sorted(events + line_events, key=lambda e: (e["index"], e["kind"], e["id"]))[-8:]
    key_levels = _structure_levels(completed, pivots, ref, price, zone, tick)
    scenarios = _scenarios(phase, zone, ref, candidate, key_levels, price, completed[-1], tick)
    selected = _select_scenario(scenarios, phase, zone, price)
    hierarchy = {"scope": phase["scope"], "reference_state": state,
                 "label": "外层参考带内" if state == "inside" else "外层参考已出现完成棒越界或收回",
                 "direction": "neutral" if state == "inside" or state.endswith("reclaimed") else "up" if state.startswith("up_") else "down",
                 "low": ref["low"], "high": ref["high"], "local_label": phase["label"] if focus_line else None,
                 "local_direction": phase["direction"] if focus_line else "neutral"}
    return {"status": "ok", "version": VERSION, "period": period, "price": _round(price),
            "decision_bar_time": completed[-1]["time"], "completed_index": completed[-1]["index"],
            "atr": ref["atr"], "buffer": ref["buffer"], "reference": ref, "phase": phase,
            "pivots": pivots, "trend_lines": lines, "observation_zone": zone, "candidate": candidate,
            "clues": clues, "events": events, "live_watch": _live_watch(lines, bars[-1], len(bars) - 1, price),
            "scenarios": scenarios, "key_levels": key_levels, "selected_scenario": selected, "hierarchy": hierarchy,
            "rules": _rules()}
