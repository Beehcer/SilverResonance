"""Read-only plan checks. No account access, orders, predictions or inferred levels.

The manual context premise covers the chosen mode and completed daily structure.
Only explicitly completed market bars establish the 30m -> 3m -> 3m sequence.
Bar event time is closed_at/end_time, falling back to dt for event-time fixtures.
Evaluation time is market.as_of, never the machine clock. Fees are round-trip per
lot; slippage_ticks is per side. Unknown inputs never become successful checks.
"""

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_RULE_PATH = Path(
    "C:/Users/Beech/Downloads/交易系统及评审skill和案例/期货交易系统：多周期结构与点3确认.md"
)
IMPLEMENTED_RULE_VERSION = "40a20922580911199dd607f171f53b78744be628a47e729288ed2b570b69f5ed"
RULE_COMPATIBILITY_REVIEW = {
    "reviewed_version": IMPLEMENTED_RULE_VERSION,
    "previous_version": "ea3300a9c7cef0ef11a623a3dfa35678f993b2c2112471c9067301c57bb3f45d",
    "reviewed_on": "2026-11-09",
    "sections": ["二6", "三3", "八4"],
    "reason": "本次补充推进高点与防守低点的人工选点定义，细化既有破位、回抽、再次转弱流程；不增加自动入场分支或放宽周期许可。",
    "mapping": [
        {"document": "对应防守低点事前已知，收盘破位只作所属层级转弱证据",
         "fields": ["main_level", "main_after", "main_buffer_ticks", "context_note"],
         "scope": "仅当参考属于30分钟时填入主结构位；3分钟对应点只能作局部记录，不替代主周期许可。程序验证参考时间之后的30分钟收盘越界。"},
        {"document": "推进段与防守低点对应，反弹未恢复原推进结构，所属模式合格",
         "fields": ["context_confirmed", "context_note", "mode", "direction"],
         "scope": "人工核对结构对应、预定收复与失效标准及其当前状态；不能把该人工前提显示为自动识别结论。前提不再成立时须撤销确认或停用计划。"},
        {"document": "主周期许可后回抽预定区，随后更晚的执行周期收盘转弱",
         "fields": ["entry_low", "entry_high", "stop_price", "trigger_price", "trigger_buffer_ticks", "activation_at"],
         "scope": "程序检查3分钟回撤入区且防守未失效，再检查更晚的闭合3分钟局部触发；当前价、信号价仍须在允许区，且通过个人风险。"},
    ],
    "not_automated": [
        "摆动高低点及其因果对应关系的自动识别",
        "原推进结构是否恢复、趋势线破坏与完整A/B形态的自动判定",
        "未写成预定防守条件的其他结构失效标准",
    ],
}
MODES = {"continuation", "reversal_a", "reversal_b", "range"}
PLAN_KEYS = {
    "board_id", "archived", "comments",
    "title", "enabled", "rule_version", "direction", "mode", "session_label",
    "expires_at", "context_confirmed", "context_note", "main_level", "main_after",
    "main_buffer_ticks", "entry_low", "entry_high", "trigger_price",
    "trigger_buffer_ticks", "stop_price", "obstacle_price", "planned_entry_price", "objects", "notes",
    "id", "symbol", "revision", "updated_by", "updated_at", "activated_snapshot", "activation_at",
}
RISK_KEYS = ("lots", "fee_per_lot", "slippage_ticks", "budget", "daily_limit", "daily_loss")
BASE_RISK_KEYS = RISK_KEYS[:4]


def _finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=SHANGHAI)
        return stamp.astimezone(SHANGHAI)
    except (ValueError, OverflowError, TypeError):
        return None


def _json_value(value, depth=0):
    if depth > 12:
        raise ValueError("内容层级过深")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > 20000:
            raise ValueError("文本过长")
        return value
    if isinstance(value, (int, float)):
        if not _finite(value):
            raise ValueError("数值必须有限")
        return value
    if isinstance(value, list) and len(value) <= 2000:
        return [_json_value(item, depth + 1) for item in value]
    if isinstance(value, dict) and len(value) <= 100:
        if any(not isinstance(key, str) for key in value):
            raise ValueError("对象字段名必须是字符串")
        return {key: _json_value(item, depth + 1) for key, item in value.items()}
    raise ValueError("内容不是有效的计划数据")


