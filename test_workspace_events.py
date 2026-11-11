"""Local SSE wakes on commits/cursors and still reconciles external writers."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask

from workspace_api import COOKIE_NAME, register_workspace
from workspace_store import WorkspaceStore, WorkspaceUnavailable, board_key


SYMBOL = "SHFE.rb2701"


class WorkspaceEventsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "workspace.json"
        self.store = WorkspaceStore(self.path)
        self.app = Flask(__name__)
        self.app.testing = True
        register_workspace(self.app, self.store, lambda symbol: {}, lambda: [SYMBOL],
                           lambda: {"version": "rules"}, lambda *args: {})
        self.client = self.app.test_client()
        session = self.client.get("/api/workspace/session").json
        self.user = session["user"]
        self.token = self.client.get_cookie(COOKIE_NAME, path="/api/workspace").value
        self.store.save_plan(SYMBOL, 0, {"title": "Original", "objects": []}, self.user["id"], "rules")

    def stream(self, board=""):
        response = self.client.get("/api/workspace/events?symbol=" + SYMBOL + "&board=" + board, buffered=False)
        self.addCleanup(response.close)
        chunks = iter(response.response)
        self.assertIn(b"retry:", next(chunks))
        initial = self.event(next(chunks))
        return chunks, initial

    @staticmethod
    def event(chunk):
        text = chunk.decode()
        assert "event: workspace" in text, text
        return json.loads(text.split("data: ", 1)[1])

    def patch_plan(self, key=SYMBOL):
        plan = self.store.plan(key)
        self.store.patch_plan(key, plan["revision"] if plan else 0, {"notes": "Updated"}, [], [],
                              self.user["id"], "rules")

    def change_after_wait(self, operation):
        waiting = threading.Event()
        done = threading.Event()
        errors = []
        real_wait = self.store.wait_for_events
        def wait(*args):
            waiting.set()
            return real_wait(*args)
        def worker():
            try:
                if not waiting.wait(2):
                    raise AssertionError("Stream did not wait")
                operation()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        return patch.object(self.store, "wait_for_events", side_effect=wait), done, errors

    def test_commit_wakes_stream_before_one_second_poll_and_without_full_plan_read(self):
        chunks, initial = self.stream()
        self.assertEqual((initial["symbol"], initial["board"], initial["revision"]), (SYMBOL, "", 1))
        # Avoid the writer's normal plan read in the spy below.
        operation = lambda: self.store.patch_plan(SYMBOL, 1, {"notes": "Peer edit"}, [], [], self.user["id"], "rules")
        waiter, done, errors = self.change_after_wait(operation)
        started = time.monotonic()
        with waiter, patch.object(self.store, "plan", side_effect=AssertionError("SSE must not copy full plans")), \
                patch.object(self.store, "authenticate", side_effect=AssertionError("SSE must not touch durable activity")):
            event = self.event(next(chunks))
        self.assertEqual(event["revision"], 2)
        self.assertLess(time.monotonic() - started, .6)
        self.assertTrue(done.wait(1))
        self.assertEqual(errors, [])

    def test_presence_wakes_immediately_without_rechecking_durable_state(self):
        chunks, _ = self.stream()
        operation = lambda: self.store.update_presence(self.user, SYMBOL, "m3", cursor={"time": 1794300000000, "price": 3020})
        waiter, _, errors = self.change_after_wait(operation)
        with waiter, patch.object(self.store, "event_state", wraps=self.store.event_state) as read:
            event = self.event(next(chunks))
        self.assertEqual(event["presence"][0]["cursor"]["price"], 3020)
        read.assert_not_called()
        self.assertEqual(errors, [])

    def test_change_between_snapshot_and_wait_cannot_be_lost(self):
        marker = self.store.event_marker(SYMBOL)
        self.patch_plan()
        started = time.monotonic()
        self.store.wait_for_events(SYMBOL, marker, 2)
        self.assertLess(time.monotonic() - started, .1)

    def test_failed_commit_does_not_publish_notification(self):
        marker = self.store.event_marker(SYMBOL)
        with patch("workspace_store.os.replace", side_effect=OSError("unavailable")):
            with self.assertRaises(WorkspaceUnavailable):
                self.patch_plan()
        self.assertEqual(self.store.event_marker(SYMBOL), marker)
        self.assertEqual(self.store.event_state(self.token, SYMBOL), 1)

    def test_event_state_is_single_lock_no_plan_copy_and_no_minute_write(self):
        before = self.path.read_bytes()
        session_time = next(iter(self.store._state["sessions"].values()))["touched"]
        with patch("workspace_store.time.time", return_value=session_time+65), \
                patch.object(self.store, "_disk_signature", wraps=self.store._disk_signature) as metadata, \
                patch.object(self.store, "_persist", side_effect=AssertionError("Event read must not write")), \
                patch("workspace_store.copy.deepcopy", side_effect=AssertionError("No full-plan copy")):
            self.assertEqual(self.store.event_state(self.token, SYMBOL), 1)
        self.assertEqual(metadata.call_count, 1)
        self.assertEqual(before, self.path.read_bytes())
        with patch("workspace_store.time.time", return_value=session_time+65), \
                patch.object(self.store, "_persist", wraps=self.store._persist) as persist:
            self.assertIsNotNone(self.store.authenticate(self.token))
        self.assertEqual(persist.call_count, 1)

    def test_other_process_commit_is_detected_by_fallback_without_notification(self):
        chunks, _ = self.stream()
        marker = self.store.event_marker(SYMBOL)
        code = ("from workspace_store import WorkspaceStore; import sys; "
                "s=WorkspaceStore(sys.argv[1]); p=s.plan(sys.argv[2]); "
                "s.patch_plan(sys.argv[2],p['revision'],{'notes':'external'},[],[],p['updated_by']['id'],'rules')")
        result = subprocess.run([sys.executable, "-c", code, str(self.path), SYMBOL],
                                cwd=Path(__file__).parent, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.store.event_marker(SYMBOL), marker)
        started = time.monotonic()
        event = self.event(next(chunks))
        self.assertEqual(event["revision"], 2)
        self.assertLess(time.monotonic()-started, 1.5)

    def test_revoked_session_stops_existing_stream_without_leaking_state(self):
        chunks, _ = self.stream()
        self.store._change(lambda state: state["sessions"].clear())
        with self.assertRaises(StopIteration):
            next(chunks)

    def test_external_corruption_fails_closed_at_fallback(self):
        chunks, _ = self.stream()
        self.path.write_text("broken", encoding="utf-8")
        self.assertIn(b"event: unavailable", next(chunks))
        with self.assertRaises(StopIteration):
            next(chunks)

    def test_board_scope_and_leaving_presence_are_isolated(self):
        scope = board_key(SYMBOL, "night")
        self.store.update_presence(self.user, scope, "m30")
        chunks, initial = self.stream("night")
        self.assertEqual((initial["symbol"], initial["board"], initial["revision"]), (SYMBOL, "night", 0))
        default_marker = self.store.event_marker(SYMBOL)
        self.store.update_presence(self.user, scope, "m3")
        self.assertEqual(self.store.event_marker(SYMBOL), default_marker)
        self.assertEqual(self.event(next(chunks))["presence"][0]["period"], "m3")
        self.store.update_presence(self.user, SYMBOL, "m30")
        self.assertEqual(self.event(next(chunks))["presence"], [])

    def test_heartbeat_and_bounded_stream_lifetime_survive_idle(self):
        with patch("workspace_api.EVENT_HEARTBEAT_SECONDS", .04), \
                patch("workspace_api.EVENT_RECHECK_SECONDS", .2), \
                patch("workspace_api.EVENT_STREAM_SECONDS", .12), \
                patch.object(self.store, "event_state", wraps=self.store.event_state) as read:
            chunks, _ = self.stream()
            self.assertIn(b": keep-alive", next(chunks))
            self.assertIn(b": keep-alive", next(chunks))
            with self.assertRaises(StopIteration):
                next(chunks)
        self.assertEqual(read.call_count, 1)


if __name__ == "__main__":
    unittest.main()
