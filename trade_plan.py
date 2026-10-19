"""30分钟结构 + 3分钟确认的条件计划；纯函数，不处理账户、下单或胜率。

两组输入的最后一根均视为未完成。结构、确认和目标锚点只读完成棒；
当前价用于参考入场和当前失效/追价检查，不允许反向改变已完成的事实。
"""

import math
from datetime import datetime, timedelta, timezone

import outlook
import resonance


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _snap(value, tick, up=False):
    units = math.ceil(value / tick - 1e-10) if up else math.floor(value / tick + 1e-10)
    return round(units * tick, 8)


def _fmt(value):
    return "—" if value is None else f"{value:.2f}".rstrip("0").rstrip(".")


def _dt(bar):
    value = bar.get("dt") if isinstance(bar, dict) else None
    return value if isinstance(value, str) else None


def _reference_time(reference, edge, bars):
    stable = reference.get(f"{edge}_time")
    if isinstance(stable, str) and stable:
        return stable
    index = reference.get(f"{edge}_index")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(bars) - 1:
        return _dt(bars[index])
    return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone(timedelta(hours=8)))
        return stamp.timestamp()
    except (ValueError, OverflowError):
        return None


def _pivot_confirmed_before_reference(pivot, reference, bars):
    """Tracked pivots can outlive the visible window; -1 is never a bar index."""
    index = pivot.get("index")
    valid_index = isinstance(index, int) and not isinstance(index, bool) and index >= 0
    if reference.get("end_time") is not None:
        end_time = _timestamp(reference["end_time"])
        confirmed = _timestamp(pivot.get("confirmed_time"))
        if confirmed is None and valid_index and index + outlook.PIVOT_CONFIRM < len(bars) - 1:
            confirmed = _timestamp(_dt(bars[index + outlook.PIVOT_CONFIRM]))
        return end_time is not None and confirmed is not None and confirmed <= end_time
    # Legacy analyses use indices relative to the current input. Missing or
    # out-of-window indices cannot establish that a pivot was confirmed in time.
    end_index = reference.get("end_index")
    return (valid_index and isinstance(end_index, int) and not isinstance(end_index, bool)
            and index + outlook.PIVOT_CONFIRM <= end_index < len(bars) - 1)