def validate_plan(raw):
    """Validate a partial draft without inventing missing premises or risk values."""
    if not isinstance(raw, dict):
        raise ValueError("计划必须是对象")
    if any(not isinstance(key, str) for key in raw):
        raise ValueError("计划字段名必须是文本")
    unknown = set(raw) - PLAN_KEYS
    if unknown:
        raise ValueError("未知计划字段：" + ", ".join(sorted(unknown)))
    plan = _json_value(raw)
    for key in ("title", "rule_version", "session_label", "context_note", "notes",
                "id", "symbol", "board_id"):
        if key in plan and plan[key] is not None and not isinstance(plan[key], str):
            raise ValueError(f"{key} 必须是文本")
    for key in ("enabled", "context_confirmed", "archived"):
        if key in plan and plan[key] is not None and not isinstance(plan[key], bool):
            raise ValueError(f"{key} 必须明确为是或否")
    for key, choices in (("direction", {"long", "short"}), ("mode", MODES)):
        if key in plan and plan[key] is not None and (not isinstance(plan[key], str) or plan[key] not in choices):
            raise ValueError(f"{key} 不受支持；不存在提前绕过确认的模式")
    for key in ("main_after", "expires_at", "updated_at", "activation_at"):
        if plan.get(key) is not None:
            stamp = _time(plan[key])
            if stamp is None:
                raise ValueError(f"{key} 必须是有效日期时间")
            plan[key] = stamp.isoformat()
    for key in ("main_level", "entry_low", "entry_high", "trigger_price", "stop_price", "obstacle_price", "planned_entry_price"):
        if plan.get(key) is not None and (not _finite(plan[key]) or plan[key] <= 0):
            raise ValueError(f"{key} 必须是有限正数")
    for key in ("main_buffer_ticks", "trigger_buffer_ticks"):
        if plan.get(key) is not None and (not _finite(plan[key]) or plan[key] < 0):
            raise ValueError(f"{key} 必须是有限非负数")
    if plan.get("revision") is not None:
        if not _finite(plan["revision"]) or not isinstance(plan["revision"], int) or plan["revision"] < 0:
            raise ValueError("revision 必须是非负整数")
    if plan.get("activated_snapshot") is not None and not isinstance(plan["activated_snapshot"], dict):
        raise ValueError("activated_snapshot 必须是对象")
    if plan.get("updated_by") is not None:
        actor = plan["updated_by"]
        if not isinstance(actor, (str, dict)) or (isinstance(actor, dict) and any(
                key not in {"id", "name"} or not isinstance(value, str) for key, value in actor.items())):
            raise ValueError("updated_by 必须是文本或含id/name的对象")
    lo, hi = plan.get("entry_low"), plan.get("entry_high")
    if lo is not None and hi is not None:
        if lo >= hi:
            raise ValueError("点3入场区下沿必须小于上沿")
        trigger, stop = plan.get("trigger_price"), plan.get("stop_price")
        if trigger is not None and not lo <= trigger <= hi:
            raise ValueError("局部触发价必须位于点3允许入场区")
        if stop is not None and plan.get("enabled") is True and plan.get("direction") in ("long", "short"):
            if (plan["direction"] == "long" and stop >= lo) or (plan["direction"] == "short" and stop <= hi):
                raise ValueError("保护止损必须位于点3允许入场区的防守侧")
    if plan.get("main_after") and plan.get("expires_at"):
        if _time(plan["expires_at"]) <= _time(plan["main_after"]):
            raise ValueError("有效期必须晚于参考条件确定时间")
    objects = plan.get("objects", [])
    comments = plan.get("comments", [])
    if not isinstance(comments, list) or len(comments) > 300:
        raise ValueError("计划讨论必须为最多 300 条的列表")
    for comment in comments:
        if (not isinstance(comment, dict) or not isinstance(comment.get("text"), str)
                or not 1 <= len(comment["text"]) <= 2000 or not _time(comment.get("at"))
                or not isinstance(comment.get("author"), dict)):
            raise ValueError("计划讨论格式无效")
    if not isinstance(objects, list) or len(objects) > 500:
        raise ValueError("绘图对象必须是最多500项的列表")
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get("kind"), str):
            raise ValueError("绘图对象缺少类型")
        if "locked" in obj and not isinstance(obj["locked"], bool):
            raise ValueError("锁定状态须明确为是或否")
        points = obj.get("points")
        if not isinstance(points, list) or not points or len(points) > 200:
            raise ValueError("绘图对象必须包含有效锚点")
        for point in points:
            if (not isinstance(point, dict) or not _finite(point.get("time"))
                    or not 0 <= point["time"] <= 253402300799000
                    or not _finite(point.get("price")) or point["price"] <= 0):
                raise ValueError("锚点时间须为毫秒时间戳，价格须为有限正数")
        if obj["kind"] == "segment":
            if len(points) != 2 or points[0]["price"] != points[1]["price"] or points[0]["time"] == points[1]["time"]:
                raise ValueError("水平线段须有两个等价、不同时间的端点")
        if obj["kind"] == "wave":
            ids = [p.get("id") for p in points]
            if (any(not isinstance(i, str) or not i or len(i) > 100 for i in ids)
                    or len(set(ids)) != len(ids)
                    or any(not isinstance(p.get("label"), str) or not 1 <= len(p["label"]) <= 12 for p in points)):
                raise ValueError("浪线节点须有固定且不重复的标识及编号")
        if obj["kind"] == "zone":
            if len(points) != 2 or points[0]["price"] == points[1]["price"] or points[0]["time"] == points[1]["time"]:
                raise ValueError("区域须有两个时间及价格均不同的边界点")
            for key, choices in (
                ("zone_type", {"entry", "danger", "obstacle", "invalid", "middle"}),
                ("direction", {"long", "short", "both"}),
                ("period", {"day", "m30", "m3", "all"}),
                ("effect", {"observe", "warn", "block"}),
            ):
                if not isinstance(obj.get(key), str) or obj[key] not in choices:
                    raise ValueError(f"区域 {key} 必须明确填写")
    plan.setdefault("objects", [])
    plan.setdefault("enabled", False)
    plan.setdefault("context_confirmed", None)
    plan.setdefault("main_buffer_ticks", 0)
    plan.setdefault("trigger_buffer_ticks", 0)
    return plan


