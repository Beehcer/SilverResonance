"""Protect repaired forecasts from old tabs without gating unrelated collaboration."""
import copy
import unittest
from unittest.mock import patch

from flask import Flask

import test_workspace_api as base
import test_workspace_collaboration as collaboration
from test_workspace_api import SYMBOL
from workspace_api import register_workspace


class CoordinateGuardTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    save = base.WorkspaceApiTests.save
    send = collaboration.WorkspaceCollaborationTests.send
    base_time = 1794407400000

    def market(self, symbol):
        self.market_calls.append(symbol)
        if getattr(self, "feed_error", False):
            raise RuntimeError("fixture feed unavailable")
        if getattr(self, "feed_empty", False):
            return {"symbol": symbol, "periods": {}}
        return {"symbol": symbol, "periods": {"m30": [{"time": self.base_time}]}}

    def future_wave(self, explicit=False):
        wave = collaboration.wave()
        for i, point in enumerate(wave["points"]):
            point["time"] = self.base_time + (i + 1) * 1800000
            if explicit:
                point["chart_anchor"] = {"period": "m30", "base_time": self.base_time, "offset": i + 1}
        return wave

    def test_old_tab_future_creation_is_rejected_and_modern_coordinates_save(self):
        response = self.save({"objects": [self.future_wave()]})
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("刷新", response.json["message"])
        self.assertIsNone(self.store.plan(SYMBOL))
        self.market_calls.clear()
        response = self.save({"objects": [self.future_wave(True)]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.market_calls, [], "Modern writes never wait for market history")

    def test_node_insert_horizontal_update_and_whole_points_replacement_guard(self):
        self.save({"objects": [collaboration.wave()]})
        baseline = self.store.plan(SYMBOL)
        time = self.base_time + 1800000
        for edit in [
            {"id": "wave", "node_updates": [{"id": "a", "changes": {"time": time}}]},
            {"id": "wave", "node_inserts": [{"point": {"id": "new", "time": time, "price": 120}, "after": "a"}]},
            {"id": "wave", "changes": {"points": self.future_wave()["points"]}},
        ]:
            response = self.send([edit])
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(self.store.plan(SYMBOL), baseline)

    def test_old_full_put_cannot_strip_repaired_anchor_even_without_time_change(self):
        self.save({"objects": [self.future_wave(True)]})
        baseline = self.store.plan(SYMBOL)
        legacy = self.future_wave(); legacy["color"] = "#00ff00"
        response = self.save({"objects": [legacy]}, revision=1)
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.store.plan(SYMBOL), baseline)

    def test_legacy_price_style_and_note_edits_remain_allowed_and_do_not_reinterpret_x(self):
        # Stored legacy data can predate this guard; leave it intact until audited repair.
        legacy = self.future_wave()
        self.store.save_plan(SYMBOL, 0, {"objects": [legacy]}, self.owner_session["user"]["id"], "rule-v1")
        before = copy.deepcopy(self.store.plan(SYMBOL)["objects"][0]["points"])
        self.market_calls.clear()
        response = self.send([{"id": "wave", "changes": {"color": "#00ff00"}, "node_updates": [{"id": "a", "changes": {"price": 130}}]}], changes={"notes": "keep editing"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.market_calls, [])
        self.assertEqual([p["time"] for p in response.json["plan"]["objects"][0]["points"]], [p["time"] for p in before])

    def test_explicit_history_coordinate_is_allowed_without_a_feed_lookup(self):
        self.save({"objects": [self.future_wave(True)]})
        self.market_calls.clear()
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": self.base_time - 1000, "chart_anchor": None}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.market_calls, [])
        self.assertIsNone(response.json["plan"]["objects"][0]["points"][0]["chart_anchor"])

    def test_unknown_feed_rejects_new_and_moved_bare_coordinates_but_keeps_style_edits(self):
        for missing in ("feed_empty", "feed_error"):
            setattr(self, missing, True)
            response = self.save({"objects": [self.future_wave()]})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertIsNone(self.store.plan(SYMBOL))
            setattr(self, missing, False)
        legacy = self.future_wave()
        self.store.save_plan(SYMBOL, 0, {"objects": [legacy]}, self.owner_session["user"]["id"], "rule-v1")
        self.feed_error = True
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": 1}}]}])
        self.assertEqual(response.status_code, 400, response.json)
        self.market_calls.clear()
        response = self.send([{"id": "wave", "changes": {"color": "#00ff00"},
                               "node_updates": [{"id": "a", "changes": {"price": 130}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.market_calls, [])

    def test_idless_legacy_path_allows_unchanged_time_sequence_and_retains_anchor_protection(self):
        legacy = self.future_wave(); legacy["kind"] = "path"
        for point in legacy["points"]:
            point.pop("id"); point.pop("label")
        self.store.save_plan(SYMBOL, 0, {"objects": [legacy]}, self.owner_session["user"]["id"], "rule-v1")
        updated = copy.deepcopy(legacy); updated["color"] = "#00ff00"; updated["points"][0]["price"] = 130
        self.feed_error = True
        response = self.save({"objects": [updated]}, revision=1)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.market_calls, [], "Unchanged legacy X coordinates need no market read")
        baseline = self.store.plan(SYMBOL)
        updated["points"][0]["time"] += 1000
        self.assertEqual(self.save({"objects": [updated]}, revision=2).status_code, 400)
        self.assertEqual(self.store.plan(SYMBOL), baseline)
        anchored = copy.deepcopy(baseline["objects"][0])
        anchored["points"][0]["chart_anchor"] = {"period": "m30", "base_time": self.base_time, "offset": 1}
        self.store.save_plan(SYMBOL, 2, {"objects": [anchored]}, self.owner_session["user"]["id"], "rule-v1")
        stripped = copy.deepcopy(anchored); stripped["points"][0].pop("chart_anchor"); stripped["color"] = "#123456"
        self.assertEqual(self.save({"objects": [stripped]}, revision=3).status_code, 400)
        self.assertIn("chart_anchor", self.store.plan(SYMBOL)["objects"][0]["points"][0])

    def test_other_drawing_kinds_never_fetch_market_for_geometry_changes(self):
        for index, kind in enumerate(("trend", "reference", "zone")):
            obj = {"id": kind, "kind": kind, "period": "m30", "points": [
                {"time": self.base_time + 1800000, "price": 100},
                {"time": self.base_time + 3600000, "price": 110}]}
            if kind == "zone":
                obj.update(zone_type="danger", direction="both", effect="warn")
            response = self.save({"objects": [obj]}, revision=index * 2)
            self.assertEqual(response.status_code, 200, response.json)
            moved = copy.deepcopy(obj["points"]); moved[1]["time"] += 1800000
            response = self.send([{"id": kind, "changes": {"points": moved}}], revision=index * 2 + 1)
            self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.market_calls, [])

    def test_modern_mixed_historical_and_future_nodes_do_not_fetch_market(self):
        wave = self.future_wave(True)
        wave["points"][0].update(time=self.base_time - 1800000, chart_anchor=None)
        self.feed_error = True
        self.assertEqual(self.save({"objects": [wave]}).status_code, 200)
        self.assertEqual(self.market_calls, [])

    def test_preview_provider_supplies_cutoff_without_reading_complete_history(self):
        calls = []
        def full(symbol):
            self.fail("Full history must not be copied just to validate coordinates")
        def preview(symbol):
            calls.append(symbol)
            return {"symbol": symbol, "partial": True, "periods": {"m30": [{"time": self.base_time}]}}
        app = Flask("coordinate-preview"); app.testing = True
        register_workspace(app, self.store, full, lambda: [{"symbol": SYMBOL}], lambda: self.rules,
                           self.evaluate, market_preview_provider=preview)
        client = app.test_client(); session = client.get("/api/workspace/session").json
        response = self.save({"objects": [self.future_wave()]}, client=client,
                             headers={"X-Workspace-CSRF": session["csrf_token"]})
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(calls, [SYMBOL])

    def test_guard_checks_latest_locked_state_after_optimization_read(self):
        legacy = self.future_wave()
        self.store.save_plan(SYMBOL, 0, {"objects": [legacy]}, self.owner_session["user"]["id"], "rule-v1")
        original_read = self.store.plan
        def racing_read(symbol):
            result = original_read(symbol)
            anchor = {"period": "m30", "base_time": self.base_time, "offset": 2}
            self.store.patch_plan(SYMBOL, 1, {}, [], [], self.owner_session["user"]["id"], "rule-v1",
                                  object_edits=[{"id": "wave", "node_updates": [{"id": "a", "changes": {
                                      "time": legacy["points"][0]["time"], "chart_anchor": anchor}}]}])
            return result
        with patch.object(self.store, "plan", racing_read):
            response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {
                "time": legacy["points"][0]["time"]}}]}])
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(original_read(SYMBOL)["revision"], 2)
        self.assertEqual(original_read(SYMBOL)["objects"][0]["points"][0]["chart_anchor"]["offset"], 2)

    def test_malformed_coordinate_ids_return_validation_error_not_server_error(self):
        wave = self.future_wave(); wave["id"] = ["invalid"]
        response = self.save({"objects": [wave]})
        self.assertEqual(response.status_code, 400, response.json)

    def test_full_legacy_path_cannot_strip_both_ids_and_anchors_even_at_historical_raw_times(self):
        for index, historical in enumerate((False, True)):
            path = self.future_wave(True); path["kind"] = "path"
            if historical:
                for offset, point in enumerate(path["points"], 1):
                    point["time"] = self.base_time - offset * 1800000
            self.assertEqual(self.save({"objects": [path]}, revision=index).status_code, 200)
            baseline = self.store.plan(SYMBOL)
            stripped = copy.deepcopy(path)
            for point in stripped["points"]:
                point.pop("id"); point.pop("chart_anchor")
            response = self.save({"objects": [stripped]}, revision=index + 1)
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(self.store.plan(SYMBOL), baseline)


if __name__ == "__main__":
    unittest.main()
