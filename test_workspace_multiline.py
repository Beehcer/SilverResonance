"""Multiline text uses name end to end; legacy drawing schemas stay readable."""
import json
import unittest

import test_workspace_api as base
from test_workspace_api import SYMBOL
from test_workspace_rules import fixture, VERSION
from workspace_rules import evaluate_plan, validate_plan
from workspace_store import WorkspaceStore


def text(content="old", **extra):
    return {"id": "text", "kind": "text", "period": "m30", "name": content,
            "note": "separate note", "color": "#f1c40f", "points": [{"time": 1700000000000, "price": 100}], **extra}


class MultilineRuleTests(unittest.TestCase):
    def test_text_normalizes_newlines_without_stripping_indentation_or_empty_lines(self):
        content = "  开盘计划\r\n\r\n等待回抽\r受阻再看空\n"
        raw = {"objects": [text(content)]}
        clean = validate_plan(raw)["objects"][0]
        self.assertEqual(clean["name"], "  开盘计划\n\n等待回抽\n受阻再看空\n")
        self.assertEqual(clean["note"], "separate note")
        self.assertEqual(raw["objects"][0]["name"], content)

    def test_two_thousand_characters_including_unicode_are_accepted_without_truncation(self):
        for content in ("", "文"*2000, "😀"*2000, "行\r\n"*1000):
            with self.subTest(length=len(content)):
                self.assertEqual(validate_plan({"objects": [text(content)]})["objects"][0]["name"], content.replace("\r\n", "\n"))
        for content in ("文"*2001, "😀"*2001, "bad\x00text", "bad\x1ftext", 123, False, None):
            with self.subTest(invalid_type=type(content).__name__):
                with self.assertRaises(ValueError):
                    validate_plan({"objects": [text(content)]})

    def test_non_text_names_keep_the_preexisting_backend_schema_allowance(self):
        raw = {"objects": [{**text("old\r\n" + "x"*500), "kind": "horizontal"}]}
        self.assertEqual(validate_plan(raw)["objects"][0]["name"], raw["objects"][0]["name"])
        with self.assertRaises(ValueError):
            validate_plan({"objects": [{**text("x"*20001), "kind": "horizontal"}]})

    def test_legacy_text_names_remain_readable_but_cannot_be_changed_to_another_oversized_value(self):
        old = text("legacy\r\n" + "x"*2100)
        self.assertEqual(validate_plan({"objects": [old]}, preserve_legacy_names=True)["objects"][0]["name"], old["name"])
        moved = {**old, "color": "#00ff00"}
        self.assertEqual(validate_plan({"objects": [moved]}, existing_objects=[old])["objects"][0]["name"], old["name"])
        with self.assertRaises(ValueError):
            validate_plan({"objects": [{**old, "name": "y"*2100}]}, existing_objects=[old])

    def test_legacy_content_does_not_change_trading_rule_evaluation(self):
        plan, market, risk = fixture()
        expected = evaluate_plan(plan, market, risk, VERSION)["status"]
        plan["objects"] = [text("x"*2100)]
        self.assertEqual(evaluate_plan(plan, market, risk, VERSION)["status"], expected)