def rule_manifest(path=None):
    """Report the exact rule document's content hash; missing files grant nothing."""
    target = Path(path) if path is not None else Path(os.environ.get("SILVER_RULES_PATH") or DEFAULT_RULE_PATH)
    result = {"title": "期货交易系统：多周期结构与点3确认", "version": None,
              "available": False, "path": str(target), "implemented_version": IMPLEMENTED_RULE_VERSION,
              "compatible": False, "compatibility_review": _json_value(RULE_COMPATIBILITY_REVIEW)}
    try:
        content = target.read_bytes()
        document = content.decode("utf-8-sig")
    except (OSError, UnicodeError):
        return result
    if not document.strip():
        return result
    heading = next((line[2:].strip() for line in document.splitlines() if line.startswith("# ")), result["title"])
    version = hashlib.sha256(content).hexdigest()
    compatible = version == IMPLEMENTED_RULE_VERSION
    result.update(title=heading, version=version, available=True, compatible=compatible,
                  message="规则文档与程序已核对，结构识别仍含人工前提" if compatible else "规则文档有更新，程序规则待核对")
    return result


def _bars(raw, as_of):
    """Validate all bars, then exclude unfinished bars from historical evidence."""
    if not isinstance(raw, list) or not raw:
        return [], False
    output = []
    seen = set()
    for bar in raw:
        if not isinstance(bar, dict) or not isinstance(bar.get("closed"), bool):
            return [], False
        stamp = _time(bar.get("closed_at") or bar.get("end_time") or bar.get("dt"))
        if stamp is None or any(not _finite(bar.get(key)) or bar[key] <= 0 for key in ("open", "high", "low", "close")):
            return [], False
        if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
            return [], False
        if bar["closed"]:
            if stamp in seen or (as_of is not None and stamp > as_of):
                return [], False
            seen.add(stamp)
            output.append({**bar, "_time": stamp})
    output.sort(key=lambda item: item["_time"])
    return output, True


