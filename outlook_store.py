"""Persist completed-bar structure tracking, separately from manual positions."""
import copy
import json
import math
import os
from pathlib import Path
import threading

import outlook


_PHASES = {"inside", "up_break", "down_break", "up_hold", "down_hold",
           "up_extension", "down_extension", "up_failed", "down_failed"}


def _require(condition):
    if not condition:
        raise ValueError("invalid persisted structure")


def _text(value):
    return isinstance(value, str) and bool(value)


def _positive(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _time(value):
    # The core persists normalized Beijing times and compares them lexically.
    return _text(value) and outlook._tracking_time(value) == value


def _fields(value, rules):
    _require(isinstance(value, dict))
    for name, rule in rules.items():
        _require(name in value and rule(value[name]))


def _one_of(*values):
    return lambda value: value in values


def _optional(rule):
    return lambda value: value is None or rule(value)


def _records(value):
    _require(isinstance(value, list))
    for record in value:
        _fields(record, {"at": _time, **{name: _positive for name in ("open", "high", "low", "close")}})
        _require(outlook._clean_bar(record) is not None)
    _require(all(a["at"] < b["at"] for a, b in zip(value, value[1:])))


def _band(value, rules):
    _fields(value, {"low": _positive, "high": _positive, **rules})
    _require(value["low"] <= value["high"])


def _reject_constant(value):
    raise ValueError("non-finite JSON constant")


def _validate_state(key, state):
    # Check all serialized values as well as the required fields below; this
    # also rejects overflowed JSON numbers and non-finite extension fields.
    json.dumps(state, allow_nan=False)
    identity = json.loads(key, parse_constant=_reject_constant)
    _require(isinstance(identity, list) and len(identity) == 3)
    _require(_text(identity[0]) and _positive(identity[1]) and _positive(identity[2]))
    _fields(state, {"version": lambda v: _integer(v) and v > 0,
                    "symbol": _text, "period": _positive, "tick": _positive,
                    "previous": lambda v: isinstance(v, list),
                    "events": lambda v: isinstance(v, list)})
    _require([state["symbol"], state["period"], state["tick"]] == identity)
    _require("active" in state and "candidate" in state)
    if "pending_rebuild" in state:
        _require(_text(state["pending_rebuild"]))
    for archived in state["previous"]:
        _band(archived, {"id": _text, "label": _text, "start_time": _time,
                         "confirmed_at": _time, "end_time": _time, "end_reason": _text,
                         "status": _one_of("rebuilt", "replaced"), "phase": _one_of(*_PHASES)})
    for event in state["events"]:
        _fields(event, {"id": _text, "at": _time, "label": _text, "detail": _text,
                        "structure_id": _text, "price": _optional(_positive),
                        "kind": _one_of("phase", "candidate_cancelled", "reference_touch", "candidate",
                                        "structure_replaced", "rebuild", "structure_created")})
    if state["active"] is None:
        # A first insufficient-data response is durable too, but an empty or
        # partially damaged object must never masquerade as a fresh baseline.
        _require(_text(state.get("pending_rebuild")) and state["candidate"] is None)
        return
    _band(state["active"], {"id": _text, "label": _text, "phase": _one_of(*_PHASES),
                            "phase_label": _text, "kind": _one_of("range", "reference", "platform"),
                            "start_time": _time, "end_time": _time, "confirmed_at": _time, "reason": _text,
                            "atr": _positive, "buffer": _positive, "upper_target": _positive,
                            "lower_target": _optional(_positive), "upper_basis": _text, "lower_basis": _text,
                            "upper_touched": lambda v: isinstance(v, bool),
                            "lower_touched": lambda v: isinstance(v, bool),
                            "breakout_at": _optional(_time), "eligible_after": _optional(_time),
                            "eligible_kind": _one_of(None, "up", "down", "failed"), "consecutive": _integer})
    _fields(state, {"seen": lambda v: isinstance(v, list) and bool(v),
                    "forming": lambda v: isinstance(v, list), "pivots": lambda v: isinstance(v, list),
                    "changed_at": _time, "change_reason": _text, "rebuild_reason": _optional(_text)})
    _records(state["seen"])
    _records(state["forming"])
    for pivot in state["pivots"]:
        _fields(pivot, {"time": _time, "confirmed_time": _time, "price": _positive,
                        "kind": _one_of("high", "low")})
    candidate = state["candidate"]
    if candidate is not None:
        _band(candidate, {"id": _text, "label": _text, "status": _one_of("forming"),
                          "start_time": _time, "end_time": _time, "reason": _text,
                          "atr": _positive, "buffer": _positive, "bars": _integer,
                          "required_bars": lambda v: _integer(v) and v > 0, "confirmations": _integer,
                          "formation": lambda v: isinstance(v, list) and bool(v)})
        _records(candidate["formation"])


def _validate_store(data):
    _fields(data, {"version": lambda v: type(v) is int and v == 1,
                   "structures": lambda v: isinstance(v, dict)})
    for key, state in data["structures"].items():
        _validate_state(key, state)


class OutlookStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.data = {"version": 1, "structures": {}}
        self.load_error = None
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
                _validate_store(data)
                self.data = data
            except (OSError, ValueError, TypeError, KeyError):
                self.load_error = "结构记录读取失败，已暂停推演，请检查本地 state/outlook_state.json"

    def _save(self, data):
        _validate_store(data)
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temp.open("w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def analyze(self, bars, *, symbol, tick, period):
        """Only a changed completed-bar state causes a disk write.

        Callers serialize snapshot acquisition with this operation so an older
        concurrent HTTP request cannot feed stale bars after a newer snapshot.
        """
        with self.lock:
            if self.load_error:
                return {"status": "error", "message": self.load_error}
            try:
                _require(_text(symbol) and _positive(period) and _positive(tick))
                key = json.dumps([symbol, period, float(tick)], separators=(",", ":"), allow_nan=False)
                previous = self.data["structures"].get(key)
                analysis, state = outlook.advance_outlook(
                    bars, previous=copy.deepcopy(previous), symbol=symbol, tick=tick, period=period)
                _validate_state(key, state)
                if state != previous:
                    updated = copy.deepcopy(self.data)
                    updated["structures"][key] = state
                    self._save(updated)
                    self.data = updated
                return analysis
            except (OSError, ValueError, TypeError, KeyError):
                # Preserve the last durable state; never silently reset a broken
                # history or let an unrecorded structure enable a trade plan.
                return {"status": "error", "message": "结构记录更新失败，推演与实时计划已暂停，请检查本地存储及K线数据"}
