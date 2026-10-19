"""本地交易计划、风险预算与手工成交记录。不连接交易账户、不发送委托。"""
import copy
from datetime import datetime, timezone, timedelta
from functools import wraps
import json
import math
from pathlib import Path
import re
import threading
import uuid

BEIJING = timezone(timedelta(hours=8))
DEFAULTS = {"equity": 50000.0, "max_loss": 1000.0, "slippage_ticks": 2.0, "fees": {}}


def number(value, name, minimum=0, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{name}必须是有效数字")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}必须是有效数字") from None
    if not math.isfinite(value) or value < minimum or (positive and value <= minimum):
        raise ValueError(f"{name}超出有效范围")
    return value


def fee_key(symbol):
    return re.sub(r"\d+$", "", symbol)


def now():
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def aligned(value, tick, name):
    value = number(value, name, positive=True)
    if abs(value / tick - round(value / tick)) > 1e-6:
        raise ValueError(f"{name}须符合最小跳动 {tick:g}")
    return round(round(value / tick) * tick, 8)


def _remaining_fee_per_lot(position, settings):
    """Reserve only the unpaid part of the round-trip fee estimate.

    entry_fee is the total actually paid for initial_lots. Partial closes must
    not divide it by the smaller remaining quantity. Legacy records without a
    reliable entry-fee flag retain the full estimate conservatively.
    """
    estimate = settings.get("fees", {}).get(fee_key(position["symbol"]), 0)
    recorded = position.get("entry_fee_recorded", position.get("fees_recorded", False))
    if not recorded:
        return estimate
    initial_lots = position.get("initial_lots", position["lots"])
    paid_per_lot = position.get("entry_fee", 0) / initial_lots if initial_lots else 0
    return max(0.0, estimate - paid_per_lot)


def _unrecorded_closed_fee_reserve(position, settings):
    """Estimate missing actual fees for closed lots without changing ledger P&L."""
    initial = position.get("initial_lots", position["lots"])
    closed = max(0, initial - position["lots"])
    if not closed:
        return 0.0
    estimate = settings.get("fees", {}).get(fee_key(position["symbol"]), 0)
    entry_recorded = position.get("entry_fee_recorded", position.get("fees_recorded", False))
    if entry_recorded:
        recorded_lots = position.get("exit_fee_recorded_lots", closed if position.get("fees_recorded") else 0)
        missing_lots = max(0, closed - recorded_lots)
        return missing_lots * max(0.0, estimate - position.get("entry_fee", 0) / initial)
    # Missing entry fees: reserve the round-trip estimate for closed lots less
    # actual close fees already present in realized_pnl. Unknown legacy fields
    # conservatively receive no deduction.
    return max(0.0, closed * estimate - position.get("exit_fees", 0))


