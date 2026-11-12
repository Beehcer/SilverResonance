"""Clock-basis transitions survive collaboration, restart and normal backups."""
import copy
import json
import tarfile
import unittest
from unittest.mock import patch

import test_workspace_api as api_base
import test_workspace_collaboration as collaboration
from workspace_backup import WorkspaceBackup, verify_archive
from workspace_market import WorkspaceMarketCache, timestamp
from workspace_rules import validate_chart_anchor
from workspace_store import WorkspaceStore


BASE = 1794380400000
REFERENCE = {"basis": "native_v1", "target_basis": "trading_minutes_v1",
             "source_times": [BASE - 1800000, BASE], "target_base_time": BASE + 900000}
SYMBOL = api_base.SYMBOL


class TransitionStoreTests(unittest.TestCase):
    setUp = api_base.WorkspaceApiTests.setUp
    market = api_base.WorkspaceApiTests.market
    evaluate = staticmethod(api_base.WorkspaceApiTests.evaluate)
    join = api_base.WorkspaceApiTests.join
    save = api_base.WorkspaceApiTests.save
    send = collaboration.WorkspaceCollaborationTests.send

    def prepare(self):
        wave = collaboration.wave()
        wave["points"][0]["chart_anchor"] = {"period": "m30", "base_time": BASE, "offset": 3}
        self.assertEqual(self.save({"objects": [wave]}).status_code, 200)
        baseline = self.store.plan(SYMBOL)
        self.store.ensure_candle_reference(SYMBOL, REFERENCE)
        self.owner_headers["X-Workspace-Candle-Protocol"] = "2"
        return baseline

    def test_reference_is_one_time_and_restart_does_not_move_user_points(self):
        baseline = self.prepare()
        candidate = {**REFERENCE, "target_base_time": BASE + 9000000}
        self.assertEqual(self.store.ensure_candle_reference(SYMBOL, candidate), REFERENCE)
        restarted = WorkspaceStore(self.path)
        self.assertEqual(restarted.candle_reference(SYMBOL), REFERENCE)
        self.assertEqual(restarted.plan(SYMBOL), baseline)
        self.assertEqual(restarted.ensure_candle_reference(SYMBOL, candidate), REFERENCE)
        self.assertEqual(self.store.plan(SYMBOL), baseline)

    def test_existing_backup_includes_reference_and_original_plans(self):
        baseline = self.prepare()
        worker = WorkspaceBackup(self.path, directory=self.path.parent / "backups")
        self.assertTrue(worker.tick())
        archive = next(worker.directory.glob("workspace-*.tar.gz"))
        verify_archive(archive)
        with tarfile.open(archive) as contents:
            state = json.load(contents.extractfile("workspace.json"))
        self.assertEqual(state["candle_references"][SYMBOL], REFERENCE)
        self.assertEqual(state["plans"][SYMBOL], baseline)

    def test_legacy_tab_cannot_receive_new_timeline_or_change_any_plan(self):
        baseline = self.prepare()
        old = {key: value for key, value in self.owner_headers.items() if key != "X-Workspace-Candle-Protocol"}
        for path in ("/market?symbol=" + SYMBOL, "/plans/" + SYMBOL, "/review?symbol=" + SYMBOL,
                     "/events?symbol=" + SYMBOL):
            response = self.owner.get("/api/workspace" + path, headers=old)
            self.assertEqual(response.status_code, 426, response.json)
            self.assertIn("刷新", response.json["message"])
        response = self.send([{"id": "wave", "changes": {"color": "#00ff00"}}], headers=old)
        self.assertEqual(response.status_code, 426)
        self.assertEqual(self.store.plan(SYMBOL), baseline)
        response = self.owner.get("/api/workspace/market?symbol=" + SYMBOL, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)

    def test_cutover_occurring_inside_legacy_market_request_is_also_blocked(self):
        original = self.app.view_functions["workspace.market"]
        def publish_inside_request():
            self.store.ensure_candle_reference(SYMBOL, REFERENCE)
            return original()
        with patch.dict(self.app.view_functions, {"workspace.market": publish_inside_request}):
            response = self.owner.get("/api/workspace/market?symbol=" + SYMBOL)
        self.assertEqual(response.status_code, 426)
        self.assertNotIn("market", response.json)

    def test_price_edit_preserves_old_anchor_but_old_horizontal_edit_rejected(self):
        self.prepare()
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"price": 125}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        baseline = self.store.plan(SYMBOL)
        old_anchor = baseline["objects"][0]["points"][0]["chart_anchor"]
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {
            "time": BASE + 9000000, "chart_anchor": {**old_anchor, "offset": 4}}}]}])
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.store.plan(SYMBOL), baseline)
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {
            "time": BASE + 9000000, "chart_anchor": {**old_anchor, "base_time": BASE + 900000,
                                                      "basis": "trading_minutes_v1", "offset": 4}}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        restarted = WorkspaceStore(self.path)
        self.assertEqual(restarted.plan(SYMBOL)["objects"][0]["points"][0]["chart_anchor"]["basis"],
                         "trading_minutes_v1")

    def test_mobile_protocol_does_not_grant_edit_permission(self):
        baseline = self.prepare()
        headers = {**self.owner_headers, "User-Agent": "Mozilla/5.0 iPhone Mobile"}
        response = self.send([{"id": "wave", "changes": {"color": "#00ff00"}}], headers=headers)
        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(self.store.plan(SYMBOL), baseline)

    def test_basis_schema_rejects_unknown_values(self):
        for basis in (True, [], {}, 2, "whatever"):
            with self.subTest(basis=basis), self.assertRaises(ValueError):
                validate_chart_anchor({"period": "m30", "base_time": BASE, "offset": 2, "basis": basis})


