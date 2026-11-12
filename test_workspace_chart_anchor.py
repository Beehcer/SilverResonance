"""Source-candle anchors survive collaboration without rewriting legacy timestamps."""
import copy
import unittest

import test_workspace_api as base
import test_workspace_collaboration as collaboration
from test_workspace_api import SYMBOL
from workspace_rules import validate_plan
from workspace_store import WorkspaceStore


def anchor(period="m30", offset=2.5):
    return {"period": period, "base_time": 1794380400000, "offset": offset}


class WorkspaceChartAnchorTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    market = base.WorkspaceApiTests.market
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    join = base.WorkspaceApiTests.join
    save = base.WorkspaceApiTests.save
    send = collaboration.WorkspaceCollaborationTests.send

    def test_legacy_points_are_unchanged_and_fractional_anchors_round_trip(self):
        wave = collaboration.wave()
        original = copy.deepcopy(wave["points"])
        self.assertEqual(validate_plan({"objects": [wave]})["objects"][0]["points"], original)
        for index, period in enumerate(("day", "m30", "m3")):
            wave["points"][index]["chart_anchor"] = anchor(period, index + .375)
        wave["points"][3]["chart_anchor"] = None
        response = self.save({"objects": [wave]})
        self.assertEqual(response.status_code, 200, response.json)
        peer, _ = self.join("Peer")
        remote = peer.get("/api/workspace/plans/" + SYMBOL).json["plan"]["objects"][0]
        self.assertEqual(remote["points"], wave["points"])
        self.assertEqual([p["time"] for p in remote["points"]], [p["time"] for p in original])
        restarted = WorkspaceStore(self.path)
        self.assertEqual(restarted.plan(SYMBOL)["objects"][0]["points"], wave["points"])

    def test_node_anchor_updates_preserve_peer_price_and_can_be_explicitly_cleared(self):
        wave = collaboration.wave()
        wave["points"][0]["chart_anchor"] = anchor()
        self.assertEqual(self.save({"objects": [wave]}).status_code, 200)
        peer, headers = self.join("Peer")
        revised = anchor("m3", 4.125)
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": 1794381120000, "chart_anchor": revised}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"price": 125}}]}], client=peer, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        point = response.json["plan"]["objects"][0]["points"][0]
        self.assertEqual(point["chart_anchor"], revised)
        self.assertEqual((point["time"], point["price"]), (1794381120000, 125))
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": point["time"], "chart_anchor": None}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(response.json["plan"]["objects"][0]["points"][0]["chart_anchor"])
        self.assertEqual(response.json["plan"]["objects"][0]["points"][0]["price"], 125)

    def test_insert_anchor_survives_retry_restart_and_delete_wins(self):
        self.save({"objects": [collaboration.wave()]})
        point = {"id": "new", "time": 1794381300000, "price": 111, "chart_anchor": anchor("m30", 3.75)}
        edits = [{"id": "wave", "node_inserts": [{"point": point, "after": "a", "before": "b"}]}]
        for _ in range(2):
            response = self.send(edits, mutation="anchor-insert")
            self.assertEqual(response.status_code, 200, response.json)
        stored = WorkspaceStore(self.path).plan(SYMBOL)["objects"][0]["points"]
        self.assertEqual([p["id"] for p in stored], ["a", "new", "b", "c", "d"])
        self.assertEqual(stored[1], {**point, "label": "2"})
        self.send([{"id": "wave", "node_deletes": ["new"]}])
        stale = self.send([{"id": "wave", "node_updates": [{"id": "new", "changes": {"time": point["time"], "chart_anchor": anchor("day", 5)}}]}])
        self.assertEqual(stale.status_code, 200, stale.json)
        self.assertNotIn("new", [p["id"] for p in stale.json["plan"]["objects"][0]["points"]])

    def test_presence_preview_preserves_source_anchor(self):
        wave = collaboration.wave()
        wave["points"][0]["chart_anchor"] = anchor()
        response = self.owner.post("/api/workspace/presence", json={"symbol": SYMBOL, "period": "m30", "preview_object": wave}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["presence"][0]["preview_object"]["points"][0]["chart_anchor"], anchor())

    def test_invalid_anchor_shapes_and_numbers_are_rejected_without_mutation(self):
        self.save({"objects": [collaboration.wave()]})
        invalid = [False, "anchor", [], {}, {"period": "m30"}, {**anchor(), "extra": 1},
                   {**anchor(), "period": "all"}, {**anchor(), "period": []}]
        invalid += [{**anchor(), "base_time": value} for value in (-1, .5, True, "123", float("nan"), float("inf"), 253402300799001)]
        invalid += [{**anchor(), "offset": value} for value in (-.01, 10000.01, True, "2", float("nan"), float("inf"))]
        baseline = self.store.plan(SYMBOL)
        for value in invalid:
            with self.subTest(anchor=value):
                wave = collaboration.wave()
                wave["points"][0]["chart_anchor"] = value
                with self.assertRaises(ValueError):
                    validate_plan({"objects": [wave]})
                response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": wave["points"][0]["time"], "chart_anchor": value}}]}])
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.store.plan(SYMBOL), baseline)
        for offset in (0, .0001, 10000):
            wave = collaboration.wave()
            wave["points"][0]["chart_anchor"] = anchor(offset=offset)
            self.assertEqual(validate_plan({"objects": [wave]})["objects"][0]["points"][0]["chart_anchor"]["offset"], offset)

    def test_anchor_only_and_legacy_time_only_edits_cannot_erase_a_fixed_coordinate(self):
        wave = collaboration.wave()
        wave["points"][0]["chart_anchor"] = anchor()
        self.assertEqual(self.save({"objects": [wave]}).status_code, 200)
        baseline = self.store.plan(SYMBOL)
        for value in (anchor(offset=4), None):
            response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"chart_anchor": value}}]}])
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(self.store.plan(SYMBOL), baseline)
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": 1794389999000}}]}])
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("刷新", response.json["message"])
        self.assertEqual(self.store.plan(SYMBOL), baseline)
        response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": {"time": 1794389999000, "chart_anchor": None}}]}])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(response.json["plan"]["objects"][0]["points"][0]["chart_anchor"])

    def test_same_revision_horizontal_edits_remain_complete_pairs_and_price_merges(self):
        wave = collaboration.wave()
        wave["points"][0]["chart_anchor"] = anchor()
        self.assertEqual(self.save({"objects": [wave]}).status_code, 200)
        peer, headers = self.join("Peer")
        first = {"time": 1794382200000, "chart_anchor": anchor(offset=3.25)}
        second = {"time": 1794385800000, "chart_anchor": anchor(offset=6.75)}
        for changes, client, request_headers in ((first, self.owner, self.owner_headers),
                                                 ({"price": 126}, peer, headers),
                                                 (second, peer, headers)):
            response = self.send([{"id": "wave", "node_updates": [{"id": "a", "changes": changes}]}], client=client, headers=request_headers)
            self.assertEqual(response.status_code, 200, response.json)
        point = response.json["plan"]["objects"][0]["points"][0]
        self.assertEqual({key: point[key] for key in ("time", "chart_anchor")}, second)
        self.assertEqual(point["price"], 126)


if __name__ == "__main__":
    unittest.main()
