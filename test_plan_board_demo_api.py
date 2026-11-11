import copy
import queue
import tempfile
import unittest
from pathlib import Path

import app as web_app
from plan_board_demo import SharedPlanStore, default_document


def drawing(object_id="line-a", price=100):
    return {"id": object_id, "kind": "horizontal", "points": [{"time": 15000, "price": price}],
            "name": "水平线", "color": "#e7c460", "width": 2, "line_style": "solid",
            "extend": False, "visible": True, "note": "", "z_index": 0}


def change(base, objects=(), deletes=()):
    base_objects = {item["id"]: item for item in base["objects"]
                    if item["id"] in {obj["id"] for obj in objects} | set(deletes)}
    return {"base_revision": base["revision"], "objects": list(objects),
            "delete_ids": list(deletes), "base_objects": base_objects}


class SharedPlanStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SharedPlanStore()

    def test_default_and_persisted_document(self):
        self.assertEqual(self.store.get("DEMO.ag"), default_document("DEMO.ag"))
        base = self.store.get("DEMO.ag")
        saved, conflicts = self.store.apply("DEMO.ag", change(base, [drawing()]), "client-a")
        self.assertEqual(conflicts, [])
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.store.get("DEMO.ag")["objects"][0]["points"][0]["time"], 15000)
        event = self.store.subscribe("DEMO.ag")
        self.store.apply("DEMO.ag", change(saved, [drawing("line-b")]), "client-b")
        message = event.get_nowait()
        self.assertEqual(message["client_id"], "client-b")
        self.assertEqual(len(message["plan"]["objects"]), 2)

    def test_stale_changes_to_different_objects_merge(self):
        base = self.store.get("DEMO.ag")
        first, _ = self.store.apply("DEMO.ag", change(base, [drawing("a")]))
        second, conflicts = self.store.apply("DEMO.ag", change(base, [drawing("b")]))
        self.assertEqual(conflicts, [])
        self.assertEqual(second["revision"], 2)
        self.assertEqual({item["id"] for item in second["objects"]}, {"a", "b"})
        self.assertEqual(first["revision"], 1)

    def test_same_object_conflict_preserves_server_version(self):
        base = self.store.get("DEMO.ag")
        first, _ = self.store.apply("DEMO.ag", change(base, [drawing("a")]))
        changed = drawing("a", 110)
        current, _ = self.store.apply("DEMO.ag", change(first, [changed]))
        server, conflicts = self.store.apply("DEMO.ag", change(first, [drawing("a", 120)]))
        self.assertEqual(conflicts, ["a"])
        self.assertEqual(server["objects"][0]["points"][0]["price"], 110)
        self.assertEqual(self.store.get("DEMO.ag")["revision"], current["revision"])

    def test_delete_and_metadata_conflicts(self):
        base = self.store.get("DEMO.ag")
        first, _ = self.store.apply("DEMO.ag", change(base, [drawing("a")]))
        changed = copy.deepcopy(first)
        changed["title"] = "服务器标题"
        current, _ = self.store.apply("DEMO.ag", {
            "base_revision": first["revision"], "title": "服务器标题", "base_title": first["title"],
            "objects": [], "delete_ids": [], "base_objects": {}})
        payload = change(first)
        payload.update(title="本地标题", base_title=first["title"])
        server, conflicts = self.store.apply("DEMO.ag", payload)
        self.assertEqual(conflicts, ["title"])
        self.assertEqual(server["title"], current["title"])
        _, conflicts = self.store.apply("DEMO.ag", change(first, deletes=["a"]))
        self.assertEqual(conflicts, [])

    def test_file_round_trip_and_corruption_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plans.json"
            store = SharedPlanStore(path)
            base = store.get("DEMO.rb")
            store.apply("DEMO.rb", change(base, [drawing()]))
            loaded = SharedPlanStore(path)
            self.assertEqual(len(loaded.get("DEMO.rb")["objects"]), 1)
            path.write_text("broken", encoding="utf-8")
            damaged = SharedPlanStore(path)
            self.assertIsNotNone(damaged.load_error)
            with self.assertRaises(ValueError):
                damaged.get("DEMO.rb")


class BoardDemoApiTests(unittest.TestCase):
    def setUp(self):
        self.original = web_app._board_demo_store
        web_app._board_demo_store = SharedPlanStore()
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app._board_demo_store = self.original

    def test_get_put_and_conflict_response(self):
        response = self.client.get("/api/board-demo/plans/DEMO.ag")
        self.assertEqual(response.status_code, 200)
        base = response.get_json()["plan"]
        response = self.client.put("/api/board-demo/plans/DEMO.ag", json=change(base, [drawing()]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["plan"]["revision"], 1)
        self.assertEqual(self.client.get("/api/board-demo/plans/UNKNOWN").status_code, 400)

    def test_event_subscriber_receives_saved_plan(self):
        listener = web_app._board_demo_store.subscribe("DEMO.au")
        base = web_app._board_demo_store.get("DEMO.au")
        self.client.put("/api/board-demo/plans/DEMO.au", json=change(base, [drawing()]))
        event = listener.get_nowait()
        self.assertEqual(event["symbol"], "DEMO.au")
        self.assertEqual(event["plan"]["revision"], 1)
        with self.assertRaises(queue.Empty):
            listener.get_nowait()


if __name__ == "__main__":
    unittest.main()