def _transactional(method):
    """Commit in-memory mutations only when the atomic file save succeeds."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            if self.load_error:
                raise ValueError(self.load_error)
            before = copy.deepcopy(self.data)
            try:
                return method(self, *args, **kwargs)
            except Exception:
                self.data = before
                raise
    return wrapped


def risk_for_plan(plan, settings, preset, positions=(), idea=None):
    """按结构止损估计计划风险；未知费用不默认为已完成核算。"""
    tick, tv = float(preset["tick"]), float(preset["tick_value"])
    equity, cap = settings["equity"], min(settings["max_loss"], settings["equity"])
    fee = settings.get("fees", {}).get(fee_key(preset["symbol"]))
    slip = settings["slippage_ticks"]
    active = [p for p in positions if p.get("status") == "open"]

    def exposure(p):
        sign = 1 if p["direction"] == "long" else -1
        # 历史成交记录锁定合约参数；不能随用户切换品种改变。
        px_tick, px_tv = p["tick"], p["tick_value"]
        p_fee = _remaining_fee_per_lot(p, settings)
        gross = max(0, (p["entry"] - p["stop"]) * sign) / px_tick * px_tv
        return (gross + p_fee + slip * px_tv) * p["lots"]

    idea_id = (idea or {}).get("id")
    open_risk = sum(exposure(p) for p in active if p["symbol"] == preset["symbol"]
                    and (not idea_id or p.get("idea_id") == idea_id))
    recorded_pnl = (idea or {}).get("realized_pnl", 0.0)
    missing_fee_reserve = (idea or {}).get("unrecorded_fee_reserve", 0.0)
    realized_loss = max(0.0, -recorded_pnl + missing_fee_reserve)
    used = open_risk + realized_loss
    result = {"equity": equity, "budget": cap, "max_loss": cap,
              "fee_per_lot": fee, "fee_configured": fee is not None,
              "slippage_ticks": slip, "rows": [], "max_lots": 0,
              "open_risk": round(open_risk, 2), "realized_loss": round(realized_loss, 2),
              "recorded_realized_loss": round(max(0.0, -recorded_pnl), 2),
              "unrecorded_fee_reserve": round(missing_fee_reserve, 2),
              "fee_reserve_note": "漏录的已平仓手续费按配置估计并占用风险预算；不改写手工实际盈亏。",
              "portfolio_risk": round(sum(exposure(p) for p in active), 2),
              "remaining_budget": round(max(0, cap - used), 2), "net_rr": None,
              "reason": "等待有效的入场、止损和目标", "idea_id": idea_id,
              "estimate_note": "按结构止损估算，滑点为假设；实际成交损失可能更大。保证金需在交易端核对。"}
    entry, stop, target = (plan.get(k) for k in ("entry", "stop", "target"))
    direction = plan.get("direction")
    if direction not in ("long", "short") or any(v is None for v in (entry, stop, target)):
        return result
    sign = 1 if direction == "long" else -1
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (entry, stop, target)):
        return result
    if (entry - stop) * sign <= 0 or (target - entry) * sign <= 0:
        result["reason"] = "止损或目标方向无效，停止生成可执行计划"
        return result
    ticks = math.ceil(abs(entry - stop) / tick - 1e-9)
    cost = (fee or 0) + slip * tv
    loss = ticks * tv + cost
    reward = (target - entry) * sign / tick * tv - cost
    result["net_rr"] = round(reward / loss, 3) if loss > 0 else None
    for lots in (1, 2):
        new_loss = loss * lots
        total = used + new_loss
        allowed = fee is not None and total <= cap + 1e-8 and reward > 0
        result["rows"].append({"lots": lots, "loss": round(new_loss, 2),
                               "total_loss": round(total, 2), "percent": round(new_loss / equity * 100, 3),
                               "total_percent": round(total / equity * 100, 3), "allowed": allowed})
        if allowed:
            result["max_lots"] = lots
    if fee is None:
        result["reason"] = "请填写该品种每手往返手续费；当前金额尚未包含手续费"
    elif reward <= 0:
        result["reason"] = "最近目标扣除费用与滑点后没有净盈利空间"
    elif result["max_lots"] == 0:
        result["reason"] = "1手已超过当前计划剩余风险预算"
    else:
        result["reason"] = f"预算内最多 {result['max_lots']} 手；已合并同计划试仓、加仓和重试风险"
    return result


class TradingStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"version": 1, "settings": copy.deepcopy(DEFAULTS), "positions": [],
                     "history": [], "ideas": {}, "signals": []}
        self.load_error = None
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if saved.get("version") != 1 or not isinstance(saved.get("settings"), dict):
                    raise ValueError("不支持的记录格式")
                self.data.update(saved)
                self.data["settings"] = {**copy.deepcopy(DEFAULTS), **saved["settings"]}
            except (OSError, ValueError, TypeError) as exc:
                # 不覆盖损坏文件，不把丢失记录伪装为已经清空。
                self.load_error = f"本地计划记录读取失败：{type(exc).__name__}，请检查 state/trading_state.json"

    def _save(self):
        if self.load_error:
            raise ValueError(self.load_error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temp.replace(self.path)

    def settings(self, symbol=None):
        with self.lock:
            result = copy.deepcopy(self.data["settings"])
        if symbol:
            result["fee_per_lot"] = result["fees"].get(fee_key(symbol))
        return result

    @_transactional
    def update_settings(self, payload, symbol=None):
        with self.lock:
            settings = copy.deepcopy(self.data["settings"])
            for key, label in (("equity", "账户权益"), ("max_loss", "单笔计划亏损上限")):
                if key in payload:
                    settings[key] = number(payload[key], label, positive=True)
            if settings["max_loss"] > settings["equity"]:
                raise ValueError("单笔计划亏损上限不能大于账户权益")
            if "slippage_ticks" in payload:
                settings["slippage_ticks"] = number(payload["slippage_ticks"], "往返滑点预留")
                if settings["slippage_ticks"] > 100:
                    raise ValueError("滑点预留不能超过100跳")
            if "fee_per_lot" in payload:
                if not symbol:
                    raise ValueError("请指定手续费对应品种")
                if payload["fee_per_lot"] in (None, ""):
                    settings["fees"].pop(fee_key(symbol), None)
                else:
                    settings["fees"][fee_key(symbol)] = number(payload["fee_per_lot"], "每手往返手续费")
            self.data["settings"] = settings
            self._save()
            return self.settings(symbol)

    def idea(self, symbol):
        with self.lock:
            result = copy.deepcopy(self.data["ideas"].get(symbol))
            if result:
                result["unrecorded_fee_reserve"] = round(sum(
                    _unrecorded_closed_fee_reserve(p, self.data["settings"])
                    for p in self.data["positions"]
                    if p["symbol"] == symbol and p.get("idea_id") == result["id"]), 2)
            return result

    @_transactional
    def new_idea(self, symbol):
        with self.lock:
            if any(p["status"] == "open" and p["symbol"] == symbol for p in self.data["positions"]):
                raise ValueError("该品种仍有登记持仓，请平仓记录完成后再开始新计划")
            self.data["ideas"][symbol] = {"id": uuid.uuid4().hex, "symbol": symbol,
                                            "started_at": now(), "realized_pnl": 0.0}
            self._save()
            return self.idea(symbol)

    def open_positions(self):
        with self.lock:
            return copy.deepcopy([p for p in self.data["positions"] if p["status"] == "open"])

    @_transactional
    def register(self, payload, preset):
        symbol = preset["symbol"]
        direction = payload.get("direction")
        if direction not in ("long", "short"):
            raise ValueError("方向须为做多或做空")
        lots = number(payload.get("lots"), "手数", positive=True)
        if lots not in (1, 2):
            raise ValueError("每次登记须为1手或2手")
        tick = preset["tick"]
        entry = aligned(payload.get("entry"), tick, "成交价")
        stop = aligned(payload.get("stop"), tick, "计划止损")
        target = aligned(payload["target"], tick, "计划目标") if payload.get("target") not in (None, "") else None
        sign = 1 if direction == "long" else -1
        if (entry - stop) * sign <= 0:
            raise ValueError("首次登记止损须位于成交价的亏损方向；后续可更新为保护盈利止损")
        if target is not None and (target - entry) * sign <= 0:
            raise ValueError("计划目标须位于成交价的盈利方向")
        mode = payload.get("mode", "confirmed")
        if mode not in ("early", "confirmed"):
            raise ValueError("未知入场模式")
        entry_fee = number(payload.get("entry_fee", 0), "实际开仓手续费")
        with self.lock:
            if symbol not in self.data["ideas"]:
                self.data["ideas"][symbol] = {"id": uuid.uuid4().hex, "symbol": symbol,
                                                "started_at": now(), "realized_pnl": 0.0}
            idea = self.data["ideas"][symbol]
            position = {"id": uuid.uuid4().hex, "idea_id": idea["id"], "symbol": symbol,
                        "mode": mode, "direction": direction, "lots": int(lots), "initial_lots": int(lots),
                        "entry": entry, "stop": stop, "initial_stop": stop, "target": target,
                        "tick": tick, "tick_value": preset["tick_value"], "opened_at": now(),
                        "status": "open", "realized_pnl": -entry_fee, "entry_fee": entry_fee,
                        "entry_fee_recorded": "entry_fee" in payload,
                        "exit_fees": 0.0, "exit_fee_recorded_lots": 0,
                        "fees_recorded": "entry_fee" in payload,
                        "initial_risk": round(abs(entry - stop) / tick * preset["tick_value"] * lots, 2)}
            idea["realized_pnl"] -= entry_fee
            self.data["positions"].append(position)
            self.data["history"].append({"action": "open", "at": now(), **copy.deepcopy(position)})
            self._save()
            return copy.deepcopy(position)

    @_transactional
    def update_position(self, position_id, payload):
        with self.lock:
            p = next((p for p in self.data["positions"] if p["id"] == position_id and p["status"] == "open"), None)
            if not p:
                raise ValueError("未找到仍持有的本地记录")
            action = payload.get("action")
            if action == "update_stop":
                old = p["stop"]
                p["stop"] = aligned(payload.get("stop"), p["tick"], "计划止损")
                self.data["history"].append({"action": action, "at": now(), "id": p["id"],
                    "idea_id": p["idea_id"], "symbol": p["symbol"], "old_stop": old, "stop": p["stop"]})
            elif action == "close":
                lots = number(payload.get("lots", p["lots"]), "平仓手数", positive=True)
                if not lots.is_integer() or lots > p["lots"]:
                    raise ValueError("平仓手数须为不超过持仓的整数")
                price = aligned(payload.get("price"), p["tick"], "实际平仓价")
                fee = number(payload.get("fee", 0), "本次实际平仓手续费")
                sign = 1 if p["direction"] == "long" else -1
                pnl = (price - p["entry"]) * sign / p["tick"] * p["tick_value"] * lots - fee
                prior_closed = p.get("initial_lots", p["lots"]) - p["lots"]
                recorded_lots = p.get("exit_fee_recorded_lots", prior_closed if p.get("fees_recorded") else 0)
                p["exit_fee_recorded_lots"] = recorded_lots + (int(lots) if "fee" in payload else 0)
                p["exit_fees"] = round(p.get("exit_fees", 0.0) + fee, 2)
                p["lots"] -= int(lots)
                p["realized_pnl"] = round(p["realized_pnl"] + pnl, 2)
                p["fees_recorded"] = p["fees_recorded"] and "fee" in payload
                if p["lots"] == 0:
                    p["status"] = "closed"
                    p["closed_at"] = now()
                idea = self.data["ideas"].get(p["symbol"])
                if idea and idea["id"] == p["idea_id"]:
                    idea["realized_pnl"] = round(idea["realized_pnl"] + pnl, 2)
                self.data["history"].append({"action": action, "at": now(), "id": p["id"],
                    "idea_id": p["idea_id"], "symbol": p["symbol"], "lots": int(lots),
                    "price": price, "fee": fee, "pnl": round(pnl, 2), "mode": p["mode"]})
            else:
                raise ValueError("未知持仓记录操作")
            self._save()
            return copy.deepcopy(p)

    def positions_view(self, symbol=None, prices=None):
        prices = prices or {}
        with self.lock:
            positions = copy.deepcopy([p for p in self.data["positions"] if p["status"] == "open" and (not symbol or p["symbol"] == symbol)])
            history = copy.deepcopy([h for h in self.data["history"] if not symbol or h["symbol"] == symbol][-60:])
            closed = [p for p in self.data["positions"] if p["status"] == "closed" and (not symbol or p["symbol"] == symbol)]
            all_rows = [p for p in self.data["positions"] if not symbol or p["symbol"] == symbol]
            settings = copy.deepcopy(self.data["settings"])
            stats = {"closed_trades": len(closed), "wins": sum(p["realized_pnl"] > 0 for p in closed),
                     "realized_pnl": round(sum(p["realized_pnl"] for p in all_rows), 2),
                     "unrecorded_fee_reserve": round(sum(_unrecorded_closed_fee_reserve(p, settings) for p in all_rows), 2),
                     "note": "仅统计手工登记成交，不是策略胜率；未录费用会使收益偏高。"}
        for p in positions:
            sign = 1 if p["direction"] == "long" else -1
            price = prices.get(p["symbol"])
            p["unrealized_pnl"] = round((price - p["entry"]) * sign / p["tick"] * p["tick_value"] * p["lots"], 2) if price else None
            fee = _remaining_fee_per_lot(p, settings)
            p["remaining_risk"] = round((max(0, (p["entry"] - p["stop"]) * sign) / p["tick"] * p["tick_value"] + fee + settings["slippage_ticks"] * p["tick_value"]) * p["lots"], 2)
            p["unrecorded_fee_reserve"] = round(_unrecorded_closed_fee_reserve(p, settings), 2)
            p["price"] = price
            p["stop_breached"] = price is not None and (price - p["stop"]) * sign <= 0
            p["target_reached"] = price is not None and p["target"] is not None and (price - p["target"]) * sign >= 0
            p["management"] = "价格已越过计划止损，请核对交易端持仓" if p["stop_breached"] else "已到计划目标，请核对退出条件" if p["target_reached"] else "按登记计划管理；本地记录不执行止损"
        return {"positions": positions, "history": history, "stats": stats, "load_error": self.load_error}

    @_transactional
    def record_signal(self, symbol, plan, risk, bar_time):
        """仅在完成棒/模式/状态改变时追加，不把每次轮询计成新信号。"""
        key = [symbol, plan.get("mode"), bar_time, plan.get("status"), plan.get("setup"), plan.get("direction")]
        with self.lock:
            prior = next((s for s in reversed(self.data["signals"]) if s["symbol"] == symbol and s["mode"] == plan.get("mode")), None)
            if prior and prior.get("key") == key:
                return
            self.data["signals"].append({"key": key, "at": now(), "symbol": symbol, "mode": plan.get("mode"),
                "status": plan.get("status"), "setup": plan.get("setup"), "direction": plan.get("direction"),
                "entry": plan.get("entry"), "stop": plan.get("stop"), "target": plan.get("target"),
                "summary": plan.get("summary"), "decision_bar_time": bar_time, "max_lots": risk.get("max_lots")})
            self.data["signals"] = self.data["signals"][-5000:]
            self._save()

    def signals(self, symbol=None):
        with self.lock:
            return copy.deepcopy([s for s in self.data["signals"] if not symbol or s["symbol"] == symbol][-100:])