def estimate_planned_risk(plan, market, risk):
    """A price-line estimate, independent of quote timing and entry permission."""
    entry, stop = plan.get("planned_entry_price"), plan.get("stop_price")
    result = {"entry_price": entry, "stop_price": stop, "complete": False,
              "valid": False, "qualified": False, "reason": "先在图上放置计划入场线与保护止损线",
              "net_obstacle_room": None, "permission": False}
    if not all(_finite(v) and v > 0 for v in (entry, stop)):
        return result
    sign = 1 if plan.get("direction") == "long" else -1 if plan.get("direction") == "short" else None
    if not sign:
        result["reason"] = "先明确计划做多还是做空"
        return result
    if (entry - stop) * sign <= 0:
        result["reason"] = "做多的止损须低于计划入场价" if sign > 0 else "做空的止损须高于计划入场价"
        return result
    tick, tick_value = market.get("tick"), market.get("tick_value")
    if not all(_finite(v) and v > 0 for v in (tick, tick_value)):
        result["reason"] = "缺少合约最小跳动与每跳价值，暂不能估算"
        return result
    for value in (entry, stop):
        ticks = value / tick
        if not _finite(ticks) or abs(ticks - round(ticks)) > 1e-7:
            result["reason"] = "计划入场与止损价格须符合合约最小跳动"
            return result
    result["valid"] = True
    daily = risk.get("daily_limit") is not None or risk.get("daily_loss") is not None
    result["daily_enabled"] = daily
    keys = RISK_KEYS if daily else BASE_RISK_KEYS
    if (any(not _finite(risk.get(k)) or risk[k] < 0 for k in keys)
            or risk["lots"] <= 0 or int(risk["lots"]) != risk["lots"]
            or risk["budget"] <= 0 or (daily and risk["daily_limit"] <= 0)):
        result["reason"] = "填写本人手数、费用、滑点和预算后可估算；每日限制须成对填写"
        return result
    lots = risk["lots"]
    price_risk = abs(entry - stop) / tick * tick_value * lots
    fees = risk["fee_per_lot"] * lots
    slippage = 2 * risk["slippage_ticks"] * tick_value * lots
    total = price_risk + fees + slippage
    remaining = risk["daily_limit"] - risk["daily_loss"] if daily else None
    obstacle = plan.get("obstacle_price")
    room = ((obstacle - entry) * sign / tick * tick_value * lots - fees - slippage
            if _finite(obstacle) and obstacle > 0 else None)
    if any(v is not None and not _finite(v) for v in (price_risk, fees, slippage, total, remaining, room)):
        result["reason"] = "数值超出计算范围，请核对计划价格与风控参数"
        return result
    qualified = total <= risk["budget"] and (not daily or total <= remaining)
    result.update(complete=True, price_risk=price_risk, fees=fees, slippage=slippage,
                  total_risk=total, remaining_daily=remaining, net_obstacle_room=room,
                  qualified=qualified,
                  reason="计划价止损估算在预算内；仍需结构、触发和现价核验" if qualified else "计划价止损估算已超过本人预算")
    return result


