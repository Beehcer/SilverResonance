"""Request-scoped mobile viewing never changes a shared user's desktop rights."""
import json
import unittest
from unittest.mock import patch

import test_workspace_api as base
from test_workspace_api import SYMBOL, drawing


DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"


class WorkspaceViewOnlyTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    market = base.WorkspaceApiTests.market
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    save = base.WorkspaceApiTests.save

    def viewer(self, device=ANDROID, explicit=False):
        client = self.app.test_client()
        headers = {"User-Agent": device}
        if explicit:
            headers["X-Workspace-View-Only"] = "1"
        response = client.get('/api/workspace/session', headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["authenticated"])
        self.assertTrue(response.json["view_only"])
        return client, {**headers, "X-Workspace-CSRF": response.json["csrf_token"]}, response.json

    def test_mobile_user_agents_receive_view_only_sessions_without_extra_setup(self):
        agents = [ANDROID, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
                  "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)", "iPod touch Mobile",
                  "Mozilla/5.0 (Windows Phone 10.0; IEMobile/11.0)", "BlackBerry 9900", "BB10 Touch",
                  "Opera/9.80 (J2ME/MIDP; Opera Mini/9)", "Mozilla/5.0 (Tablet; rv:100)",
                  "Mozilla/5.0 (Kindle Fire) Silk/108.5", "webOS/3.0"]
        for agent in agents:
            with self.subTest(agent=agent):
                client, headers, session = self.viewer(agent)
                self.assertTrue(session["mobile_device"])
                self.assertFalse(session["needs_setup"])
                self.assertEqual(client.get('/api/workspace/session', headers=headers).json["user"]["id"], session["user"]["id"])

    def test_explicit_header_is_request_local_and_separate_from_device_detection(self):
        headers = {**self.owner_headers, "User-Agent": DESKTOP, "X-Workspace-View-Only": "1"}
        readonly = self.owner.get('/api/workspace/session', headers=headers).json
        self.assertTrue(readonly["view_only"])
        self.assertFalse(readonly["mobile_device"])
        denied = self.owner.post('/api/workspace/profile', json={"name": "Blocked"}, headers=headers)
        self.assertEqual(denied.status_code, 403)
        self.assertIn("移动端仅供查看", denied.json["message"])
        desktop = self.owner.get('/api/workspace/session', headers={"User-Agent": DESKTOP}).json
        self.assertFalse(desktop["view_only"])
        self.assertFalse(desktop["mobile_device"])
        self.assertEqual(desktop["user"]["id"], readonly["user"]["id"])
        allowed = self.owner.post('/api/workspace/profile', json={"name": "Desktop works"}, headers={**self.owner_headers, "User-Agent": DESKTOP})
        self.assertEqual(allowed.status_code, 200, allowed.json)
        self.assertEqual(allowed.json["user"]["name"], "Desktop works")
        persisted = self.path.read_text(encoding="utf-8")
        self.assertNotIn('"view_only"', persisted)
        self.assertNotIn('"mobile_device"', persisted)

    def test_mobile_ua_cannot_opt_out_with_a_zero_header_and_desktop_ua_stays_editable(self):
        mobile = self.owner.get('/api/workspace/session', headers={"User-Agent": ANDROID, "X-Workspace-View-Only": "0"}).json
        self.assertTrue(mobile["view_only"])
        self.assertTrue(mobile["mobile_device"])
        for agent in (DESKTOP, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15", "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"):
            with self.subTest(agent=agent):
                session = self.owner.get('/api/workspace/session', headers={"User-Agent": agent, "X-Workspace-View-Only": "0"}).json
                self.assertFalse(session["view_only"])
                self.assertFalse(session["mobile_device"])

    def test_all_persistent_workspace_endpoints_reject_ua_and_header_viewers_atomically(self):
        saved = self.save({"title": "Keep", "notes": "untouched", "objects": [drawing("keep", 100)]})
        self.assertEqual(saved.status_code, 200)
        cases = [
            ("POST", "/profile", {"name": "Blocked"}),
            ("PUT", "/risk", {"risk": {"budget": 5000}}),
            ("PUT", "/plans/" + SYMBOL, {"base_revision": 1, "plan": {"objects": [], "notes": "Blocked"}}),
            ("POST", "/boards?symbol=" + SYMBOL, {"title": "Blocked"}),
            ("POST", "/boards?symbol=" + SYMBOL, {"title": "Blocked copy", "copy_from": ""}),
            ("POST", "/comments?symbol=" + SYMBOL, {"text": "Blocked"}),
            ("POST", "/plans/" + SYMBOL + "/patch", {"base_revision": 1, "changes": {"notes": "Blocked", "archived": True}, "delete_object_ids": ["keep"]}),
            ("POST", "/plans/" + SYMBOL + "/patch", {"base_revision": 1, "object_edits": [{"id": "keep", "changes": {"color": "#ff0000"}}]}),
        ]
        for device, explicit in ((ANDROID, False), (DESKTOP, True)):
            client, headers, _ = self.viewer(device, explicit)
            before = self.path.read_bytes()
            with patch.object(self.store, "_change", side_effect=AssertionError("View-only request attempted a durable edit")):
                for method, route, payload in cases:
                    with self.subTest(device=device, route=route, method=method):
                        response = client.open('/api/workspace' + route, method=method, json=payload, headers=headers)
                        self.assertEqual(response.status_code, 403, response.json)
                        self.assertIn("移动端仅供查看", response.json["message"])
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.plan(SYMBOL), saved.json["plan"])

    def test_read_endpoints_and_market_subscription_remain_available(self):
        self.save({"objects": [drawing("keep", 100)], "notes": "Read me"})
        client, headers, _ = self.viewer()
        routes = ["/session", "/symbols", "/quotes", "/rules", "/risk", "/plans/" + SYMBOL,
                  "/market?symbol=" + SYMBOL, "/market?symbol=" + SYMBOL + "&preview=240",
                  "/market?symbol=" + SYMBOL + "&history=existing-token", "/review?symbol=" + SYMBOL,
                  "/history?symbol=" + SYMBOL, "/boards?symbol=" + SYMBOL, "/presence?symbol=" + SYMBOL]
        for route in routes:
            with self.subTest(route=route):
                response = client.get('/api/workspace' + route, headers=headers)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertGreaterEqual(self.market_calls.count(SYMBOL), 3)
        self.assertEqual(client.get('/api/workspace/plans/' + SYMBOL, headers=headers).json["plan"]["notes"], "Read me")

    def test_viewer_presence_is_online_but_cannot_publish_editing_or_preview_objects(self):
        self.save({"objects": [drawing("keep", 100)]})
        client, headers, session = self.viewer()
        cursor = {"time": 1700000000000, "price": 100}
        view = {"period": "m30", "range": {"start": 1700000000000, "end": 1700001800000, "min": 90, "max": 110}}
        payload = {"symbol": SYMBOL, "period": "m30", "cursor": cursor, "view": view,
                   "object_id": {"not": "a valid target"}, "preview_object": "invalid preview", "editing": True}
        before = self.path.read_bytes()
        with patch.object(self.store, "_change", side_effect=AssertionError("Presence wrote a plan")):
            response = client.post('/api/workspace/presence', json=payload, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        peer_presence = self.owner.get('/api/workspace/presence?symbol=' + SYMBOL).json["presence"]
        viewer = next(item for item in peer_presence if item["id"] == session["user"]["id"])
        self.assertIsNone(viewer["object_id"])
        self.assertIsNone(viewer["preview_object"])
        self.assertNotIn("editing", viewer)
        self.assertEqual(viewer["cursor"], cursor)
        self.assertEqual(viewer["view"], view)
        self.assertEqual(viewer["period"], "m30")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn(session["user"]["id"], [member["id"] for member in self.owner.get('/api/workspace/session').json["members"]])

    def test_readonly_heartbeat_still_requires_csrf_same_origin_and_valid_view_coordinates(self):
        client, headers, _ = self.viewer()
        value = {"symbol": SYMBOL, "period": "m30"}
        response = client.post('/api/workspace/presence', json=value, headers={"User-Agent": ANDROID})
        self.assertEqual(response.status_code, 403)
        response = client.post('/api/workspace/presence', json=value, headers={**headers, "Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        response = client.post('/api/workspace/presence', json={**value, "cursor": {"time": 1, "price": -1}}, headers=headers)
        self.assertEqual(response.status_code, 400)
        response = client.post('/api/workspace/presence', json={**value, "unknown": True}, headers=headers)
        self.assertEqual(response.status_code, 400)

    def test_desktop_presence_keeps_editor_preview_and_is_not_globally_downgraded(self):
        headers = {**self.owner_headers, "User-Agent": DESKTOP}
        payload = {"symbol": SYMBOL, "period": "m30", "object_id": "editing-layer", "preview_object": drawing("editing-layer", 100)}
        self.owner.get('/api/workspace/session', headers={"User-Agent": ANDROID})
        response = self.owner.post('/api/workspace/presence', json=payload, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        own = next(item for item in response.json["presence"] if item["id"] == self.owner_session["user"]["id"])
        self.assertEqual(own["object_id"], "editing-layer")
        self.assertEqual(own["preview_object"], payload["preview_object"])

    def test_mobile_viewer_stream_receives_desktop_revisions_and_preserves_board_scope(self):
        board = "readonly-test"
        self.store.save_plan(SYMBOL + "::" + board, 0, {"objects": [], "notes": "before"}, self.owner_session["user"]["id"], "rule-v1")
        client, headers, _ = self.viewer()
        response = client.get('/api/workspace/events?symbol=' + SYMBOL + '&board=' + board, headers=headers, buffered=False)
        self.assertEqual(response.status_code, 200)
        chunks = iter(response.response)
        try:
            self.assertIn(b"retry:", next(chunks))
            def event(chunk):
                line = next(line for line in chunk.decode().splitlines() if line.startswith('data: '))
                return json.loads(line[6:])
            first = event(next(chunks))
            self.assertEqual((first["symbol"], first["board"], first["revision"]), (SYMBOL, board, 1))
            self.store.patch_plan(SYMBOL + "::" + board, 1, {"notes": "desktop changed"}, [], [], self.owner_session["user"]["id"], "rule-v1")
            updated = event(next(chunks))
            self.assertEqual((updated["symbol"], updated["board"], updated["revision"]), (SYMBOL, board, 2))
        finally:
            response.close()
        actual = client.get('/api/workspace/plans/' + SYMBOL + '?board=' + board, headers=headers).json["plan"]
        self.assertEqual(actual["notes"], "desktop changed")


if __name__ == '__main__':
    unittest.main()