def build_plan(bars, small_bars=None, tick=1.0, mode="confirmed", live_price=None, analysis=None):
    """固定30m主结构、3m企稳；mode='early'仅放宽确认，不放宽有效性门禁。

    entry 为当前参考入场价（买入向上、卖出向下按跳对齐），signal_price 为
    决策完成棒收盘价。reference 是冻结参考段首末时间，缺时间则为 None。
    target 仅来自观察段开始前已完成右侧确认的历史摆动障碍，不制造延伸目标。
    analysis 可注入完整的增量结构快照；缺省保留原 build_outlook 行为。
    增量快照以稳定时间筛选参考段和已确认目标，不把窗口外的 -1 当作当前棒。
    """
    result = {
        "status": "insufficient", "label": "数据不足", "mode": mode,
        "setup": "none", "direction": "none", "entry": None, "stop": None,
        "target": None, "target_basis": None, "signal_price": None,
        "summary": "等待至少40根完成30分K线及一根当前K线。", "wait_for": "等待足够的有效30分K线",
        "trigger": "", "invalidation": "", "conditions": [],
        "structure": {"label": "待识别", "summary": "仅完成K线用于确认"},
        "decision_bar_time": None, "reference": {"start": None, "end": None},
    }

    def finish(status, label, reason):
        result.update(status=status, label=label, summary=reason,
                      wait_for="" if status == "candidate" else reason)
        return result

    if mode not in ("confirmed", "early"):
        return finish("invalid", "模式无效", "模式必须为 confirmed 或 early")
    if not _number(tick):
        return finish("invalid", "合约参数无效", "最小跳动必须为有限正数")
    if analysis is None:
        analysis = outlook.build_outlook(bars, tick=tick, period=1800)
    if analysis["status"] != "ok":
        return finish("insufficient", "数据不足", analysis["state"]["summary"])
    if (not isinstance(bars, (list, tuple)) or len(bars) < 3
            or any(outlook._clean_bar(b) is None for b in bars[:-1])):
        return finish("insufficient", "数据不足", "结构快照仍需至少两根有效完成30分K线及一根当前K线")
    closed = bars[:-1]
    current = live_price if live_price is not None else bars[-1].get("close")
    if not _number(current):
        return finish("invalid", "现价无效", "等待有效实时价格，不能用旧收盘价代替当前可入场价")
    signal = float(closed[-1]["close"])
    result["signal_price"] = signal
    result["decision_bar_time"] = _dt(closed[-1])
    ref = analysis["range"]
    result["reference"] = {"start": _reference_time(ref, "start", bars),
                           "end": _reference_time(ref, "end", bars)}
    result["structure"] = {"label": analysis["state"]["label"], "summary": analysis["state"]["summary"]}
    high, low = ref["high"], ref["low"]
    atr, buffer = analysis["atr"], analysis["buffer"]
    state = analysis["state"]["key"]
    width = high - low
    if width <= 2 * buffer:
        return finish("waiting", "参考结构过窄", f"支撑 {_fmt(low)} 与阻力 {_fmt(high)} 的间隔不足两倍确认缓冲 {_fmt(buffer)}")
    zone = max(0.75 * atr, 2 * tick)
    direction, setup, anchor, retest = "none", "none", None, False
    if state in ("up_hold", "up_break", "up_extension"):
        direction, anchor = "long", high
        retest = state == "up_hold"
        setup = "retest_confirm" if retest else "breakout_probe"
    elif state in ("down_hold", "down_break", "down_extension"):
        direction, anchor = "short", low
        retest = state == "down_hold"
        setup = "retest_confirm" if retest else "breakout_probe"
    elif state in ("up_failed", "down_failed"):
        return finish("invalid", "原突破已失效", analysis["state"]["summary"])
    elif mode == "early":
        last, prev = closed[-1], closed[-2]
        # 仅靠“没有上破”不能做空；边界试探还需完成棒自身出现相应拒绝方向。
        if abs(signal - low) <= zone and last["close"] > last["open"] and last["close"] > prev["close"]:
            direction, anchor, setup = "long", low, "boundary_probe"
        elif abs(signal - high) <= zone and last["close"] < last["open"] and last["close"] < prev["close"]:
            direction, anchor, setup = "short", high, "boundary_probe"
    if direction == "none":
        result["conditions"] = [{"label": "完成30分K线形成突破后回踩/反抽确认", "met": False}]
        return finish("waiting", "等待结构触发", f"参考带 {_fmt(low)}–{_fmt(high)} 内尚无有效触发；未上破不等于做空。")
    sign = 1 if direction == "long" else -1
    entry = _snap(current, tick, up=sign > 0)
    stop = _snap(anchor - sign * buffer, tick, up=sign < 0)
    result.update(direction=direction, setup=setup, entry=entry, stop=stop)
    # 首个障碍相对完成棒选定，不能因实时价越过它就悄悄改成更远的目标。
    candidates = [p for p in analysis["pivots"]
                  if p["kind"] == ("high" if sign > 0 else "low")
                  and _pivot_confirmed_before_reference(p, ref, bars)
                  and (p["price"] - signal) * sign > 0]
    candidates.sort(key=lambda p: (p["price"] - signal) * sign)
    target = _snap(candidates[0]["price"], tick, up=sign < 0) if candidates else None
    result["target"] = target
    result["target_basis"] = "观察段开始前已确认的最近历史摆动障碍" if target is not None else "缺少盈利侧历史障碍；不使用参考延伸代替目标"

    small_ready = (isinstance(small_bars, (list, tuple)) and len(small_bars) >= 8
                   and all(outlook._clean_bar(b) is not None for b in small_bars[:-1]))
    small_ok = bool(small_ready and resonance.trigger_ok(small_bars[:-1], direction, tick=tick))
    price_gate = anchor + sign * buffer
    confirmed_structure = retest
    result["trigger"] = (f"30分完成K线{'上破后回踩守住' if sign > 0 else '下破后反抽受阻'} {_fmt(anchor)}，"
                         f"收盘{'高于' if sign > 0 else '低于'} {_fmt(price_gate)}；另需3分完成K线连续企稳。")
    result["invalidation"] = (f"当前价{'到达或低于' if sign > 0 else '到达或高于'} {_fmt(stop)}，结构计划失效；"
                              + (f"已到达目标 {_fmt(target)} 则本次入场空间失效。" if target is not None else "缺少历史目标时不形成候选。"))
    conditions = [
        {"label": "30分结构使用完成K线，边界及ATR在观察段前固定", "met": True},
        {"label": "30分突破后的回踩/反抽已经完成且仍有效", "met": confirmed_structure},
        {"label": "3分完成K线企稳确认" if small_ready else "3分完成K线数据足够且企稳确认", "met": small_ok},
        {"label": "存在盈利侧已确认历史目标", "met": target is not None},
    ]
    result["conditions"] = conditions
    if not _number(stop) or (current - stop) * sign <= 0 or (entry - stop) * sign <= 0:
        conditions.append({"label": "当前价与止损方向有效", "met": False})
        return finish("invalid", "结构止损已失效", f"现价 {_fmt(current)} 已触及或越过止损 {_fmt(stop)}，不能继续使用原计划。")
    if target is not None and ((target - current) * sign <= 0 or (target - entry) * sign <= 0):
        conditions[-1]["met"] = False
        return finish("invalid", "目标空间已失效", f"当前参考入场 {_fmt(entry)} 已到达或越过原目标 {_fmt(target)}，不能以更远目标替换原障碍。")
    max_distance = zone if setup == "boundary_probe" else max(1.5 * atr, 2 * tick)
    if abs(current - anchor) > max_distance:
        conditions.append({"label": f"现价距离结构边界不超过 {_fmt(max_distance)}", "met": False})
        return finish("waiting", "现价已离开入场区域", f"现价 {_fmt(current)} 距结构位 {_fmt(anchor)} 超过 {_fmt(max_distance)}；等待重新靠近并确认。")
    if setup != "boundary_probe" and (current - price_gate) * sign <= 0:
        conditions.append({"label": "当前价仍处于突破确认门槛外", "met": False})
        return finish("waiting", "当前仍待确认", f"完成棒证据保留，但现价 {_fmt(current)} 尚未{'站在' if sign > 0 else '回到'} {_fmt(price_gate)} {'上方' if sign > 0 else '下方'}。")
    if mode == "confirmed" and (not confirmed_structure or not small_ok):
        missing = []
        if not confirmed_structure:
            missing.append(f"30分后续回踩/反抽 {_fmt(anchor)} 确认")
        if not small_ok:
            missing.append("3分完成K线企稳")
        return finish("waiting", "等待确认", "仍缺少：" + "、".join(missing) + "。")
    if target is None:
        return finish("waiting", "缺少有效历史目标", "盈利侧未找到观察段前已确认的摆动障碍；不按风险倍数制造目标，继续等待。")
    missing = [c["label"] for c in conditions if not c["met"]]
    description = ("试探候选，尚缺：" + "、".join(missing) + "。") if missing else "30分结构及3分企稳均已确认。"
    return finish("candidate", "试探候选" if mode == "early" else "确认候选",
                  f"{description}当前参考入场 {_fmt(entry)}、结构止损 {_fmt(stop)}、历史障碍 {_fmt(target)}；仍须通过账户风险核验，规则不代表胜率。")
