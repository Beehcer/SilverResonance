"""独立模拟画板的共享计划存储与并发合并，不连接真实行情。"""
import copy
import json
import math
import os
from pathlib import Path
import queue
import re
import tempfile
import threading


SYMBOLS = {
    "DEMO.ag": {"name": "沪银", "title": "沪银模拟计划"},
    "DEMO.au": {"name": "沪金", "title": "沪金模拟计划"},
    "DEMO.rb": {"name": "螺纹钢", "title": "螺纹钢模拟计划"},
    "DEMO.SA": {"name": "纯碱", "title": "纯碱模拟计划"},
}
KINDS = {"horizontal", "trend", "reference", "path", "text"}
COLORS = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_document(symbol):
    return {"schema_version": 1, "id": "demo-current-plan", "title": SYMBOLS[symbol]["title"],
            "symbol": symbol, "period": 30, "notes": "", "revision": 0, "objects": []}


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _number(value, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else True))


def sanitize_object(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        raise ValueError("无效的画板对象")
    kind = raw["kind"]
    source = raw.get("points")
    if not isinstance(source, list):
        raise ValueError("画板锚点无效")
    points = []
    for item in source:
        if not isinstance(item, dict) or not _number(item.get("price"), positive=True):
            raise ValueError("画板锚点无效")
        time = item.get("time")
        if time is not None and (not _number(time) or time < 0):
            raise ValueError("画板时间无效")
        if kind != "horizontal" and time is None:
            raise ValueError("画板时间缺失")
        points.append({"time": time, "price": float(item["price"])})
    valid = ((kind in ("horizontal", "text") and len(points) == 1)
             or (kind in ("trend", "reference") and len(points) == 2)
             or (kind == "path" and len(points) >= 2))
    if not valid:
        raise ValueError("画板锚点数量无效")
    color = raw.get("color")
    if not isinstance(color, str) or not COLORS.fullmatch(color):
        raise ValueError("画板颜色无效")
    width = raw.get("width", 2)
    if not _number(width) or not 1 <= width <= 6:
        raise ValueError("画板线宽无效")
    return {
        "id": _text(raw.get("id"), 100), "kind": kind, "points": points,
        "name": _text(raw.get("name"), 80), "color": color.lower(),
        "width": int(round(width)), "line_style": "dashed" if raw.get("line_style") == "dashed" else "solid",
        "extend": raw.get("extend") is True, "visible": raw.get("visible") is not False,
        "period": raw.get("period") if raw.get("period") in ("day", "m3", "m30", "all") else "m30",
        "text_side": "left" if raw.get("text_side") == "left" else "right",
        "note": _text(raw.get("note"), 500),
        "z_index": int(raw.get("z_index", 0)) if _number(raw.get("z_index", 0)) else 0,
    }


def sanitize_document(symbol, raw):
    if not isinstance(raw, dict):
        raise ValueError("计划文档无效")
    objects = raw.get("objects")
    if not isinstance(objects, list):
        raise ValueError("计划对象列表无效")
    clean = [sanitize_object(item) for item in objects]
    ids = [item["id"] for item in clean]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("画板对象标识无效")
    revision = raw.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("计划修订号无效")
    return {"schema_version": 1, "id": "demo-current-plan",
            "title": _text(raw.get("title"), 100) or SYMBOLS[symbol]["title"],
            "symbol": symbol, "period": 30, "notes": _text(raw.get("notes"), 3000),
            "revision": revision, "objects": sorted(clean, key=lambda item: item["z_index"])}


class SharedPlanStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.plans = {}
        self.subscribers = {}
        self.load_error = None
        if self.path and self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if saved.get("version") != 1 or not isinstance(saved.get("plans"), dict):
                    raise ValueError("格式无效")
                for symbol, document in saved["plans"].items():
                    if symbol not in SYMBOLS:
                        raise ValueError("品种无效")
                    self.plans[symbol] = sanitize_document(symbol, document)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.load_error = f"共享计划读取失败：{type(exc).__name__}"

    def get(self, symbol):
        if symbol not in SYMBOLS:
            raise ValueError("未知模拟品种")
        if self.load_error:
            raise ValueError(self.load_error)
        with self.lock:
            return copy.deepcopy(self.plans.get(symbol, default_document(symbol)))

    def _save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "plans": self.plans}
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="plan-board-", suffix=".tmp", delete=False) as stream:
                temp = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def apply(self, symbol, payload, client_id=""):
        if not isinstance(payload, dict) or not isinstance(payload.get("base_revision"), int):
            raise ValueError("缺少计划基础修订号")
        objects = payload.get("objects", [])
        delete_ids = payload.get("delete_ids", [])
        base_objects = payload.get("base_objects", {})
        if not isinstance(objects, list) or not isinstance(delete_ids, list) or not isinstance(base_objects, dict):
            raise ValueError("计划变更格式无效")
        if len(objects) > 1000 or len(delete_ids) > 1000:
            raise ValueError("单次变更对象过多")
        changed = [sanitize_object(item) for item in objects]
        bases = {key: sanitize_object(value) for key, value in base_objects.items()}
        deleted = [_text(item, 100) for item in delete_ids]
        with self.lock:
            current = self.get(symbol)
            before = copy.deepcopy(current)
            current_by_id = {item["id"]: item for item in current["objects"]}
            conflicts = []
            for item in changed:
                object_id = item["id"]
                base = bases.get(object_id)
                existing = current_by_id.get(object_id)
                if base is not None and existing != base:
                    conflicts.append(object_id)
                elif base is None and existing is not None:
                    conflicts.append(object_id)
            for object_id in deleted:
                base = bases.get(object_id)
                existing = current_by_id.get(object_id)
                if base is None or (existing is not None and existing != base):
                    conflicts.append(object_id)
            for field in ("title", "notes"):
                if field in payload and current[field] != payload.get("base_" + field, ""):
                    conflicts.append(field)
            if conflicts:
                return current, sorted(set(conflicts))
            updated = current_by_id
            for object_id in deleted:
                updated.pop(object_id, None)
            for item in changed:
                updated[item["id"]] = item
            current["objects"] = sorted(updated.values(), key=lambda item: item["z_index"])
            for field in ("title", "notes"):
                if field in payload:
                    current[field] = _text(payload[field], 100 if field == "title" else 3000)
            current["revision"] += 1
            current = sanitize_document(symbol, current)
            try:
                self.plans[symbol] = current
                self._save()
            except Exception:
                self.plans[symbol] = before
                raise
            self._publish(symbol, current, client_id)
            return copy.deepcopy(current), []

    def _publish(self, symbol, document, client_id):
        event = {"symbol": symbol, "client_id": _text(client_id, 100), "plan": copy.deepcopy(document)}
        for subscriber in list(self.subscribers.get(symbol, set())):
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass

    def subscribe(self, symbol):
        if symbol not in SYMBOLS:
            raise ValueError("未知模拟品种")
        subscriber = queue.Queue(maxsize=20)
        with self.lock:
            self.subscribers.setdefault(symbol, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, symbol, subscriber):
        with self.lock:
            listeners = self.subscribers.get(symbol)
            if listeners is not None:
                listeners.discard(subscriber)
                if not listeners:
                    self.subscribers.pop(symbol, None)