class MultilineCollaborationTests(unittest.TestCase):
    setUp = base.WorkspaceApiTests.setUp
    market = base.WorkspaceApiTests.market
    evaluate = staticmethod(base.WorkspaceApiTests.evaluate)
    join = base.WorkspaceApiTests.join
    save = base.WorkspaceApiTests.save

    def patch_name(self, content, **extra):
        return self.owner.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
            "base_revision": 1, "conflict_policy": "editor_wins", "changes": extra,
            "object_edits": [{"id": "text", "changes": {"name": content}}]}, headers=self.owner_headers)

    def test_multiline_full_save_patch_peer_read_and_restart_round_trip(self):
        content = "  等待回抽\r\n\r\n" + "保护位置明确"*25 + "\r受阻再看空\n"
        created = self.save({"objects": [text(content)]})
        self.assertEqual(created.status_code, 200, created.json)
        expected = content.replace("\r\n", "\n").replace("\r", "\n")
        self.assertEqual(created.json["plan"]["objects"][0]["name"], expected)
        peer, _ = self.join("Peer")
        shared = peer.get('/api/workspace/plans/' + SYMBOL).json["plan"]
        self.assertEqual(shared["objects"][0]["name"], expected)
        changed = self.patch_name("新的\r\n计划\n\n" + "文"*1988)
        self.assertEqual(changed.status_code, 200, changed.json)
        latest = changed.json["plan"]["objects"][0]
        self.assertEqual(latest["name"], "新的\n计划\n\n" + "文"*1988)
        self.assertEqual(latest["note"], "separate note")
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)["objects"][0], latest)

    def test_newline_patch_preserves_concurrent_peer_color_geometry_and_note(self):
        self.save({"objects": [text()]})
        peer, headers = self.join("Peer")
        response = peer.post('/api/workspace/plans/' + SYMBOL + '/patch', json={
            "base_revision": 1, "conflict_policy": "editor_wins", "object_edits": [{"id": "text", "changes": {
                "color": "#00ff00", "note": "peer note", "points": [{"time": 1700000000000, "price": 103}]}}]}, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        updated = self.patch_name("等反弹\r\n再观察受阻")
        self.assertEqual(updated.status_code, 200, updated.json)
        obj = updated.json["plan"]["objects"][0]
        self.assertEqual((obj["name"], obj["color"], obj["note"], obj["points"][0]["price"]), ("等反弹\n再观察受阻", "#00ff00", "peer note", 103))

    def test_invalid_multiline_content_rejects_the_whole_patch_without_partial_save(self):
        self.save({"objects": [text()], "notes": "original"})
        for content in ("x"*2001, "bad\0name", False):
            response = self.patch_name(content, notes="must not save")
            self.assertEqual(response.status_code, 400, response.json)
            actual = self.store.plan(SYMBOL)
            self.assertEqual((actual["revision"], actual["notes"], actual["objects"][0]["name"]), (1, "original", "old"))

    def test_presence_preview_normalizes_and_shares_name_without_persisting_it(self):
        self.save({"objects": [text()]})
        peer, _ = self.join("Peer")
        preview = text("第一行\r\n\r\n第二行\r第三行")
        response = self.owner.post('/api/workspace/presence', json={"symbol": SYMBOL, "period": "m30", "preview_object": preview}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)
        presence = peer.get('/api/workspace/presence?symbol=' + SYMBOL).json["presence"]
        shared = next(item for item in presence if item["id"] == self.owner_session["user"]["id"])["preview_object"]
        self.assertEqual(shared["name"], "第一行\n\n第二行\n第三行")
        self.assertEqual(shared["note"], "separate note")
        self.assertEqual(self.store.plan(SYMBOL)["objects"][0]["name"], "old")
        preview["name"] = "x"*2001
        rejected = self.owner.post('/api/workspace/presence', json={"symbol": SYMBOL, "period": "m30", "preview_object": preview}, headers=self.owner_headers)
        self.assertEqual(rejected.status_code, 400)

    def test_existing_long_names_do_not_lock_storage_or_block_unrelated_edits(self):
        self.save({"objects": [text(), {**text("legacy line"), "id": "line", "kind": "horizontal"}]})
        legacy = json.loads(self.path.read_text(encoding="utf-8"))
        for plan in [legacy["plans"][SYMBOL], legacy["history"][SYMBOL][0]["plan"]]:
            plan["objects"][0]["name"] = "legacy\r\n" + "x"*2100
            plan["objects"][1]["name"] = "name\r\n" + "x"*500
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        reloaded = WorkspaceStore(self.path)
        actual = reloaded.plan(SYMBOL)
        self.assertEqual(actual["objects"], legacy["plans"][SYMBOL]["objects"])
        response = self.owner.post('/api/workspace/plans/' + SYMBOL + '/patch', json={"base_revision": 1, "changes": {"notes": "new note"}}, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200, response.json)
        current = response.json["plan"]
        self.assertEqual(current["objects"], actual["objects"])
        put = self.save({**current, "title": "new title"}, revision=current["revision"])
        self.assertEqual(put.status_code, 200, put.json)
        self.assertEqual(put.json["plan"]["objects"], actual["objects"])
        fixed = self.patch_name("新的\r\n两行")
        self.assertEqual(fixed.status_code, 200, fixed.json)
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)["objects"][0]["name"], "新的\n两行")

    def test_note_only_text_is_not_promoted_to_content_by_save_or_preview(self):
        obj = text()
        del obj["name"]
        response = self.save({"objects": [obj]})
        self.assertEqual(response.status_code, 200, response.json)
        saved = response.json["plan"]["objects"][0]
        self.assertNotIn("name", saved)
        self.assertEqual(saved["note"], "separate note")

    def test_unchanged_locked_legacy_text_keeps_its_exact_name_during_unrelated_save(self):
        self.save({"objects": [text("legacy", locked=True)]})
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        for plan in [persisted["plans"][SYMBOL], persisted["history"][SYMBOL][0]["plan"]]:
            plan["objects"][0]["name"] = "legacy\r\n" + "x"*2100
        self.path.write_text(json.dumps(persisted), encoding="utf-8")
        current = self.store.plan(SYMBOL)
        response = self.save({**current, "notes": "unrelated"}, revision=1)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["plan"]["objects"], current["objects"])


if __name__ == '__main__':
    unittest.main()
