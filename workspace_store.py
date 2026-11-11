"""Anonymous browser identities and revisioned shared plans; no trading side effects."""
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from pathlib import Path
import secrets
import tempfile
import threading
import time

SESSION_SECONDS = 365 * 24 * 3600
PRESENCE_SECONDS = 15
_COLORS = ("#0f766e", "#2563eb", "#9333ea", "#db2777", "#b45309", "#0891b2", "#4f46e5", "#65a30d")
_METADATA = {"id", "symbol", "board_id", "revision", "updated_by", "updated_at", "activated_snapshot", "activation_at"}
_DECISION_FIELDS = {"rule_version", "direction", "mode", "session_label", "expires_at",
                    "context_confirmed", "context_note", "main_level", "main_after", "main_buffer_ticks",
                    "entry_low", "entry_high", "trigger_price", "trigger_buffer_ticks", "stop_price", "obstacle_price", "planned_entry_price"}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class WorkspaceError(Exception):
    status_code = 400


class WorkspaceUnavailable(WorkspaceError):
    status_code = 503


class AuthenticationError(WorkspaceError):
    status_code = 401


class PermissionError(WorkspaceError):
    status_code = 403


class RevisionConflict(WorkspaceError):
    status_code = 409

    def __init__(self, plan, conflicts=None):
        super().__init__("同一内容已被其他协作者修改，请检查冲突后再保存")
        self.plan = copy.deepcopy(plan)
        self.conflicts = conflicts or ["revision"]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def board_key(symbol, board_id=""):
    if not isinstance(board_id, str) or (board_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", board_id)):
        raise WorkspaceError("计划标识无效")
    return symbol + ("::" + board_id if board_id else "")


def _plan_key(plan):
    return board_key(plan["symbol"], plan.get("board_id", ""))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token(token):
    return _hash("workspace-csrf:" + token)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _require(condition):
    if not condition:
        raise ValueError("invalid persisted workspace")


def _public_member(member):
    return {key: member[key] for key in ("id", "name", "color", "role", "last_seen")}


def _decision_changed(current, updated):
    if any(current.get(key) != updated.get(key) for key in _DECISION_FIELDS):
        return True
    def blocks(plan):
        # Opacity changes visibility only; an invisible blocking region still
        # has the same trading meaning and must not reactivate the plan.
        return [{key: value for key, value in obj.items() if key != "opacity"}
                for obj in plan.get("objects", []) if obj.get("kind") == "zone" and obj.get("effect") == "block"]
    return blocks(current) != blocks(updated)


def _display_name(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or any(ord(c) < 32 for c in name):
        raise WorkspaceError("昵称需为 1–40 个字符")
    return name.strip()


def validate_risk(raw):
    """Explicit private fields only; missing values remain unset, never guessed."""
    fields = {"lots", "budget", "daily_limit", "daily_loss", "fee_per_lot", "slippage_ticks"}
    if not isinstance(raw, dict) or set(raw) - fields:
        raise WorkspaceError("个人风控字段无效")
    clean = {}
    for key, value in raw.items():
        if value is None:
            clean[key] = None
            continue
        if not _finite(value) or value < 0 or value > 1e12:
            raise WorkspaceError("个人风控数值无效")
        if key == "lots" and (not _integer(value) or not 1 <= value <= 100000):
            raise WorkspaceError("手数必须为正整数")
        if key in ("budget", "daily_limit") and value <= 0:
            raise WorkspaceError("风险预算与当日额度必须大于零")
        clean[key] = value
    return clean


class WorkspaceStore:
    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path) if self.path else id(self), threading.RLock())
        self._state = self._empty()
        self._presence = {}
        self._presence_lock = threading.RLock()
        self._load_error = False
        if self.path:
            try:
                with self._locked():
                    self._reload()
            except WorkspaceUnavailable:
                self._load_error = True

    @staticmethod
    def _empty():
        return {"schema_version": 2, "members": {}, "sessions": {},
                "plans": {}, "history": {}, "activations": {}, "mutations": {}}

    @contextmanager
    def _locked(self):
        """Serialize threads and processes sharing the same JSON path."""
        with self._lock:
            handle = None
            try:
                if self.path:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    handle = open(str(self.path) + ".lock", "a+b")
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
            except OSError as exc:
                raise WorkspaceUnavailable("工作区存储不可用，未保存修改") from exc
            finally:
                if handle is not None:
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        handle.close()

    def _validate_state(self, state):
        json.dumps(state, allow_nan=False)
        _require(isinstance(state, dict) and set(state) == set(self._empty()) and state["schema_version"] == 2)
        for key in ("members", "sessions", "plans", "history", "activations", "mutations"):
            _require(isinstance(state[key], dict))
        members = state["members"]
        _require(len(state["mutations"]) <= 2000)
        for key, receipt in state["mutations"].items():
            _require(isinstance(key, str) and len(key) == 64)
            _require(isinstance(receipt, dict) and set(receipt) == {"symbol", "user_id", "digest", "revision"})
            _require(receipt["symbol"] in state["plans"] and receipt["user_id"] in members)
            _require(isinstance(receipt["digest"], str) and len(receipt["digest"]) == 64)
            _require(_integer(receipt["revision"]) and receipt["revision"] <= state["plans"][receipt["symbol"]]["revision"])
        for identity, member in members.items():
            _require(set(member) == {"id", "name", "color", "role", "last_seen", "risk"})
            _require(member["id"] == identity and isinstance(identity, str) and identity)
            _require(isinstance(member["name"], str) and 1 <= len(member["name"]) <= 40)
            _require(member["role"] == "member" and isinstance(member["last_seen"], str))
            _require(isinstance(member["color"], str) and re.fullmatch(r"#[0-9a-f]{6}", member["color"]))
            validate_risk(member["risk"])
        for digest, session in state["sessions"].items():
            _require(isinstance(digest, str) and len(digest) == 64)
            _require(set(session) == {"user_id", "expires", "touched"})
            _require(session["user_id"] in members and _finite(session["expires"]) and _finite(session["touched"]))
        for symbol, plan in state["plans"].items():
            _require(isinstance(symbol, str) and _plan_key(plan) == symbol)
            _require(_integer(plan["revision"]) and plan["revision"] >= 1)
            _require(plan["updated_by"]["id"] in members and isinstance(plan["updated_at"], str))
            from workspace_rules import validate_plan
            validate_plan(plan)
            _require(symbol in state["history"] and symbol in state["activations"])
        _require(set(state["plans"]) == set(state["history"]) == set(state["activations"]))
        for symbol, history in state["history"].items():
            _require(isinstance(history, list) and 1 <= len(history) <= 200)
            last = 0
            for entry in history:
                _require(set(entry) == {"revision", "actor", "at", "action", "plan"})
                _require(_integer(entry["revision"]) and entry["revision"] > last)
                last = entry["revision"]
                _require(entry["actor"]["id"] in members and isinstance(entry["at"], str))
                _require(entry["action"] in ("created", "updated", "activated"))
                _require(entry["plan"]["revision"] == entry["revision"] and _plan_key(entry["plan"]) == symbol)
                validate_plan(entry["plan"])
                _require(entry["actor"] == entry["plan"]["updated_by"] and entry["at"] == entry["plan"]["updated_at"])
            _require(last == state["plans"][symbol]["revision"])
            _require(history[-1]["plan"] == state["plans"][symbol])
        for symbol, snapshots in state["activations"].items():
            _require(isinstance(snapshots, list))
            last = 0
            for snapshot in snapshots:
                _require(_plan_key(snapshot) == symbol and _integer(snapshot["revision"]) and snapshot["revision"] > last)
                last = snapshot["revision"]
                _require("activated_snapshot" not in snapshot)
                validate_plan(snapshot)
                _require(snapshot.get("enabled") is True and bool(snapshot.get("activation_at")))
                _require(snapshot["revision"] <= state["plans"][symbol]["revision"])
            active_snapshot = state["plans"][symbol].get("activated_snapshot")
            _require(active_snapshot == (snapshots[-1] if snapshots else None))

    def _reload(self):
        if self._load_error:
            raise WorkspaceUnavailable("工作区文件读取失败，请恢复备份后重启；禁止重新初始化")
        if not self.path:
            return
        try:
            if self.path.exists():
                def reject(value):
                    raise ValueError("invalid JSON constant")
                with self.path.open("r", encoding="utf-8") as stream:
                    state = json.load(stream, parse_constant=reject)
                migrated = isinstance(state, dict) and state.get("schema_version") == 1
                if migrated:
                    _require(set(state) - {"mutations"} == (set(self._empty()) - {"mutations"}) | {"invite_code"})
                    state.pop("invite_code")
                    for index, member in enumerate(state["members"].values()):
                        _require(set(member) == {"id", "name", "role", "last_seen", "password_hash", "risk"})
                        member.pop("password_hash")
                        member.update(role="member", color=_COLORS[index % len(_COLORS)])
                    state["schema_version"] = 2
                if isinstance(state, dict) and state.get("schema_version") == 2 and "mutations" not in state:
                    state["mutations"] = {}
                    migrated = True
                self._validate_state(state)
                if migrated:
                    self._persist(state)
                self._state = state
            elif self._state["members"]:
                raise ValueError("existing workspace disappeared")
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, AssertionError, WorkspaceError) as exc:
            self._load_error = True
            raise WorkspaceUnavailable("工作区文件读取失败，请恢复备份后重启；禁止重新初始化") from exc

    def _persist(self, state):
        if not self.path:
            return
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError) as exc:
            raise WorkspaceUnavailable("工作区写入失败，修改未保存") from exc
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, operation):
        with self._locked():
            self._reload()
            return copy.deepcopy(operation(self._state))

    def _change(self, operation):
        with self._locked():
            self._reload()
            updated = copy.deepcopy(self._state)
            result = operation(updated)
            try:
                self._validate_state(updated)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, WorkspaceError) as exc:
                raise WorkspaceUnavailable("工作区数据校验失败，修改未保存") from exc
            self._persist(updated)
            self._state = updated
            return copy.deepcopy(result)

    @staticmethod
    def _new_session(state, user_id):
        token = secrets.token_urlsafe(32)
        now = time.time()
        state["sessions"] = {key: session for key, session in state["sessions"].items() if session["expires"] > now}
        own = [(key, session) for key, session in state["sessions"].items() if session["user_id"] == user_id]
        for key, _ in sorted(own, key=lambda item: item[1]["touched"])[:-9]:
            del state["sessions"][key]
        state["sessions"][_hash(token)] = {"user_id": user_id, "expires": now + SESSION_SECONDS, "touched": now}
        state["members"][user_id]["last_seen"] = _now()
        return token

    def anonymous_session(self, token=None):
        if self.authenticate(token):
            return token
        def operation(state):
            identity = secrets.token_hex(12)
            state["members"][identity] = {
                "id": identity, "name": "协作者 " + identity[:4].upper(), "role": "member",
                "color": _COLORS[len(state["members"]) % len(_COLORS)], "last_seen": _now(), "risk": {},
            }
            return self._new_session(state, identity)
        return self._change(operation)

    def profile(self, user_id, name=None, color=None):
        name = _display_name(name) if name is not None else None
        if color is not None and (not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
            raise WorkspaceError("协作颜色无效")
        def operation(state):
            member = state["members"][user_id]
            if name is not None:
                member["name"] = name
            if color is not None:
                member["color"] = color.lower()
            member["last_seen"] = _now()
            return _public_member(member)
        user = self._change(operation)
        with self._presence_lock:
            if user_id in self._presence:
                self._presence[user_id].update(user)
        return user

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) > 200:
            return None
        def operation(state):
            session = state["sessions"].get(_hash(token))
            if session is None or session["expires"] <= time.time():
                return None
            return _public_member(state["members"][session["user_id"]])
        # Durable activity updates at most once a minute; live cursors stay in memory.
        with self._locked():
            self._reload()
            result = operation(self._state)
            if result:
                session = self._state["sessions"][_hash(token)]
                if time.time() - session["touched"] >= 60:
                    updated = copy.deepcopy(self._state)
                    updated["sessions"][_hash(token)]["touched"] = time.time()
                    updated["members"][result["id"]]["last_seen"] = _now()
                    self._persist(updated)
                    self._state = updated
                    result = operation(updated)
            return copy.deepcopy(result)

    def session(self, user=None):
        # Only live collaborators are public; past browser identities and private
        # risk budgets are never listed to other visitors.
        self._read(lambda state: None)
        return {"status": "ok", "authenticated": bool(user), "needs_setup": False,
                "anonymous": True, "user": user, "members": self.presence()}

    def update_presence(self, user, symbol, period=None, object_id=None, cursor=None, preview_object=None, view=None):
        if period not in (None, "day", "m30", "m3", "all"):
            raise WorkspaceError("画面周期无效")
        if object_id is not None and (not isinstance(object_id, str) or len(object_id) > 100):
            raise WorkspaceError("选中对象无效")
        if cursor is not None:
            if (not isinstance(cursor, dict) or set(cursor) != {"time", "price"}
                    or not _finite(cursor["time"]) or not 0 <= cursor["time"] <= 253402300799000
                    or not _finite(cursor["price"]) or cursor["price"] <= 0):
                raise WorkspaceError("光标位置无效")
        if preview_object is not None:
            from workspace_rules import validate_plan
            try:
                validate_plan({"objects": [preview_object]})
                self._objects_by_id([preview_object])
            except (ValueError, TypeError) as exc:
                raise WorkspaceError("拖拽预览无效") from exc
        if view is not None:
            if (not isinstance(view, dict) or set(view) != {"period", "range"}
                    or view["period"] not in ("day", "m30", "m3") or not isinstance(view["range"], dict)
                    or set(view["range"]) != {"start", "end", "min", "max"}):
                raise WorkspaceError("协作视角无效")
            area = view["range"]
            if (not all(_finite(value) for value in area.values())
                    or not 0 <= area["start"] < area["end"] <= 253402300799000
                    or not 0 < area["min"] < area["max"]):
                raise WorkspaceError("协作视角范围无效")
        with self._presence_lock:
            self._presence[user["id"]] = {**user, "symbol": symbol, "period": period,
                                         "object_id": object_id, "cursor": copy.deepcopy(cursor),
                                         "preview_object": copy.deepcopy(preview_object),
                                         "view": copy.deepcopy(view),
                                         "seen_at": time.monotonic()}
        return self.presence(symbol)

    def presence(self, symbol=None):
        now = time.monotonic()
        with self._presence_lock:
            self._presence = {key: value for key, value in self._presence.items()
                              if now - value["seen_at"] < PRESENCE_SECONDS}
            return [{key: (None if key in ("cursor", "preview_object", "view") and now - item["seen_at"] > 6
                           else copy.deepcopy(value)) for key, value in item.items() if key != "seen_at"}
                    for item in self._presence.values() if symbol is None or item["symbol"] == symbol]

    def risk(self, user_id):
        return self._read(lambda state: state["members"][user_id]["risk"])

    def save_risk(self, user_id, raw):
        clean = validate_risk(raw)
        def operation(state):
            state["members"][user_id]["risk"] = clean
            state["members"][user_id]["last_seen"] = _now()
            return clean
        return self._change(operation)

    def plan(self, symbol):
        return self._read(lambda state: state["plans"].get(symbol))

    def history(self, symbol, compact=False):
        def read(state):
            entries = state["history"].get(symbol, [])
            if not compact:
                return list(reversed(entries))
            result = []
            previous = {}
            for entry in entries:
                plan = entry["plan"]
                fields = [key for key in plan if key not in _METADATA | {"objects", "comments"}
                          and plan.get(key) != previous.get(key)]
                old = {o["id"]: o for o in previous.get("objects", [])}
                new = {o["id"]: o for o in plan.get("objects", [])}
                summary = {k: plan.get(k) for k in ("title", "direction", "enabled", "archived")}
                summary.update(notes=(plan.get("notes") or "")[:800],
                               context_note=(plan.get("context_note") or "")[:400], object_count=len(new))
                result.append({**{k: v for k, v in entry.items() if k != "plan"}, "plan": summary,
                               "changes": {"fields": fields, "added": len(new.keys() - old.keys()),
                                           "removed": len(old.keys() - new.keys()),
                                           "edited": sum(old[k] != new[k] for k in old.keys() & new.keys()),
                                           "comments": max(0, len(plan.get("comments", [])) - len(previous.get("comments", [])))}})
                previous = plan
            return list(reversed(result))
        return self._read(read)

    def boards(self, symbol):
        fields = ("board_id", "title", "direction", "revision", "updated_at", "archived")
        return self._read(lambda state: [{**{k: p.get(k) for k in fields}, "board_id": p.get("board_id", ""),
                                         "object_count": len(p.get("objects", []))}
                                        for p in state["plans"].values() if p["symbol"] == symbol])

    @staticmethod
    def _check_locks(current, clean):
        incoming = {o["id"]: o for o in clean.get("objects", [])}
        for old in (current or {}).get("objects", []):
            if old.get("locked") is True:
                new = incoming.get(old["id"])
                # Unlock is its own recorded operation. It cannot also move/delete.
                if new != old and new != {**old, "locked": False}:
                    raise WorkspaceError("标记已锁定，请先解锁再修改或删除")

    def add_comment(self, symbol, user_id, text, object_id=None):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise WorkspaceError("备注需为 1–2000 个字符")
        def operation(state):
            current = state["plans"].get(symbol)
            if not current:
                raise WorkspaceError("请先保存计划")
            if object_id is not None and object_id not in self._objects_by_id(current.get("objects", [])):
                raise WorkspaceError("对应标记已不存在，请改为计划备注")
            clean = {k: copy.deepcopy(v) for k, v in current.items() if k not in _METADATA}
            comments = clean.setdefault("comments", [])
            if len(comments) >= 300:
                raise WorkspaceError("当前计划讨论已达 300 条，请新建计划继续记录")
            comments.append({"id": secrets.token_hex(12), "text": text.strip(), "object_id": object_id,
                             "author": {"id": user_id, "name": state["members"][user_id]["name"]}, "at": _now()})
            return self._commit_plan(state, symbol, clean, user_id)
        return self._change(operation)

    @staticmethod
    def _objects_by_id(objects):
        if not isinstance(objects, list):
            raise WorkspaceError("绘图对象必须是列表")
        result = {}
        for obj in objects:
            identity = obj.get("id") if isinstance(obj, dict) else None
            if not isinstance(identity, str) or not 1 <= len(identity) <= 100 or identity in result:
                raise WorkspaceError("绘图对象需要不重复的标识")
            result[identity] = obj
        return result

    def _clean_plan(self, raw, rule_version):
        from workspace_rules import validate_plan
        try:
            clean = validate_plan(raw)
        except (ValueError, TypeError) as exc:
            raise WorkspaceError(str(exc)) from exc
        for field in _METADATA:
            clean.pop(field, None)
        self._objects_by_id(clean["objects"])
        if clean.get("enabled") and (not rule_version or clean.get("rule_version") != rule_version):
            raise WorkspaceError("启用计划前须确认当前规则版本")
        return clean

    def _commit_plan(self, state, symbol, clean, user_id):
        current = state["plans"].get(symbol)
        self._check_locks(current, clean)
        if clean.get("archived"):
            clean["enabled"] = False
        revision = current["revision"] if current else 0
        member = state["members"][user_id]
        actor = {"id": user_id, "name": member["name"]}
        now = _now()
        contract, _, board_id = symbol.partition("::")
        plan = {**clean, "id": current["id"] if current else secrets.token_hex(12), "symbol": contract,
                "revision": revision + 1, "updated_by": actor, "updated_at": now}
        if board_id:
            plan["board_id"] = board_id
        snapshots = state["activations"].setdefault(symbol, [])
        activated = bool(plan.get("enabled") and (not current or not current.get("enabled") or _decision_changed(current, plan)))
        plan["activation_at"] = now if activated else (current.get("activation_at") if current else None)
        if activated:
            snapshot = copy.deepcopy(plan)
            snapshots.append(snapshot)
            plan["activated_snapshot"] = copy.deepcopy(snapshot)
        else:
            plan["activated_snapshot"] = copy.deepcopy(current.get("activated_snapshot")) if current else None
        state["plans"][symbol] = plan
        history = state["history"].setdefault(symbol, [])
        history.append({"revision": plan["revision"], "actor": actor, "at": now,
                        "action": "activated" if activated else ("updated" if current else "created"),
                        "plan": copy.deepcopy(plan)})
        state["history"][symbol] = history[-200:]
        member["last_seen"] = now
        return plan

    def save_plan(self, symbol, base_revision, raw, user_id, rule_version):
        if not _integer(base_revision):
            raise WorkspaceError("保存计划必须提供有效版本号")
        clean = self._clean_plan(raw, rule_version)
        def operation(state):
            current = state["plans"].get(symbol)
            revision = current["revision"] if current else 0
            if base_revision != revision:
                raise RevisionConflict(current)
            clean["comments"] = copy.deepcopy((current or {}).get("comments", []))
            return self._commit_plan(state, symbol, clean, user_id)
        return self._change(operation)

    @staticmethod
    def _object_edits(raw):
        """Validate operation shape before merging into a concurrently edited object."""
        from workspace_rules import validate_chart_anchor
        if not isinstance(raw, list) or len(raw) > 500:
            raise WorkspaceError("图层修改格式无效")
        seen = set()
        def identity(value):
            return isinstance(value, str) and 1 <= len(value) <= 100
        for edit in raw:
            if not isinstance(edit, dict) or set(edit) - {"id", "changes", "unset", "node_updates", "node_inserts", "node_deletes"}:
                raise WorkspaceError("图层修改字段无效")
            if not identity(edit.get("id")) or edit["id"] in seen:
                raise WorkspaceError("图层修改标识重复或无效")
            seen.add(edit["id"])
            fields, unset = edit.get("changes", {}), edit.get("unset", [])
            if not isinstance(fields, dict) or set(fields) & {"id", "kind"}:
                raise WorkspaceError("图层标识与类型不可修改")
            if not isinstance(unset, list) or any(not isinstance(k, str) or k in {"id", "kind", "points"} for k in unset) or set(unset) & set(fields):
                raise WorkspaceError("图层移除字段无效")
            updates, inserts, deletes = (edit.get(k, []) for k in ("node_updates", "node_inserts", "node_deletes"))
            if any(not isinstance(items, list) or len(items) > 200 for items in (updates, inserts, deletes)):
                raise WorkspaceError("节点修改数量无效")
            if "points" in fields and (updates or inserts or deletes):
                raise WorkspaceError("节点不可同时整组与逐点修改")
            node_ids = set()
            for update in updates:
                if (not isinstance(update, dict) or set(update) != {"id", "changes"} or not identity(update.get("id"))
                        or not isinstance(update["changes"], dict) or set(update["changes"]) - {"time", "price", "chart_anchor"}):
                    raise WorkspaceError("节点位置修改无效")
                if "chart_anchor" in update["changes"]:
                    if "time" not in update["changes"]:
                        raise WorkspaceError("图表横坐标修改须同时提供时间与柱位锚定")
                    try:
                        validate_chart_anchor(update["changes"]["chart_anchor"])
                    except ValueError as exc:
                        raise WorkspaceError(str(exc)) from exc
                if update["id"] in node_ids:
                    raise WorkspaceError("同一节点不能重复修改")
                node_ids.add(update["id"])
            for insert in inserts:
                if (not isinstance(insert, dict) or set(insert) - {"point", "before", "after"}
                        or not isinstance(insert.get("point"), dict) or not identity(insert["point"].get("id"))
                        or set(insert["point"]) - {"id", "time", "price", "label", "chart_anchor"}
                        or any(insert.get(k) is not None and not identity(insert[k]) for k in ("before", "after"))):
                    raise WorkspaceError("新增节点无效")
                if "chart_anchor" in insert["point"]:
                    try:
                        validate_chart_anchor(insert["point"]["chart_anchor"])
                    except ValueError as exc:
                        raise WorkspaceError(str(exc)) from exc
                if insert["point"]["id"] in node_ids:
                    raise WorkspaceError("同一节点不能重复修改")
                node_ids.add(insert["point"]["id"])
            if any(not identity(i) or i in node_ids for i in deletes) or len(set(deletes)) != len(deletes):
                raise WorkspaceError("删除节点标识无效")
        return raw

    @staticmethod
    def _apply_object_edit(obj, edit):
        result = copy.deepcopy(obj)
        result.update(copy.deepcopy(edit.get("changes", {})))
        for key in edit.get("unset", []):
            result.pop(key, None)
        node_ops = any(edit.get(k) for k in ("node_updates", "node_inserts", "node_deletes"))
        if node_ops:
            if obj.get("kind") not in ("wave", "path"):
                raise WorkspaceError("该图层不支持逐点修改")
            points = result.get("points", [])
            if any(not isinstance(p.get("id"), str) for p in points):
                raise WorkspaceError("旧节点需要先更新标识")
            deleted = set(edit.get("node_deletes", []))
            points = [p for p in points if p["id"] not in deleted]
            nodes = {p["id"]: p for p in points}
            for update in edit.get("node_updates", []):
                # A stale drag must never resurrect a node another editor deleted.
                if update["id"] in nodes:
                    if "time" in update["changes"] and "chart_anchor" not in update["changes"]:
                        # Older clients send absolute time only; do not retain an unrelated source-candle anchor.
                        nodes[update["id"]].pop("chart_anchor", None)
                    nodes[update["id"]].update(copy.deepcopy(update["changes"]))
            for insert in edit.get("node_inserts", []):
                point = copy.deepcopy(insert["point"])
                if point["id"] in nodes:
                    continue
                before = next((i for i, p in enumerate(points) if p["id"] == insert.get("before")), None)
                after = next((i for i, p in enumerate(points) if p["id"] == insert.get("after")), None)
                index = before if before is not None else after + 1 if after is not None else len(points)
                points.insert(index, point)
                nodes[point["id"]] = point
            if len(points) < 2:
                raise WorkspaceError("走势线至少保留两个点，请删除整条线代替")
            result["points"] = points
        if result.get("kind") == "wave":
            for index, point in enumerate(result.get("points", [])):
                point["label"] = str(index + 1)
        return result

    def patch_plan(self, symbol, base_revision, changes, upserts, deletes, user_id, rule_version, conflict_policy="detect", object_edits=None, mutation_id=None):
        from workspace_rules import PLAN_KEYS
        if not _integer(base_revision):
            raise WorkspaceError("保存修改必须提供有效版本号")
        if conflict_policy not in ("detect", "editor_wins"):
            raise WorkspaceError("未知的编辑合并方式")
        if not isinstance(changes, dict) or set(changes) - (PLAN_KEYS - _METADATA - {"objects", "comments"}):
            raise WorkspaceError("计划修改包含未知或只读字段")
        incoming = self._objects_by_id(upserts)
        edits = self._object_edits([] if object_edits is None else object_edits)
        if mutation_id is not None and (not isinstance(mutation_id, str) or not 1 <= len(mutation_id) <= 100):
            raise WorkspaceError("修改请求标识无效")
        if not isinstance(deletes, list) or len(deletes) > 500 or any(not isinstance(i, str) or not 1 <= len(i) <= 100 for i in deletes):
            raise WorkspaceError("删除对象标识无效")
        edit_ids = {edit["id"] for edit in edits}
        if len(set(deletes)) != len(deletes) or set(deletes) & (set(incoming) | edit_ids) or set(incoming) & edit_ids:
            raise WorkspaceError("同一对象不能同时修改与删除")
        if not changes and not incoming and not deletes and not edits:
            raise WorkspaceError("修改内容为空")
        try:
            digest = _hash(json.dumps([changes, upserts, deletes, edits], sort_keys=True, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError) as exc:
            raise WorkspaceError("修改内容不是有效数据") from exc
        receipt_key = _hash(user_id + "\n" + symbol + "\n" + mutation_id) if mutation_id else None
        def operation(state):
            current = state["plans"].get(symbol)
            receipt = state["mutations"].get(receipt_key) if receipt_key else None
            if receipt:
                if receipt["digest"] != digest:
                    raise WorkspaceError("同一请求标识不能重复用于不同修改")
                return current
            revision = current["revision"] if current else 0
            if base_revision > revision and conflict_policy != "editor_wins":
                raise RevisionConflict(current, ["base_revision"])
            if conflict_policy == "editor_wins":
                # Apply only this editor's patch to the latest plan under the store lock.
                # Unrelated fields/objects survive even if the editor's base has expired.
                base = current or {}
            elif base_revision == 0:
                base = {}
            elif base_revision == revision:
                base = current
            else:
                base = next((entry["plan"] for entry in state["history"].get(symbol, [])
                             if entry["revision"] == base_revision), None)
                if base is None:
                    raise RevisionConflict(current, ["base_revision"])
            latest = current or {}
            conflicts = [key for key, value in changes.items()
                         if latest.get(key) != base.get(key) and latest.get(key) != value]
            base_objects = self._objects_by_id(base.get("objects", []))
            current_objects = self._objects_by_id(latest.get("objects", []))
            for identity, value in incoming.items():
                if current_objects.get(identity) != base_objects.get(identity) and current_objects.get(identity) != value:
                    conflicts.append("objects:" + identity)
            for identity in deletes:
                if current_objects.get(identity) != base_objects.get(identity) and identity in current_objects:
                    conflicts.append("objects:" + identity)
            if conflict_policy == "detect":
                for edit in edits:
                    identity = edit["id"]
                    if current_objects.get(identity) != base_objects.get(identity):
                        conflicts.append("objects:" + identity)
            if conflicts:
                raise RevisionConflict(current, conflicts)
            merged = {key: copy.deepcopy(value) for key, value in latest.items() if key not in _METADATA}
            merged.update(copy.deepcopy(changes))
            current_objects.update(copy.deepcopy(incoming))
            for edit in edits:
                if edit["id"] in current_objects:
                    current_objects[edit["id"]] = self._apply_object_edit(current_objects[edit["id"]], edit)
            for identity in deletes:
                current_objects.pop(identity, None)
            merged["objects"] = list(current_objects.values())
            clean = self._clean_plan(merged, rule_version)
            unchanged = {key: value for key, value in latest.items() if key not in _METADATA}
            result = current if current and clean == unchanged else self._commit_plan(state, symbol, clean, user_id)
            if receipt_key:
                state["mutations"][receipt_key] = {"symbol": symbol, "user_id": user_id, "digest": digest, "revision": result["revision"]}
                while len(state["mutations"]) > 2000:
                    del state["mutations"][next(iter(state["mutations"]))]
            return result
        return self._change(operation)