def evaluate_plan(plan, market, risk, rule_version):
    """Return evidence and personal eligibility, without performing any action."""
    result = {
        "status": "insufficient", "label": "条件未齐", "reason": "",
        "direction": None, "mode": None, "checks": [], "levels": {}, "risk": {}, "planned_risk": {},
        "next_action": "补全计划", "signal_key": None, "source": None,
    }
    issues = []

    def check(key, label, state, detail, when=None, severity=None):
        result["checks"].append({"key": key, "label": label, "state": state,
                                 "detail": detail, "time": when.isoformat() if isinstance(when, datetime) else when})
        if severity:
            issues.append((severity, detail))

    try:
        plan = validate_plan(plan)
    except ValueError as exc:
        result.update(status="invalid", label="计划无效", reason=str(exc), next_action="修正计划字段后重新检查")
        check("plan", "计划参数", "fail", str(exc))
        return result
    market = market if isinstance(market, dict) else {}
    risk = risk if isinstance(risk, dict) else {}
    result["planned_risk"] = estimate_planned_risk(plan, market, risk)
    direction, mode = plan.get("direction"), plan.get("mode")
    result.update(direction=direction, mode=mode, source=market.get("source"))
    sign = 1 if direction == "long" else -1 if direction == "short" else None
    as_of, after, expires = _time(market.get("as_of")), _time(plan.get("main_after")), _time(plan.get("expires_at"))
    activation = _time(plan.get("activation_at"))
    for key in ("main_level", "main_buffer_ticks", "entry_low", "entry_high", "trigger_price", "trigger_buffer_ticks", "stop_price", "obstacle_price", "planned_entry_price"):
        result["levels"][key] = plan.get(key)
    result["levels"]["price"] = market.get("price") if _finite(market.get("price")) else None
    check("enabled", "计划启用", "pass" if plan.get("enabled") is True else "wait",
          "计划已启用" if plan.get("enabled") is True else "计划未启用，仅供观察",
          severity=None if plan.get("enabled") is True else "waiting")
    if not isinstance(rule_version, str) or not rule_version:
        check("rules", "当前规则文件", "unknown", "当前规则文件不可用，不能核发许可", severity="insufficient")
    elif not plan.get("rule_version"):
        check("rules", "规则版本", "unknown", "计划未绑定规则版本", severity="insufficient")
    elif plan["rule_version"] != rule_version:
        check("rules", "规则版本", "fail", "规则版本已变化，请按当前规则重新确认计划", severity="invalid")
    else:
        check("rules", "规则版本", "pass", "计划已绑定当前规则版本")
    if as_of is None or expires is None or after is None:
        check("validity", "计划时限", "unknown", "须填写参考确定时间、有效期及行情检查时间", severity="insufficient")
    elif as_of >= expires:
        check("validity", "计划时限", "fail", "计划已到期，原许可取消", as_of, "invalid")
    elif as_of < after:
        check("validity", "计划时限", "wait", "尚未到计划参考确定时间", as_of, "waiting")
    else:
        check("validity", "计划时限", "pass", "计划在预定有效期内", as_of)
    source = market.get("source")
    if source == "mock":
        check("source", "行情来源", "warn", "模拟行情仅供演练，不能形成实盘许可", severity="waiting")
    elif source != "live":
        check("source", "行情来源", "unknown", "行情来源尚未确认", severity="insufficient")
    else:
        check("source", "行情来源", "pass", "真实行情来源；结构仍逐项核验")
    if market.get("fresh") is True:
        check("freshness", "行情时效", "pass", "行情适配器确认数据新鲜", as_of)
    elif market.get("fresh") is False:
        check("freshness", "行情时效", "fail", "行情过期或连接异常，原许可撤销", severity="invalid")
    else:
        check("freshness", "行情时效", "unknown", "行情时效未确认", severity="insufficient")
    if not isinstance(market.get("symbol"), str) or not market["symbol"].strip():
        check("symbol", "实际合约", "unknown", "行情未提供实际合约", severity="insufficient")
    elif plan.get("symbol") and market["symbol"] != plan["symbol"]:
        check("symbol", "实际合约", "fail", "行情合约与计划合约不一致", severity="invalid")
    else:
        check("symbol", "实际合约", "pass", market["symbol"])
    context = plan.get("context_confirmed")
    if context is True and (plan.get("context_note") or "").strip() and sign and mode:
        check("context", "人工方向与模式前提", "pass",
              "人工已确认已完成日线背景及模式前提；程序不自动认证趋势线或A/B分类：" + plan["context_note"])
    elif context is False:
        check("context", "人工方向与模式前提", "wait", "人工尚未确认日线方向及所属模式前提", severity="waiting")
    else:
        check("context", "人工方向与模式前提", "unknown", "须明确方向、模式及人工核对说明；未填写不等于不成立", severity="insufficient")
    periods = market.get("periods") if isinstance(market.get("periods"), dict) else {}
    day, day_ok = _bars(periods.get("day"), as_of)
    main_bars, main_ok = _bars(periods.get("m30"), as_of)
    execution, execution_ok = _bars(periods.get("m3"), as_of)
    past_day = [bar for bar in day if after and bar["_time"] <= after]
    if day_ok and past_day:
        check("daily", "已完成日线证据", "pass", "存在计划参考确定前已完成日线；方向判断仍为人工前提", past_day[-1]["_time"])
    else:
        check("daily", "已完成日线证据", "unknown", "缺少参考确定前的有效完成日线，不能使用当日未完成日K补判", severity="insufficient")
    if not main_ok or not main_bars:
        check("main_data", "30分钟数据", "unknown", "缺少有效的已完成30分钟K线", severity="insufficient")
    if not execution_ok or not execution:
        check("execution_data", "3分钟数据", "unknown", "缺少有效的已完成3分钟K线", severity="insufficient")
    price, tick, tick_value = (market.get(key) for key in ("price", "tick", "tick_value"))
    prices_ok = all(_finite(value) and value > 0 for value in (price, tick, tick_value))
    if not prices_ok:
        check("contract", "现价与合约参数", "unknown", "须提供有效现价、最小跳动及每跳每手价值", severity="insufficient")
    else:
        check("contract", "现价与合约参数", "pass", "现价、最小跳动及每跳每手价值齐全")
    required_levels = ("main_level", "entry_low", "entry_high", "trigger_price", "stop_price", "obstacle_price", "main_buffer_ticks", "trigger_buffer_ticks")
    levels_ok = sign is not None and all(plan.get(key) is not None for key in required_levels)
    if levels_ok and prices_ok:
        gates = [plan["main_level"] + sign * plan["main_buffer_ticks"] * tick,
                 plan["trigger_price"] + sign * plan["trigger_buffer_ticks"] * tick]
        if not all(_finite(value) for value in gates):
            levels_ok = False
            check("level_arithmetic", "结构参数范围", "unknown", "结构参数运算超出有限数值范围，须修正参数", severity="insufficient")
    if not levels_ok:
        check("levels", "预定结构与防守", "unknown", "主结构位、点3范围、局部触发、止损和最近障碍须事前填齐", severity="insufficient")
    planned_entry = plan.get("planned_entry_price")
    if planned_entry is not None:
        if not result["planned_risk"]["valid"]:
            check("planned_entry", "计划入场与防守", "fail", result["planned_risk"]["reason"], severity="invalid")
        elif plan.get("entry_low") is None or plan.get("entry_high") is None:
            check("planned_entry", "计划入场与防守", "unknown", "计划入场线已放置，仍须确定点3允许区", severity="insufficient")
        elif not plan["entry_low"] <= planned_entry <= plan["entry_high"]:
            check("planned_entry", "计划入场与防守", "fail", "计划入场价不在点3允许区内，须重新核对计划位置", severity="invalid")
        else:
            check("planned_entry", "计划入场与防守", "pass", "计划入场价在允许区；价格线不替代局部触发，正式风险继续按现价核算")
    main_event = retest_event = trigger_event = None
    if levels_ok and prices_ok and after and as_of:
        lo, hi, stop = plan["entry_low"], plan["entry_high"], plan["stop_price"]
        main_gate = plan["main_level"] + sign * plan["main_buffer_ticks"] * tick
        trigger_gate = plan["trigger_price"] + sign * plan["trigger_buffer_ticks"] * tick
        result["levels"].update(main_gate=main_gate, trigger_gate=trigger_gate)
        if not lo <= trigger_gate <= hi:
            check("trigger_geometry", "缓冲后触发位置", "fail", "缓冲后的触发价已离开点3区，不能追价触发", severity="invalid")
        candidates = [bar for bar in main_bars if bar["_time"] > after and (bar["close"] - main_gate) * sign > 0]
        if candidates:
            main_event = candidates[0]
            detail = "30分钟已收盘越过事前选定的局部结构位"
            if mode == "reversal_b":
                detail += "；B类不要求更大关键水平已破，原趋势线受损前提由人工确认"
            check("main", "30分钟主结构许可", "pass", detail, main_event["_time"])
        else:
            check("main", "30分钟主结构许可", "wait" if main_ok else "unknown",
                  "等待参考确定时间之后的30分钟收盘突破；3分钟不能补发主周期许可", severity="waiting" if main_ok else "insufficient")
        if main_event:
            execution_after = max(main_event["_time"], activation) if activation else main_event["_time"]
            previous = None
            for bar in execution:
                if bar["_time"] <= execution_after:
                    continue
                if retest_event is None:
                    touches = bar["low"] <= hi and bar["high"] >= lo
                    defensive_extreme = bar["low"] if sign > 0 else bar["high"]
                    if (touches and lo <= bar["close"] <= hi and (defensive_extreme - stop) * sign > 0
                            and (bar["close"] - trigger_gate) * sign <= 0):
                        retest_event = bar
                elif (previous and (previous["close"] - trigger_gate) * sign <= 0
                      and (bar["close"] - trigger_gate) * sign > 0 and lo <= bar["close"] <= hi):
                    trigger_event = bar
                    break
                previous = bar
        check("retest", "许可后的3分钟回撤验证", "pass" if retest_event else "wait",
              "已完成3分钟回撤进入允许区且守住预定防守" if retest_event else "等待30分钟许可及本版计划启用之后独立的3分钟回撤完成，不能与触发同根补齐",
              retest_event["_time"] if retest_event else None, None if retest_event else "waiting")
        check("trigger", "回撤后的3分钟收盘触发", "pass" if trigger_event else "wait",
              "后续已完成3分钟收盘越过局部触发位，且收盘仍在允许区" if trigger_event else "等待回撤完成后更晚一根3分钟收盘越过触发位；未完成K线不作触发",
              trigger_event["_time"] if trigger_event else None, None if trigger_event else "waiting")
        if trigger_event:
            departed = next((bar for bar in execution if bar["_time"] > trigger_event["_time"]
                             and not lo <= bar["close"] <= hi), None)
            check("signal_validity", "本轮触发是否仍有效", "fail" if departed else "pass",
                  "触发后已有3分钟收盘离开允许入场区，旧触发取消；回到区内也须另立计划重新验证" if departed
                  else "触发后尚无已完成3分钟收盘离区；完成事实持续保留",
                  departed["_time"] if departed else trigger_event["_time"], "invalid" if departed else None)
        stop_hits = [bar for bar in main_bars + execution if bar["_time"] > after
                     and ((bar["low"] if sign > 0 else bar["high"]) - stop) * sign <= 0]
        forming_hits = []
        for period, valid in (("m30", main_ok), ("m3", execution_ok)):
            if not valid:
                continue
            for bar in periods[period]:
                start = _time(bar.get("dt"))
                if (bar["closed"] is False and start and after <= start <= as_of
                        and ((bar["low"] if sign > 0 else bar["high"]) - stop) * sign <= 0):
                    forming_hits.append(start)
        if (price - stop) * sign <= 0 or stop_hits or forming_hits:
            when = min([bar["_time"] for bar in stop_hits] + forming_hits, default=as_of)
            detail = "当前价或计划生效后的K线已触及预定止损，本轮依据失效"
            if forming_hits:
                detail += "；形成中K线的盘中止损触及也有效，回升不恢复许可；不将其作为收盘确认"
            check("stop", "预定保护边界", "fail", detail, when, "invalid")
        else:
            check("stop", "预定保护边界", "pass", "现价及已完成证据未触发预定止损；不因未完成K线颜色撤销历史事实")
        if lo <= price <= hi:
            check("entry", "当前可入场位置", "pass", "当前价仍在预先允许的点3范围")
        else:
            check("entry", "当前可入场位置", "fail" if retest_event else "wait",
                  "现价已离开点3入场区，本次禁止追价" if retest_event else "价格尚未处于允许入场区，等待首次回撤机会",
                  severity="invalid" if retest_event else "waiting")
        if (plan["obstacle_price"] - price) * sign <= 0:
            check("obstacle", "预定最近障碍", "fail", "当前价已到达或越过预定障碍，不能换更远目标补空间", severity="invalid")
        else:
            check("obstacle", "预定最近障碍", "pass", "最近障碍由计划指定，尚未到达；不生成目标价")
    else:
        for key, label in (("main", "30分钟主结构许可"), ("retest", "许可后的3分钟回撤验证"), ("trigger", "回撤后的3分钟收盘触发")):
            check(key, label, "unknown", "必要参数或行情未齐，尚不能判断", severity="insufficient")
    active_zones = []
    if sign and _finite(price) and as_of:
        now_ms = as_of.timestamp() * 1000
        for obj in plan["objects"]:
            if obj["kind"] != "zone" or obj["direction"] not in (direction, "both"):
                continue
            p1, p2 = obj["points"]
            if (min(p1["time"], p2["time"]) <= now_ms <= max(p1["time"], p2["time"])
                    and min(p1["price"], p2["price"]) <= price <= max(p1["price"], p2["price"])):
                active_zones.append(obj)
                effect = obj["effect"]
                check("zone:" + str(obj.get("id", len(active_zones))), "当前方向的人工区域",
                      "fail" if effect == "block" else "warn" if effect == "warn" else "pass",
                      "现价位于" + {"day": "日线", "m30": "30分钟", "m3": "3分钟", "all": "跨周期"}[obj["period"]]
                      + {"entry": "入场", "danger": "危险", "obstacle": "障碍", "invalid": "失效", "middle": "中部"}[obj["zone_type"]]
                      + "区域，作用：" + {"observe": "观察", "warn": "提醒", "block": "禁止入场"}[effect]
                      + "；适用方向：" + {"long": "做多", "short": "做空", "both": "双向"}[obj["direction"]],
                      severity="waiting" if effect == "block" else None)
    result["levels"]["active_zone_ids"] = [obj.get("id") for obj in active_zones]
    daily_enabled = risk.get("daily_limit") is not None or risk.get("daily_loss") is not None
    required_risk = RISK_KEYS if daily_enabled else BASE_RISK_KEYS
    risk_missing = [key for key in required_risk if not _finite(risk.get(key))]
    risk_bad = [key for key in required_risk if _finite(risk.get(key)) and risk[key] < 0]
    if _finite(risk.get("lots")) and (risk["lots"] <= 0 or int(risk["lots"]) != risk["lots"]):
        risk_bad.append("lots")
    for key in ("budget", "daily_limit"):
        if _finite(risk.get(key)) and risk[key] <= 0:
            risk_bad.append(key)
    result["risk"] = {key: risk.get(key) if _finite(risk.get(key)) else None for key in RISK_KEYS}
    result["risk"].update(complete=not risk_missing and not risk_bad, qualified=False, daily_enabled=daily_enabled,
                          fee_basis="每手往返费用", slippage_basis="每侧跳数，入场和退出各计一次")
    if risk_missing or risk_bad:
        check("risk", "个人风险资料", "unknown", "个人风险参数须明确且有效：" + "、".join(dict.fromkeys(risk_missing + risk_bad)), severity="insufficient")
    elif levels_ok and prices_ok:
        lots = risk["lots"]
        price_risk = abs(price - plan["stop_price"]) / tick * tick_value * lots
        fee = risk["fee_per_lot"] * lots
        slippage = 2 * risk["slippage_ticks"] * tick_value * lots
        total = price_risk + fee + slippage
        remaining = risk["daily_limit"] - risk["daily_loss"] if daily_enabled else None
        room = (plan["obstacle_price"] - price) * sign / tick * tick_value * lots - fee - slippage
        projected = risk["daily_loss"] + total if daily_enabled else None
        ratio = room / total if total > 0 else None
        amounts = (price_risk, fee, slippage, total, remaining, room, projected, ratio)
        if any(value is not None and not _finite(value) for value in amounts):
            check("risk", "个人费用后风险", "unknown", "风险参数运算超出有限数值范围，须修正参数", severity="insufficient")
        else:
            qualified = total <= risk["budget"] and (not daily_enabled or total <= remaining) and room > 0
            result["risk"].update(price_risk=price_risk, fees=fee, slippage=slippage, total_risk=total,
                                  remaining_daily=remaining, projected_daily_loss=projected,
                                  net_obstacle_room=room, reward_risk=ratio, qualified=qualified)
            check("risk", "个人费用后风险", "pass" if qualified else "fail",
                  ("本人手数、往返费用及双侧滑点已计入；单笔预算合格且障碍前有净空间；"
                   + ("当日预算合格" if daily_enabled else "未启用当日止损上限")) if qualified else "计入费用与双侧滑点后，单笔预算、当日额度或障碍前净空间不合格",
                  severity=None if qualified else "invalid")
    else:
        check("risk", "个人费用后风险", "unknown", "缺少现价、合约参数或计划防守，不能计算个人风险", severity="insufficient")
    if trigger_event:
        identity = {"symbol": market.get("symbol"), "rule_version": plan.get("rule_version"),
                    "direction": direction, "mode": mode, "main_after": plan.get("main_after"),
                    "activation_at": plan.get("activation_at"),
                    "planned_entry_price": plan.get("planned_entry_price"),
                    "levels": {key: plan.get(key) for key in required_levels},
                    "main": main_event["_time"].isoformat(), "retest": retest_event["_time"].isoformat(),
                    "trigger": trigger_event["_time"].isoformat()}
        result["signal_key"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    if issues:
        rank = {"invalid": 0, "insufficient": 1, "waiting": 2}
        status, reason = min(issues, key=lambda issue: rank[issue[0]])
    else:
        status, reason = "ready", "人工模式前提及已完成K线顺序齐备，现价位置与个人风险合格"
    labels = {"ready": "条件齐备", "waiting": "等待条件", "invalid": "当前不可执行", "insufficient": "信息不足"}
    result.update(status=status, label=("模拟检查 · " if source == "mock" else "") + labels[status], reason=reason,
                  next_action="仅供人工决策；本面板不下单" if status == "ready" else reason)
    return result
