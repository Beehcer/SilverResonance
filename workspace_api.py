"""Anonymous browser collaboration routes, isolated from legacy app state."""
import functools
import hmac
import json
import math
import re
import secrets
import time
from datetime import datetime
from urllib.parse import urlsplit

from flask import Blueprint, Response, g, jsonify, request, stream_with_context
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from workspace_store import (AuthenticationError, PermissionError, RevisionConflict,
                             SESSION_SECONDS, WorkspaceError, WorkspaceUnavailable, csrf_token, board_key)


COOKIE_NAME = "workspace_session"
MAX_REQUEST_BYTES = 128 * 1024
EVENT_RECHECK_SECONDS = 1
EVENT_HEARTBEAT_SECONDS = 5
EVENT_STREAM_SECONDS = 120
MOBILE_USER_AGENT = re.compile(r"Android|iPhone|iPad|iPod|Windows Phone|IEMobile|BlackBerry|BB10|Opera Mini|Mobi|Tablet|Kindle|Silk/|webOS", re.IGNORECASE)
VIEW_ONLY_MESSAGE = "移动端仅供查看，请在桌面端编辑计划、图层和个人设置。"


def register_workspace(app, store, market_provider, symbols_provider, rule_provider, evaluator, quotes_provider=None,
                       market_incremental_provider=None, market_preview_provider=None):
    """Providers: market(symbol), optional incremental(symbol, token), symbols(),
    rules(), evaluate(plan, market, risk, version). Reviews always read full data.
    """
    bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")

    def fail(message, code):
        return jsonify(status="error", message=message), code

    def candle_client_current():
        return (request.headers.get("X-Workspace-Candle-Protocol") == "2"
                or request.args.get("candle_protocol") == "2")

    @bp.before_request
    def protect_candle_transition():
        # An already-open older tab cannot interpret the new coordinate basis.
        # Keep its last known picture; require reload before returning a changed
        # timeline or accepting edits. Never reinterpret old data in that tab.
        if candle_client_current():
            return None
        symbol = (request.view_args or {}).get("symbol") or request.args.get("symbol")
        if symbol is None and request.is_json and (request.content_length or 0) <= MAX_REQUEST_BYTES:
            value = request.get_json(silent=True)
            symbol = value.get("symbol") if isinstance(value, dict) else None
        g.candle_symbol = symbol
        if isinstance(symbol, str) and store.has_candle_reference(symbol):
            return fail("K线与画线定位已升级，请刷新页面继续查看和编辑；已有计划已保留。", 426)

    @bp.after_request
    def protect_transition_during_request(response):
        # The feed may publish its very first transition while a legacy GET is
        # already running. Recheck immediately before returning any new data.
        symbol = getattr(g, "candle_symbol", None)
        if (response.status_code == 200 and not candle_client_current()
                and isinstance(symbol, str) and store.has_candle_reference(symbol)):
            response = jsonify(status="error", message="K线与画线定位已升级，请刷新页面继续查看和编辑；已有计划已保留。")
            response.status_code = 426
        return response

    def body():
        if not request.is_json:
            raise WorkspaceError("请求需要使用 JSON 格式")
        value = request.get_json()
        if not isinstance(value, dict):
            raise WorkspaceError("请求内容必须是对象")
        return value

    def allowed_fields(value, fields):
        if set(value) - set(fields):
            raise WorkspaceError("请求包含未知字段")

    def coordinate_cutoffs(symbol, value):
        # Only old coordinate payloads need a cached-market lookup; modern drawing
        # events and price/colour/note edits keep the fast, market-independent path.
        objects = value.get("upsert_objects", [])
        if isinstance(value.get("plan"), dict):
            objects = value["plan"].get("objects", [])
        objects = objects if isinstance(objects, list) else []
        bare = lambda p: isinstance(p, dict) and "time" in p and "chart_anchor" not in p
        identity = lambda item: item.get("id") if isinstance(item.get("id"), str) else None
        candidates = [o for o in objects if isinstance(o, dict) and o.get("kind") in ("wave", "path") and
                      isinstance(o.get("points"), list) and any(bare(p) for p in o["points"])]
        edits = []
        for edit in value.get("object_edits", []) if isinstance(value.get("object_edits", []), list) else []:
            if not isinstance(edit, dict):
                continue
            changes = edit.get("changes", {})
            candidate = False
            if isinstance(changes, dict) and isinstance(changes.get("points"), list):
                candidate |= any(bare(p) for p in changes["points"])
            for update in edit.get("node_updates", []) if isinstance(edit.get("node_updates", []), list) else []:
                candidate |= isinstance(update, dict) and bare(update.get("changes"))
            for insert in edit.get("node_inserts", []) if isinstance(edit.get("node_inserts", []), list) else []:
                candidate |= isinstance(insert, dict) and bare(insert.get("point"))
            if candidate:
                edits.append(edit)
        if not candidates and not edits:
            return {}
        # The store performs the final check again under its write lock. This
        # read is only an optimization: unrelated drawing kinds never need K-lines.
        previous = store.plan(selected_key(symbol)) or {}
        old_objects = {obj["id"]: obj for obj in previous.get("objects", [])}

        def changed_bare_points(points, prior):
            valid = [point for point in points if isinstance(point, dict)]
            if (len(valid) == len(points) == len(prior) and all(not p.get("id") for p in valid + prior)
                    and [p.get("time") for p in valid] == [p.get("time") for p in prior]):
                return False
            old_points = {identity(point): point for point in prior if identity(point)}
            return any(bare(point) and (not identity(point) or identity(point) not in old_points
                                       or old_points[identity(point)].get("time") != point["time"]) for point in valid)

        needed = any(changed_bare_points(obj["points"], old_objects.get(identity(obj), {}).get("points", []))
                     for obj in candidates)
        for edit in edits:
            obj = old_objects.get(identity(edit))
            if obj is None or obj.get("kind") not in ("wave", "path"):
                continue
            prior = obj.get("points", [])
            nodes = {identity(point): point for point in prior if identity(point)}
            changes = edit.get("changes", {})
            if isinstance(changes, dict) and isinstance(changes.get("points"), list):
                needed |= changed_bare_points(changes["points"], prior)
            for update in edit.get("node_updates", []) if isinstance(edit.get("node_updates", []), list) else []:
                if isinstance(update, dict) and bare(update.get("changes")) and identity(update) in nodes:
                    needed |= nodes[identity(update)].get("time") != update["changes"]["time"]
            for insert in edit.get("node_inserts", []) if isinstance(edit.get("node_inserts", []), list) else []:
                if isinstance(insert, dict) and bare(insert.get("point")):
                    needed |= identity(insert["point"]) not in nodes
        if not needed:
            return {}
        try:
            # Existing preview providers accept one symbol and cap history at
            # 240 rows per period. Do not copy 16,000 rows merely to read last K.
            provider = market_preview_provider or market_provider
            market = provider(symbol)
            if (market.get("symbol") != symbol or market.get("incremental")
                    or market.get("partial") and market_preview_provider is None):
                return {}
            result = {}
            for period in ("day", "m30", "m3"):
                bars = market.get("periods", {}).get(period, [])
                if not bars:
                    continue
                raw = bars[-1].get("dt", bars[-1].get("time"))
                if isinstance(raw, str):
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        continue
                    cutoff = parsed.timestamp() * 1000
                else:
                    cutoff = raw
                if isinstance(cutoff, (int, float)) and not isinstance(cutoff, bool) and math.isfinite(cutoff):
                    result[period] = cutoff
            return result
        except Exception:
            # A feed outage must not prevent unrelated stored-plan editing.
            # Anchor-loss protection in the store still applies without a feed.
            return {}

    def same_origin():
        fetch_site = request.headers.get("Sec-Fetch-Site")
        if fetch_site in ("cross-site", "same-site"):
            raise PermissionError("请从工作区页面提交修改")
        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")
        source = origin or referer
        if source:
            try:
                parsed, expected = urlsplit(source), urlsplit(request.host_url)
                source_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                expected_port = expected.port or (443 if expected.scheme == "https" else 80)
                if (parsed.scheme, parsed.hostname, source_port) != (expected.scheme, expected.hostname, expected_port):
                    raise ValueError("origin mismatch")
                if parsed.username or parsed.password:
                    raise ValueError("invalid origin")
            except ValueError as exc:
                raise PermissionError("仅允许同源页面提交修改") from exc
        # JSON-only requests and authenticated CSRF headers block browser form CSRF
        # even for clients that omit Origin/Referer entirely.

    @bp.before_request
    def prepare():
        if request.content_length and request.content_length > MAX_REQUEST_BYTES:
            raise RequestEntityTooLarge()
        request.max_content_length = MAX_REQUEST_BYTES
        # Presentation permissions belong to this request, not the anonymous
        # identity: opening a phone must never revoke that user's desktop edits.
        g.workspace_mobile_device = bool(MOBILE_USER_AGENT.search(request.headers.get("User-Agent", "")))
        g.workspace_view_only = g.workspace_mobile_device or request.headers.get("X-Workspace-View-Only", "").strip() == "1"
        g.workspace_token = request.cookies.get(COOKIE_NAME, "")
        g.workspace_user = store.authenticate(g.workspace_token)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            same_origin()
            # All current persistent writes (profile, risk, plans, boards and
            # comments) are blocked. Only the transient viewer heartbeat is safe.
            viewer_presence = request.method == "POST" and request.endpoint == "workspace.presence"
            if g.workspace_view_only and not viewer_presence:
                raise PermissionError(VIEW_ONLY_MESSAGE)
            if g.workspace_user:
                supplied = request.headers.get("X-Workspace-CSRF", "")
                if len(supplied) > 128 or not hmac.compare_digest(supplied.encode("utf-8"), csrf_token(g.workspace_token).encode("ascii")):
                    raise PermissionError("页面验证已失效，请刷新后重试")

    @bp.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Vary"] = "Cookie, User-Agent, X-Workspace-View-Only"
        return response

    @bp.errorhandler(WorkspaceError)
    def workspace_error(error):
        if isinstance(error, RevisionConflict):
            return jsonify(status="conflict", message=str(error), plan=error.plan, conflicts=error.conflicts), 409
        return fail(str(error), error.status_code)

    @bp.errorhandler(BadRequest)
    def bad_request(error):
        return fail("请求 JSON 无效", 400)

    @bp.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return fail("请求过大，请缩减计划内容", 413)

    def authenticated(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not g.workspace_user:
                raise AuthenticationError("浏览器会话已过期，请刷新页面恢复协作")
            return view(*args, **kwargs)
        return wrapped

    def session_response(token=None):
        token = token if token is not None else g.workspace_token
        user = store.authenticate(token)
        value = store.session(user)
        value["csrf_token"] = csrf_token(token) if user else None
        value["view_only"] = g.workspace_view_only
        value["mobile_device"] = g.workspace_mobile_device
        response = jsonify(value)
        if user and token != g.workspace_token:
            response.set_cookie(COOKIE_NAME, token, httponly=True, secure=request.is_secure,
                                samesite="Strict", max_age=SESSION_SECONDS, path="/api/workspace")
        return response

    def symbol_requested(symbol=None):
        symbol = symbol if symbol is not None else request.args.get("symbol", "")
        entries = symbols_provider()
        if isinstance(entries, dict):
            available = set(entries)
        else:
            available = {item.get("symbol") if isinstance(item, dict) else item for item in entries}
        if not isinstance(symbol, str) or not symbol or len(symbol) > 100 or symbol not in available:
            raise WorkspaceError("请选择有效品种")
        return symbol

    def current_rules():
        rules = rule_provider()
        if not isinstance(rules, dict):
            raise WorkspaceUnavailable("交易规则暂不可用")
        return rules

    def selected_key(symbol):
        return board_key(symbol, request.args.get("board", ""))

    def public_rules(rules):
        # Filesystem paths are host implementation details, not a member-facing field.
        return {key: value for key, value in rules.items() if key != "path"}

    def evaluate(plan, symbol):
        market = market_provider(symbol)
        rules = current_rules()
        version = rules.get("version") if rules.get("available", True) and rules.get("compatible", True) else ""
        return evaluator(plan or {}, market, store.risk(g.workspace_user["id"]), version or "")

    @bp.get("/session")
    def session():
        same_origin()
        token = store.anonymous_session(g.workspace_token)
        user = store.authenticate(token)
        if not any(item["id"] == user["id"] for item in store.presence()):
            store.update_presence(user, None)
        return session_response(token)

    @bp.post("/profile")
    @authenticated
    def profile():
        value = body()
        allowed_fields(value, ("name", "color"))
        if not value:
            raise WorkspaceError("请选择昵称或颜色")
        store.profile(g.workspace_user["id"], value.get("name"), value.get("color"))
        return session_response()

    @bp.get("/symbols")
    @authenticated
    def symbols():
        return jsonify(status="ok", symbols=symbols_provider())

    @bp.get("/quotes")
    @authenticated
    def quotes():
        return jsonify(status="ok", quotes=quotes_provider() if quotes_provider else [])

    @bp.get("/rules")
    @authenticated
    def rules():
        return jsonify(status="ok", rules=public_rules(current_rules()))

    @bp.route("/risk", methods=["GET", "PUT"])
    @authenticated
    def risk():
        if request.method == "GET":
            return jsonify(status="ok", risk=store.risk(g.workspace_user["id"]))
        value = body()
        allowed_fields(value, ("risk",))
        return jsonify(status="ok", risk=store.save_risk(g.workspace_user["id"], value.get("risk")))

    @bp.route("/plans/<path:symbol>", methods=["GET", "PUT"])
    @authenticated
    def plans(symbol):
        symbol = symbol_requested(symbol)
        if request.method == "GET":
            return jsonify(status="ok", plan=store.plan(selected_key(symbol)))
        value = body()
        allowed_fields(value, ("base_revision", "plan"))
        rules = current_rules()
        version = rules.get("version") if rules.get("available", True) and rules.get("compatible", True) else None
        plan = store.save_plan(selected_key(symbol), value.get("base_revision"), value.get("plan"), g.workspace_user["id"], version, coordinate_cutoffs(symbol, value))
        # Saving and reviewing are separate actions. A market failure cannot turn a
        # committed revision into an apparent failed save that the client retries.
        return jsonify(status="ok", plan=plan)

    @bp.get("/market")
    @authenticated
    def market():
        symbol = symbol_requested()
        history = request.args.get("history", "")
        if request.args.get("preview") == "240" and market_preview_provider is not None:
            result = market_preview_provider(symbol)
        elif history and len(history) <= 128 and market_incremental_provider is not None:
            result = market_incremental_provider(symbol, history)
        else:
            result = market_provider(symbol)
        return jsonify(status="ok", market=result)

    @bp.get("/review")
    @authenticated
    def review():
        symbol = symbol_requested()
        plan = store.plan(selected_key(symbol))
        return jsonify(status="ok", evaluation=evaluate(plan, symbol), plan_revision=plan["revision"] if plan else 0)

    @bp.get("/history")
    @authenticated
    def history():
        return jsonify(status="ok", history=store.history(selected_key(symbol_requested()), compact=request.args.get("compact") == "1"))

    @bp.route("/boards", methods=["GET", "POST"])
    @authenticated
    def boards():
        symbol = symbol_requested()
        if request.method == "GET":
            return jsonify(status="ok", boards=store.boards(symbol))
        value = body()
        allowed_fields(value, ("title", "copy_from", "copy_revision", "direction"))
        title = value.get("title", "新计划")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
            raise WorkspaceError("计划名称需为 1–80 个字符")
        source = store.plan(board_key(symbol, value["copy_from"])) if "copy_from" in value else None
        if "copy_revision" in value:
            revision = value["copy_revision"]
            if "copy_from" not in value or not isinstance(revision, int) or isinstance(revision, bool):
                raise WorkspaceError("请选择有效历史版本")
            source = next((h["plan"] for h in store.history(board_key(symbol, value["copy_from"]))
                           if h["revision"] == revision), None)
        if "copy_from" in value and source is None:
            raise WorkspaceError("原计划已不存在")
        raw = dict(source or {})
        raw.update(title=title.strip(), direction=value.get("direction", raw.get("direction", "long")),
                   enabled=False, context_confirmed=False, archived=False, comments=[])
        identity = secrets.token_hex(8)
        rules = current_rules()
        plan = store.save_plan(board_key(symbol, identity), 0, raw, g.workspace_user["id"], rules.get("version"))
        return jsonify(status="ok", plan=plan), 201

    @bp.post("/comments")
    @authenticated
    def comment():
        value = body()
        allowed_fields(value, ("text", "object_id"))
        plan = store.add_comment(selected_key(symbol_requested()), g.workspace_user["id"],
                                 value.get("text"), value.get("object_id"))
        return jsonify(status="ok", plan=plan)

    @bp.post("/plans/<path:symbol>/patch")
    @authenticated
    def patch_plan(symbol):
        symbol = symbol_requested(symbol)
        value = body()
        allowed_fields(value, ("base_revision", "changes", "upsert_objects", "delete_object_ids", "conflict_policy", "object_edits", "mutation_id"))
        rules = current_rules()
        version = rules.get("version") if rules.get("available", True) and rules.get("compatible", True) else None
        plan = store.patch_plan(selected_key(symbol), value.get("base_revision"), value.get("changes", {}),
                                value.get("upsert_objects", []), value.get("delete_object_ids", []),
                                g.workspace_user["id"], version, value.get("conflict_policy", "detect"),
                                value.get("object_edits", []), value.get("mutation_id"), coordinate_cutoffs(symbol, value))
        return jsonify(status="ok", plan=plan)

    @bp.route("/presence", methods=["GET", "POST"])
    @authenticated
    def presence():
        if request.method == "GET":
            return jsonify(status="ok", presence=store.presence(selected_key(symbol_requested())))
        value = body()
        if g.workspace_view_only:
            # A viewer can share a cursor/viewport and appear online, but cannot
            # claim an editing target or send a ghost object to collaborators.
            value.pop("editing", None)
            value["object_id"] = None
            value["preview_object"] = None
        allowed_fields(value, ("symbol", "period", "object_id", "cursor", "preview_object", "view"))
        symbol = symbol_requested(value.get("symbol"))
        result = store.update_presence(g.workspace_user, selected_key(symbol), value.get("period"), value.get("object_id"),
                                       value.get("cursor"), value.get("preview_object"), value.get("view"))
        return jsonify(status="ok", presence=result)

    @bp.get("/events")
    @authenticated
    def events():
        symbol = symbol_requested()
        board = request.args.get("board", "")
        scope = board_key(symbol, board)
        token = g.workspace_token
        @stream_with_context
        def generate():
            yield "retry: 500\n\n"
            previous = None
            revision, next_plan_check, next_ping = 0, 0, 0
            checked_marker = None
            deadline = time.monotonic() + EVENT_STREAM_SECONDS
            while time.monotonic() < deadline:
                now = time.monotonic()
                # Capture before reading: a concurrent commit/cursor update must
                # either be included below or make wait_for_events return now.
                marker = store.event_marker(scope)
                try:
                    if not candle_client_current() and store.has_candle_reference(symbol):
                        yield 'event: unavailable\ndata: {"status":"reload_required"}\n\n'
                        return
                    if now >= next_plan_check or checked_marker != marker[0]:
                        revision = store.event_state(token, scope)
                        if revision is None:
                            return
                        checked_marker = marker[0]
                        next_plan_check = now + EVENT_RECHECK_SECONDS
                    payload = json.dumps({"symbol": symbol, "board": board, "revision": revision,
                                          "presence": store.presence(scope)},
                                         ensure_ascii=False, separators=(",", ":"))
                except WorkspaceError:
                    yield 'event: unavailable\ndata: {"status":"unavailable"}\n\n'
                    return
                if payload != previous:
                    yield "event: workspace\ndata: " + payload + "\n\n"
                    previous = payload
                    next_ping = now + EVENT_HEARTBEAT_SECONDS
                elif now >= next_ping:
                    yield ": keep-alive\n\n"
                    next_ping = now + EVENT_HEARTBEAT_SECONDS
                store.wait_for_events(scope, marker,
                                      min(next_plan_check, next_ping, deadline) - time.monotonic())
        response = Response(generate(), mimetype="text/event-stream")
        response.headers["X-Accel-Buffering"] = "no"
        return response

    app.register_blueprint(bp)
    return bp
