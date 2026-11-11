"""Opacity is optional presentation state: durable, collaborative, never a gate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import math
import threading
import unittest
from unittest.mock import patch

import test_workspace_api as base
from test_workspace_api import SYMBOL
from workspace_rules import evaluate_plan, validate_plan
from workspace_store import WorkspaceStore


KINDS = ("horizontal", "segment", "trend", "reference", "wave", "path", "zone", "text")
INVALID = (True, False, "0.5", None, math.nan, math.inf, -math.inf, -0.01, 1.01)


def drawing(kind, opacity=None, include_opacity=True):
    points = [{"time": 1794200000000, "price": 100}, {"time": 1794200060000, "price": 110}]
    if kind in ("horizontal", "text"):
        points = points[:1]
    if kind == "segment":
        points[1]["price"] = points[0]["price"]
    if kind in ("wave", "path"):
        points = [{**point, "id": "point-" + str(index), "label": str(index + 1)}
                  for index, point in enumerate(points)]
    obj = {"id": kind, "kind": kind, "name": kind, "color": "#32d583", "period": "m30", "points": points}
    if kind == "zone":
        obj.update(zone_type="danger", direction="both", effect="block")
    if include_opacity:
        obj["opacity"] = opacity
    return obj


class OpacityValidationTests(unittest.TestCase):
    def test_missing_values_remain_missing_for_legacy_documents(self):
        old = {"objects": [drawing(kind, include_opacity=False) for kind in KINDS]}
        validated = validate_plan(old)
        self.assertNotIn("plan_opacity", validated)
        self.assertTrue(all("opacity" not in item for item in validated["objects"]))
        self.assertTrue(all(item.get("opacity", 1) == 1 for item in validated["objects"]))

    def test_zero_and_fractional_values_are_preserved_for_every_kind(self):
        for opacity in (0, 0.25, 0.6, 1):
            with self.subTest(opacity=opacity):
                value = validate_plan({"plan_opacity": opacity, "objects": [drawing(kind, opacity) for kind in KINDS]})
                self.assertEqual(value["plan_opacity"], opacity)
                self.assertEqual([obj["opacity"] for obj in value["objects"]], [opacity] * len(KINDS))

    def test_invalid_values_are_rejected_in_plan_and_all_drawings(self):
        for value in INVALID:
            with self.subTest(value=value, field="plan"):
                with self.assertRaises(ValueError):
                    validate_plan({"plan_opacity": value})
            for kind in KINDS:
                with self.subTest(value=value, kind=kind):
                    with self.assertRaises(ValueError):
                        validate_plan({"objects": [drawing(kind, value)]})

    def test_invisible_zone_still_blocks_entry_and_plan_opacity_does_not_change_review(self):
        from test_workspace_rules import VERSION, fixture
        plan, market, risk = fixture()
        baseline = evaluate_plan(plan, market, risk, VERSION)
        plan["plan_opacity"] = 0
        self.assertEqual(evaluate_plan(plan, market, risk, VERSION), baseline)
        now_ms = datetime.fromisoformat(market["as_of"]).timestamp() * 1000
        zone = drawing("zone", 0)
        zone["points"] = [{"time": now_ms - 60000, "price": 100}, {"time": now_ms + 60000, "price": 110}]
        plan["objects"] = [zone]
        result = evaluate_plan(plan, market, risk, VERSION)
        self.assertEqual(next(item for item in result["checks"] if item["key"] == "zone:zone")["state"], "fail")
        self.assertNotEqual(result["status"], "ready")


class OpacityApiTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    market = base.WorkspaceApiTests.market
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    join = base.WorkspaceApiTests.join
    save = base.WorkspaceApiTests.save

    def send(self, *, edits=None, changes=None, revision=1, client=None, headers=None):
        return (client or self.owner).post("/api/workspace/plans/" + SYMBOL + "/patch",
            json={"base_revision": revision, "conflict_policy": "editor_wins", "object_edits": edits or [],
                  "changes": changes or {}}, headers=headers or self.owner_headers)

    def test_save_patch_reload_all_eight_kinds_and_plan_opacity(self):
        first = self.save({"plan_opacity": 0, "objects": [drawing(kind, 0) for kind in KINDS]})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json["plan"]["plan_opacity"], 0)
        self.assertTrue(all(obj["opacity"] == 0 for obj in first.json["plan"]["objects"]))
        changed = self.send(changes={"plan_opacity": 0.4},
                            edits=[{"id": kind, "changes": {"opacity": (index + 1) / 8}} for index, kind in enumerate(KINDS)])
        self.assertEqual(changed.status_code, 200, changed.json)
        reloaded = WorkspaceStore(self.path).plan(SYMBOL)
        self.assertEqual(reloaded, changed.json["plan"])
        self.assertEqual(reloaded["plan_opacity"], 0.4)
        self.assertEqual([obj["opacity"] for obj in reloaded["objects"]], [(index + 1) / 8 for index in range(8)])
        hidden = self.send(changes={"plan_opacity": 0}, edits=[{"id": "text", "changes": {"opacity": 0}}])
        self.assertEqual(hidden.json["plan"]["plan_opacity"], 0)
        self.assertEqual(hidden.json["plan"]["objects"][-1]["opacity"], 0)

    def test_presence_preview_roundtrip_preserves_zero_for_all_kinds(self):
        peer, _ = self.join("Peer")
        for kind in KINDS:
            with self.subTest(kind=kind):
                response = self.owner.post("/api/workspace/presence", json={"symbol": SYMBOL, "period": "m30",
                    "preview_object": drawing(kind, 0)}, headers=self.owner_headers)
                self.assertEqual(response.status_code, 200, response.json)
                seen = peer.get("/api/workspace/presence?symbol=" + SYMBOL).json["presence"]
                ours = next(member for member in seen if member["id"] == self.owner_session["user"]["id"])
                self.assertEqual(ours["preview_object"]["opacity"], 0)
        self.assertEqual(WorkspaceStore(self.path).presence(SYMBOL), [])

    def test_invalid_save_patch_and_presence_do_not_change_saved_revision(self):
        saved = self.save({"plan_opacity": 1, "objects": [drawing(kind, 1) for kind in KINDS]}).json["plan"]
        for invalid in INVALID:
            with self.subTest(value=invalid):
                self.assertEqual(self.save({"plan_opacity": invalid}, revision=1).status_code, 400)
                self.assertEqual(self.send(changes={"plan_opacity": invalid}).status_code, 400)
                self.assertEqual(self.send(edits=[{"id": "wave", "changes": {"opacity": invalid}}]).status_code, 400)
                response = self.owner.post("/api/workspace/presence", json={"symbol": SYMBOL, "period": "m30",
                    "preview_object": drawing("wave", invalid)}, headers=self.owner_headers)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.plan(SYMBOL), saved)

    def test_stale_different_property_edit_preserves_opacity_and_geometry(self):
        self.save({"objects": [drawing("wave", 1)], "plan_opacity": 1})
        peer, headers = self.join("Peer")
        first = self.send(changes={"plan_opacity": 0}, edits=[{"id": "wave", "changes": {"opacity": 0}}])
        self.assertEqual(first.status_code, 200, first.json)
        second = self.send(changes={"notes": "Peer note"}, edits=[{"id": "wave", "changes": {"color": "#abcdef"},
            "node_updates": [{"id": "point-0", "changes": {"price": 99}}]}], client=peer, headers=headers)
        self.assertEqual(second.status_code, 200, second.json)
        plan = second.json["plan"]
        self.assertEqual(plan["plan_opacity"], 0)
        self.assertEqual(plan["objects"][0]["opacity"], 0)
        self.assertEqual(plan["objects"][0]["color"], "#abcdef")
        self.assertEqual(plan["objects"][0]["points"][0]["price"], 99)

    def test_concurrent_store_instances_merge_opacity_and_another_property(self):
        self.save({"objects": [drawing("wave", 1)]})
        peer = WorkspaceStore(self.path)
        barrier = threading.Barrier(2)
        def update(store, changes):
            barrier.wait(timeout=5)
            return store.patch_plan(SYMBOL, 1, {}, [], [], self.owner_session["user"]["id"], "rule-v1",
                "editor_wins", [{"id": "wave", "changes": changes}])
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(update, self.store, {"opacity": 0}), pool.submit(update, peer, {"name": "Renamed"})]
            for job in jobs:
                job.result(timeout=10)
        obj = WorkspaceStore(self.path).plan(SYMBOL)["objects"][0]
        self.assertEqual((obj["opacity"], obj["name"]), (0, "Renamed"))

    def test_style_only_patch_does_not_reactivate_blocking_zone_plan(self):
        with patch("workspace_store._now", return_value="2026-11-11T10:00:00+00:00"):
            first = self.save({"enabled": True, "rule_version": "rule-v1", "context_confirmed": True,
                "context_note": "Original context", "plan_opacity": 1, "objects": [drawing("zone", 1)]}).json["plan"]
        with patch("workspace_store._now", return_value="2026-11-11T10:01:00+00:00"):
            changed = self.send(changes={"plan_opacity": 0}, edits=[{"id": "zone", "changes": {"opacity": 0}}])
        self.assertEqual(changed.status_code, 200, changed.json)
        plan = changed.json["plan"]
        self.assertEqual(plan["activation_at"], first["activation_at"])
        self.assertEqual(plan["activated_snapshot"], first["activated_snapshot"])
        self.assertTrue(plan["context_confirmed"])
        self.assertEqual(plan["context_note"], first["context_note"])
        self.assertEqual(len(self.store._state["activations"][SYMBOL]), 1)

    def test_zone_geometry_and_blocking_effect_still_reactivate_plan(self):
        with patch("workspace_store._now", return_value="2026-11-11T10:00:00+00:00"):
            self.save({"enabled": True, "rule_version": "rule-v1", "context_confirmed": True,
                       "objects": [drawing("zone", 0)]})
        moved = drawing("zone", 0)["points"]
        moved[1]["price"] = 111
        with patch("workspace_store._now", return_value="2026-11-11T10:01:00+00:00"):
            changed = self.send(edits=[{"id": "zone", "changes": {"points": moved}}])
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(changed.json["plan"]["activation_at"], "2026-11-11T10:01:00+00:00")
        with patch("workspace_store._now", return_value="2026-11-11T10:02:00+00:00"):
            changed = self.send(edits=[{"id": "zone", "changes": {"effect": "observe"}}])
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(changed.json["plan"]["activation_at"], "2026-11-11T10:02:00+00:00")
        self.assertEqual(len(self.store._state["activations"][SYMBOL]), 3)

    def test_legacy_plan_remains_readable_without_opacity_migration(self):
        saved = self.save({"objects": [drawing(kind, include_opacity=False) for kind in KINDS]}).json["plan"]
        reloaded = WorkspaceStore(self.path).plan(SYMBOL)
        self.assertEqual(saved, reloaded)
        self.assertNotIn("plan_opacity", reloaded)
        self.assertTrue(all("opacity" not in obj for obj in reloaded["objects"]))


if __name__ == "__main__":
    unittest.main()
