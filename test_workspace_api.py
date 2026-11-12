"""Offline anonymous collaboration, privacy, merge, and durability checks."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import Flask

from workspace_api import COOKIE_NAME, register_workspace
from workspace_store import WorkspaceStore, WorkspaceUnavailable


SYMBOL = "SHFE.ag2612"
RISK = {"lots": 2, "budget": 1800, "daily_limit": 3000, "daily_loss": 200,
        "fee_per_lot": 10, "slippage_ticks": 1}


def drawing(identity, price):
    return {"id": identity, "kind": "horizontal", "points": [{"time": 1794200000000, "price": price}],
            "period": "all", "color": "#2563eb"}


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "workspace.json"
        self.store = WorkspaceStore(self.path)
        self.market_calls = []
        self.rules = {"available": True, "compatible": True, "version": "rule-v1", "title": "Rules", "path": "private-path"}
        self.app = Flask(__name__)
        self.app.testing = True
        register_workspace(self.app, self.store, self.market,
                           lambda: [{"symbol": SYMBOL, "name": "沪银", "tick": 1, "multiplier": 15}],
                           lambda: self.rules, self.evaluate)
        self.owner = self.app.test_client()
        response = self.owner.get("/api/workspace/session")
        self.assertEqual(response.status_code, 200, response.json)
        self.owner_session = response.json
        self.owner_headers = {"X-Workspace-CSRF": response.json["csrf_token"]}
        self.owner.post("/api/workspace/profile", json={"name": "Owner"}, headers=self.owner_headers)

    def market(self, symbol):
        self.market_calls.append(symbol)
        return {"symbol": symbol, "source": "fixture", "connected": False, "stale": True,
                "periods": {key: [{"time": 1893456000000}] for key in ("day", "m30", "m3")}}

    @staticmethod
    def evaluate(plan, market, risk, version):
        return {"status": "insufficient", "risk": risk, "checked_version": version}

    def join(self, name):
        client = self.app.test_client()
        response = client.get("/api/workspace/session")
        self.assertEqual(response.status_code, 200, response.json)
        headers = {"X-Workspace-CSRF": response.json["csrf_token"]}
        response = client.post("/api/workspace/profile", json={"name": name}, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        return client, headers

    def patch_plan(self, revision, changes=None, upserts=None, deletes=None, client=None, headers=None):
        return (client or self.owner).post("/api/workspace/plans/" + SYMBOL + "/patch",
            json={"base_revision": revision, "changes": changes or {}, "upsert_objects": upserts or [],
                  "delete_object_ids": deletes or []}, headers=headers or self.owner_headers)

    def save(self, plan, revision=0, client=None, headers=None):
        return (client or self.owner).put("/api/workspace/plans/" + SYMBOL,
                                         json={"base_revision": revision, "plan": plan},
                                         headers=headers or self.owner_headers)

    def test_many_browsers_have_private_risk_without_accounts(self):
        second, second_headers = self.join("Second")
        third, third_headers = self.join("Third")
        for index in range(5):
            self.join("Extra " + str(index))
        self.assertEqual(self.owner.put("/api/workspace/risk", json={"risk": RISK}, headers=self.owner_headers).status_code, 200)
        second_risk = {**RISK, "lots": 1, "budget": 800}
        second.put("/api/workspace/risk", json={"risk": second_risk}, headers=second_headers)
        self.assertEqual(third.get("/api/workspace/risk").json["risk"], {})
        self.assertEqual(self.owner.get("/api/workspace/risk").json["risk"], RISK)
        self.assertEqual(second.get("/api/workspace/review?symbol=" + SYMBOL).json["evaluation"]["risk"], second_risk)
        session = second.get("/api/workspace/session").json
        self.assertEqual(len(session["members"]), 8)
        self.assertNotIn("invite_code", session)
        for member in session["members"]:
            self.assertIn("color", member)
            self.assertNotIn("risk", member)
        for client in (self.owner, second, third):
            for endpoint in ("session", "history?symbol=" + SYMBOL, "rules", "symbols"):
                payload = client.get("/api/workspace/" + endpoint).get_data(as_text=True)
                for secret in ("password_hash", "token_hash", "private-path", '\"budget\"'):
                    self.assertNotIn(secret, payload)

    def test_anonymous_restore_csrf_origin_and_cookie_safety(self):
        fresh = self.app.test_client()
        self.assertEqual(fresh.get("/api/workspace/market?symbol=" + SYMBOL).status_code, 401)
        self.assertEqual(fresh.get("/api/workspace/session", headers={"Origin": "https://attacker.example"}).status_code, 403)
        response = fresh.get("/api/workspace/session")
        session = response.json
        self.assertTrue(session["authenticated"])
        self.assertTrue(session["anonymous"])
        self.assertFalse(session["needs_setup"])
        self.assertNotIn("invite_code", session)
        self.assertEqual(fresh.get("/api/workspace/session").json["user"]["id"], session["user"]["id"])
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        token = fresh.get_cookie(COOKIE_NAME, path="/api/workspace").value
        self.assertNotIn(token, self.path.read_text(encoding="utf-8"))
        self.assertNotIn("password", self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.owner.put("/api/workspace/risk", json={"risk": RISK}).status_code, 403)
        self.assertEqual(self.owner.put("/api/workspace/risk", json={"risk": RISK},
                                       headers={**self.owner_headers, "Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.owner.put("/api/workspace/risk", json={"risk": RISK},
                                       headers={**self.owner_headers, "Origin": "http://localhost"}).status_code, 200)
        for endpoint in ("setup", "join", "login", "invite"):
            self.assertEqual(fresh.post("/api/workspace/" + endpoint, json={}).status_code, 404)

    def test_remote_browser_needs_no_setup(self):
        client = self.app.test_client()
        response = client.get("/api/workspace/session", base_url="http://192.0.2.10",
                              environ_overrides={"REMOTE_ADDR": "192.0.2.11"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["anonymous"])
        self.assertFalse(response.json["needs_setup"])

    def test_revision_conflict_and_immutable_activation(self):
        second, headers = self.join("Second")
        draft = {"title": "Original", "rule_version": "rule-v1", "enabled": True, "objects": []}
        first = self.save(draft)
        self.assertEqual(first.status_code, 200, first.json)
        plan = first.json["plan"]
        self.assertEqual(plan["revision"], 1)
        snapshot = copy.deepcopy(plan["activated_snapshot"])
        stale = self.save({"title": "Lost edit"}, 0, second, headers)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json["plan"], plan)
        plan["title"] = "Second edit"
        plan["enabled"] = False
        plan["updated_by"] = {"id": "forged", "name": "Forged"}
        plan["activated_snapshot"] = {"title": "tampered"}
        second_save = self.save(plan, 1, second, headers)
        self.assertEqual(second_save.status_code, 200, second_save.json)
        saved = second_save.json["plan"]
        self.assertEqual(saved["updated_by"]["name"], "Second")
        self.assertEqual(saved["activated_snapshot"], snapshot)
        history = self.owner.get("/api/workspace/history?symbol=" + SYMBOL).json["history"]
        self.assertEqual([item["revision"] for item in history], [2, 1])
        self.assertEqual(history[1]["plan"]["title"], "Original")
        self.assertEqual(history[0]["actor"]["name"], "Second")
        reloaded = WorkspaceStore(self.path)
        self.assertEqual(reloaded.plan(SYMBOL), saved)

    def test_validation_and_rule_updates_fail_closed(self):
        for draft in ({"main_level": float("nan")}, {"enabled": "yes"}, {"injected": True},
                      {"enabled": True, "rule_version": "old"}, {"objects": "bad"}):
            self.assertEqual(self.save(draft).status_code, 400)
        self.rules["compatible"] = False
        self.assertEqual(self.save({"enabled": True, "rule_version": "rule-v1"}).status_code, 400)
        self.assertEqual(self.save({"enabled": False, "title": "Allowed draft"}).status_code, 200)
        self.assertEqual(self.owner.get("/api/workspace/review?symbol=" + SYMBOL).json["evaluation"]["checked_version"], "")
        for risk in ({"lots": 1.5}, {"budget": -1}, {"capital": 100}, {"daily_limit": 0}, {"fee_per_lot": float("inf")}):
            self.assertEqual(self.owner.put("/api/workspace/risk", json={"risk": risk}, headers=self.owner_headers).status_code, 400)

    def test_stale_patches_merge_different_fields_and_objects(self):
        second, headers = self.join("Second")
        first = self.save({"title": "Initial", "objects": [drawing("a", 100), drawing("b", 200)]}).json["plan"]
        response = self.patch_plan(1, {"notes": "One note"}, [drawing("a", 101)])
        self.assertEqual(response.status_code, 200, response.json)
        response = self.patch_plan(1, {"title": "Other title"}, [drawing("b", 202)], client=second, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        plan = response.json["plan"]
        self.assertEqual(plan["revision"], 3)
        self.assertEqual(plan["title"], "Other title")
        self.assertEqual(plan["notes"], "One note")
        self.assertEqual([obj["points"][0]["price"] for obj in plan["objects"]], [101, 202])
        # A collaborator can edit an object originally created or moved by another.
        response = self.patch_plan(3, upserts=[drawing("a", 105)], client=second, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["plan"]["updated_by"]["name"], "Second")

    def test_patch_conflicts_only_on_same_field_or_same_object(self):
        self.save({"title": "First", "objects": [drawing("a", 100)]})
        self.patch_plan(1, {"title": "Changed"}, [drawing("a", 101)])
        conflict = self.patch_plan(1, {"title": "Conflicting", "notes": "Must not partly save"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json["conflicts"], ["title"])
        self.assertNotIn("notes", self.store.plan(SYMBOL))
        self.assertEqual(self.patch_plan(1, upserts=[drawing("a", 102)]).json["conflicts"], ["objects:a"])
        self.assertEqual(self.patch_plan(1, deletes=["a"]).status_code, 409)
        # Same intended value converges without inventing an extra revision.
        equal = self.patch_plan(1, {"title": "Changed"}, [drawing("a", 101)])
        self.assertEqual(equal.status_code, 200)
        self.assertEqual(equal.json["plan"]["revision"], 2)
        removed = self.patch_plan(2, deletes=["a"])
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json["plan"]["objects"], [])

    def test_concurrent_independent_patches_across_store_instances(self):
        self.save({"title": "First", "objects": [drawing("a", 100), drawing("b", 200)]})
        other = WorkspaceStore(self.path)
        barrier = threading.Barrier(2)
        results = []
        def writer(store, identity, price):
            barrier.wait()
            try:
                results.append(store.patch_plan(SYMBOL, 1, {}, [drawing(identity, price)], [],
                                               self.owner_session["user"]["id"], "rule-v1"))
            except Exception as exc:
                results.append(exc)
        threads = [threading.Thread(target=writer, args=(store, identity, price))
                   for store, identity, price in ((self.store, "a", 101), (other, "b", 202))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertTrue(all(isinstance(result, dict) for result in results), results)
        saved = self.store.plan(SYMBOL)
        self.assertEqual(saved["revision"], 3)
        self.assertEqual([obj["points"][0]["price"] for obj in saved["objects"]], [101, 202])

    def test_patch_validation_rule_gate_and_failure_are_atomic(self):
        self.save({"title": "First"})
        for changes in ({"activation_at": "2000-01-01T00:00:00Z"}, {"objects": []}, {"unknown": True},
                        {"enabled": True, "rule_version": "old"}):
            self.assertEqual(self.patch_plan(1, changes).status_code, 400)
        self.assertEqual(self.patch_plan(1, upserts=[drawing("a", 100), drawing("a", 101)]).status_code, 400)
        before = self.path.read_bytes()
        with patch("workspace_store.os.replace", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.patch_plan(1, {"title": "After"}).status_code, 503)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.plan(SYMBOL)["revision"], 1)

    def test_presence_and_drag_preview_are_ephemeral_and_private(self):
        second, headers = self.join("Second")
        self.owner.put("/api/workspace/risk", json={"risk": RISK}, headers=self.owner_headers)
        before = self.path.read_bytes()
        payload = {"symbol": SYMBOL, "period": "m30", "object_id": "a", "cursor": {"time": 1794200000000, "price": 100},
                   "preview_object": drawing("a", 101)}
        with patch("workspace_store.time.monotonic", return_value=100):
            response = self.owner.post("/api/workspace/presence", json=payload, headers=self.owner_headers)
            self.assertEqual(response.status_code, 200, response.json)
            others = second.get("/api/workspace/presence?symbol=" + SYMBOL).json["presence"]
            self.assertEqual(others[0]["preview_object"]["id"], "a")
            self.assertNotIn("risk", others[0])
            self.assertNotIn("seen_at", others[0])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(WorkspaceStore(self.path).presence(SYMBOL), [])
        with patch("workspace_store.time.monotonic", return_value=116):
            self.assertEqual(self.store.presence(SYMBOL), [])

    def test_sse_delivers_revision_and_presence_without_private_risk(self):
        self.save({"title": "First"})
        self.owner.post("/api/workspace/presence", json={"symbol": SYMBOL, "period": "m3"}, headers=self.owner_headers)
        response = self.owner.get("/api/workspace/events?symbol=" + SYMBOL, buffered=False)
        try:
            chunks = iter(response.response)
            self.assertIn(b"retry: 500", next(chunks))
            message = next(chunks).decode("utf-8")
            self.assertIn("event: workspace", message)
            self.assertIn('"revision":1', message)
            self.assertIn('"period":"m3"', message)
            self.assertNotIn("risk", message)
            self.assertNotIn("token", message)
        finally:
            response.close()

    def test_existing_storage_migrates_without_credentials_or_plan_loss(self):
        self.save({"title": "Preserved"})
        self.owner.put("/api/workspace/risk", json={"risk": RISK}, headers=self.owner_headers)
        legacy = json.loads(self.path.read_text(encoding="utf-8"))
        legacy["schema_version"] = 1
        legacy["invite_code"] = "old-code-no-longer-used"
        for member in legacy["members"].values():
            member.pop("color")
            member["role"] = "owner"
            member["password_hash"] = "old$hash$not-used"
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        migrated = WorkspaceStore(self.path)
        self.assertEqual(migrated.plan(SYMBOL)["title"], "Preserved")
        self.assertEqual(migrated.risk(self.owner_session["user"]["id"]), RISK)
        content = self.path.read_text(encoding="utf-8")
        self.assertNotIn("password_hash", content)
        self.assertNotIn("invite_code", content)

    def test_activation_time_is_owned_by_server_and_cosmetic_edits_preserve_it(self):
        draft = {"title": "Initial", "enabled": True, "rule_version": "rule-v1", "main_level": 100,
                 "activation_at": "2000-01-01T00:00:00+00:00"}
        first_time = "2026-11-09T08:00:00+00:00"
        second_time = "2026-11-09T08:05:00+00:00"
        with patch("workspace_store._now", return_value=first_time):
            response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.json)
        first = response.json["plan"]
        self.assertEqual(first["activation_at"], first_time)
        with patch("workspace_store._now", return_value=second_time):
            cosmetic = self.save({**first, "title": "Renamed", "notes": "More explanation"}, 1).json["plan"]
        self.assertEqual(cosmetic["activation_at"], first_time)
        self.assertEqual(cosmetic["activated_snapshot"], first["activated_snapshot"])
        with patch("workspace_store._now", return_value=second_time):
            changed = self.save({**cosmetic, "main_level": 101}, 2).json["plan"]
        self.assertEqual(changed["activation_at"], second_time)
        self.assertEqual(changed["activated_snapshot"]["main_level"], 101)
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["activations"][SYMBOL]), 2)
        self.assertEqual(persisted["activations"][SYMBOL][0], first["activated_snapshot"])
        self.assertEqual(len(self.store.history(SYMBOL)), 3)

    def test_history_retention_keeps_immutable_activation_archive(self):
        first = self.save({"enabled": True, "rule_version": "rule-v1", "title": "First"}).json["plan"]
        for revision in range(1, 203):
            response = self.save({"enabled": False, "title": "Draft " + str(revision)}, revision)
            self.assertEqual(response.status_code, 200, response.json)
        history = self.store.history(SYMBOL)
        self.assertEqual(len(history), 200)
        self.assertEqual(history[0]["revision"], 203)
        self.assertEqual(history[-1]["revision"], 4)
        self.assertEqual(self.store.plan(SYMBOL)["activated_snapshot"], first["activated_snapshot"])
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)["revision"], 203)

    def test_failed_write_rolls_back_everything(self):
        first = self.save({"title": "Before"}).json["plan"]
        disk_before = self.path.read_bytes()
        with patch("workspace_store.os.replace", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.save({"title": "After", "enabled": True, "rule_version": "rule-v1"}, 1).status_code, 503)
            self.assertEqual(self.owner.put("/api/workspace/risk", json={"risk": RISK}, headers=self.owner_headers).status_code, 503)
            self.assertEqual(self.owner.post("/api/workspace/profile", json={"name":"Changed"}, headers=self.owner_headers).status_code, 503)
        self.assertEqual(self.path.read_bytes(), disk_before)
        self.assertEqual(self.store.plan(SYMBOL), first)
        self.assertEqual(len(self.store.history(SYMBOL)), 1)
        self.assertEqual(self.owner.get("/api/workspace/risk").json["risk"], {})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_corrupt_or_missing_storage_never_becomes_setup(self):
        self.path.write_text('{"schema_version":1}', encoding="utf-8")
        broken = WorkspaceStore(self.path)
        with self.assertRaises(WorkspaceUnavailable):
            broken.session()
        self.assertEqual(self.owner.get("/api/workspace/session").status_code, 503)
        self.path.unlink()
        self.assertEqual(self.owner.get("/api/workspace/session").status_code, 503)

    def test_parallel_saves_across_store_instances_never_overwrite(self):
        other = WorkspaceStore(self.path)
        barrier = threading.Barrier(2)
        results = []
        def writer(store, title):
            barrier.wait()
            try:
                results.append(store.save_plan(SYMBOL, 0, {"title": title}, self.owner_session["user"]["id"], "rule-v1"))
            except Exception as exc:
                results.append(exc)
        threads = [threading.Thread(target=writer, args=(store, title)) for store, title in ((self.store, "One"), (other, "Two"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1, results)
        self.assertEqual(sum(getattr(result, "status_code", None) == 409 for result in results), 1, results)
        self.assertEqual(self.store.plan(SYMBOL)["revision"], 1)

    def test_profile_is_editable_without_identity_or_risk_spoofing(self):
        second, headers = self.join("Second")
        response = second.post("/api/workspace/profile", json={"name":"同一个昵称", "color":"#FFAA33"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["user"]["color"], "#ffaa33")
        self.assertEqual(second.post("/api/workspace/profile", json={"name":"Name", "id":"forged"}, headers=headers).status_code, 400)
        self.assertEqual(second.put("/api/workspace/risk", json={"risk":RISK,"user_id":self.owner_session["user"]["id"]}, headers=headers).status_code, 400)
        self.assertEqual(self.owner.get("/api/workspace/risk").json["risk"], {})

    def test_request_bounds_and_invalid_symbol_do_not_fetch_market(self):
        oversized = self.owner.put("/api/workspace/plans/" + SYMBOL, data='{"notes":"' + "x" * 140000 + '"}',
                                  content_type="application/json", headers=self.owner_headers)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(self.owner.get("/api/workspace/market?symbol=UNKNOWN").status_code, 400)
        self.assertEqual(self.market_calls, [])
        self.assertEqual(self.owner.put("/api/workspace/risk", data="broken", content_type="application/json",
                                       headers=self.owner_headers).status_code, 400)
        self.assertEqual(self.owner.put("/api/workspace/risk", data="{}", headers=self.owner_headers).status_code, 400)

    def test_expired_session_denies_access(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for session in data["sessions"].values():
            session["expires"] = 0
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.owner.get("/api/workspace/risk").status_code, 401)
        restored = self.owner.get("/api/workspace/session").json
        self.assertTrue(restored["authenticated"])
        self.assertNotEqual(restored["user"]["id"], self.owner_session["user"]["id"])


if __name__ == "__main__":
    unittest.main()