class TransitionMarketTests(unittest.TestCase):
    def setUp(self):
        self.now = timestamp("2026-11-12T11:30:00+08:00")
        self.cache = WorkspaceMarketCache(lambda: self.now)
        self.symbol = "CZCE.TA701"
        self.native = {"dt": "2026-11-12T11:00:00+08:00", "open": 5984, "high": 6010,
                       "low": 5984, "close": 5994}
        self.trade = {"dt": "2026-11-12T11:15:00+08:00", "open": 5996, "high": 6010,
                      "low": 5990, "close": 5994, "end_time": "2026-11-12T13:45:00+08:00",
                      "source_last_start": "2026-11-12T11:15:00+08:00", "closed": False}

    def publish(self):
        return self.cache.publish_trading_m30(self.symbol, [self.trade], {})

    def test_first_reference_requires_native_success_and_same_current_window(self):
        self.assertFalse(self.publish())
        self.cache.cache(self.symbol, 1800, [self.native])
        self.cache.mark_failed(self.symbol, 1800)
        self.assertFalse(self.publish())
        self.cache.cache(self.symbol, 1800, [self.native])
        self.now += 21
        self.assertFalse(self.publish())
        self.cache.cache(self.symbol, 1800, [{**self.native, "dt": "2026-11-12T10:30:00+08:00"}])
        self.assertFalse(self.publish())
        self.assertEqual(self.cache.references, {})
        self.cache.cache(self.symbol, 1800, [self.native])
        self.assertTrue(self.publish())

    def test_restart_loads_original_reference_without_using_current_native_tail(self):
        fixed = copy.deepcopy(REFERENCE)
        self.cache.reference_loader = lambda symbol: fixed
        self.assertTrue(self.publish())
        self.assertEqual(self.cache.references[self.symbol], fixed)
        self.now += 3600
        self.assertTrue(self.publish())
        self.assertEqual(self.cache.references[self.symbol], fixed)

    def test_failed_reference_persistence_cannot_publish_new_basis(self):
        self.cache.cache(self.symbol, 1800, [self.native])
        def fail(symbol, reference):
            raise OSError("disk unavailable")
        self.cache.reference_provider = fail
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual(self.cache.references, {})
        self.assertNotIn((self.symbol, "trade_m30"), self.cache.data)

    def test_existing_transition_cannot_silently_return_native_on_new_session(self):
        self.cache.cache(self.symbol, 1800, [self.native])
        self.cache.reference_loader = lambda symbol: REFERENCE
        result = self.cache.snapshot(self.symbol, {}, {"connected": True})
        self.assertEqual(result["periods"]["m30"], [])
        self.assertEqual(result["candle_basis"]["m30"], "trading_minutes_v1")
        self.assertEqual(result["anchor_reference"]["m30"], REFERENCE)
        self.assertFalse(result["fresh"])


if __name__ == "__main__":
    unittest.main()
