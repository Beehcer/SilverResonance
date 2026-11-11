"""Validated file-cache reads must retain cross-process durability and fail closed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from workspace_store import WorkspaceStore, WorkspaceUnavailable


SYMBOL = "SHFE.rb2701"


class WorkspaceStoreCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "workspace.json"
        self.store = WorkspaceStore(self.path)
        self.token = self.store.anonymous_session()
        self.user = self.store.authenticate(self.token)["id"]
        self.store.save_plan(SYMBOL, 0, {"title": "Alpha", "notes": "original", "objects": []}, self.user, "rules")

    def replace_title(self, title, preserve_mtime=False):
        before = self.path.stat()
        payload = self.path.read_bytes().replace(b'"title":"Alpha"', ('"title":"' + title + '"').encode())
        replacement = self.path.with_name("replacement.json")
        replacement.write_bytes(payload)
        if preserve_mtime:
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, self.path)

    def edit(self, **changes):
        return self.store.patch_plan(SYMBOL, 1, changes, [], [], self.user, "rules", "editor_wins")

    def test_unchanged_reads_check_file_metadata_without_reparsing_or_revalidating(self):
        with patch("workspace_store.json.load", wraps=json.load) as load, \
                patch.object(self.store, "_validate_state", wraps=self.store._validate_state) as validate, \
                patch.object(self.store, "_disk_signature", wraps=self.store._disk_signature) as signature:
            for _ in range(5):
                self.assertEqual(self.store.authenticate(self.token)["id"], self.user)
                self.assertEqual(self.store.plan(SYMBOL)["title"], "Alpha")
                self.assertEqual(self.store.risk(self.user), {})
            self.assertEqual(signature.call_count, 15)
            load.assert_not_called()
            validate.assert_not_called()

    def test_own_validated_write_updates_state_and_signature_together(self):
        before = self.store._file_signature
        with patch("workspace_store.json.load", wraps=json.load) as load, \
                patch.object(self.store, "_validate_state", wraps=self.store._validate_state) as validate:
            self.edit(notes="saved")
            self.assertEqual(self.store.plan(SYMBOL)["notes"], "saved")
            self.assertEqual(self.store.risk(self.user), {})
            self.assertEqual(validate.call_count, 1)
            load.assert_not_called()
        self.assertNotEqual(self.store._file_signature, before)
        self.assertEqual(self.store._file_signature, self.store._disk_signature())
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)["notes"], "saved")

    def test_peer_instance_writes_are_loaded_once_and_independent_edits_are_preserved(self):
        peer = WorkspaceStore(self.path)
        peer.patch_plan(SYMBOL, 1, {"notes": "peer"}, [], [], self.user, "rules", "editor_wins")
        with patch("workspace_store.json.load", wraps=json.load) as load, \
                patch.object(self.store, "_validate_state", wraps=self.store._validate_state) as validate:
            self.assertEqual(self.store.plan(SYMBOL)["notes"], "peer")
            self.assertEqual(self.store.plan(SYMBOL)["revision"], 2)
            self.assertEqual(load.call_count, 1)
            self.assertEqual(validate.call_count, 1)
        result = self.edit(title="Mine")
        self.assertEqual((result["title"], result["notes"], result["revision"]), ("Mine", "peer", 3))
        self.assertEqual(peer.plan(SYMBOL), result)

    def test_same_size_atomic_replacement_with_restored_mtime_is_not_missed(self):
        before = self.path.stat()
        self.replace_title("Bravo", preserve_mtime=True)
        after = self.path.stat()
        self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertEqual(self.store.plan(SYMBOL)["title"], "Bravo")
        self.assertEqual(self.store._file_signature, self.store._disk_signature())

    def test_same_size_in_place_edit_with_restored_mtime_is_detected_by_change_time(self):
        before = self.path.stat()
        signature = self.store._file_signature
        self.path.write_bytes(self.path.read_bytes().replace(b'"title":"Alpha"', b'"title":"Bravo"'))
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = self.path.stat()
        self.assertEqual((after.st_ino, after.st_size, after.st_mtime_ns), (before.st_ino, before.st_size, before.st_mtime_ns))
        self.assertNotEqual(self.store._disk_signature(), signature)
        self.assertEqual(self.store.plan(SYMBOL)["title"], "Bravo")

    def test_corrupt_cached_file_fails_closed_and_requires_a_new_store_after_repair(self):
        original = self.path.read_bytes()
        self.path.write_text('{"schema_version":NaN}', encoding="utf-8")
        with self.assertRaises(WorkspaceUnavailable):
            self.store.plan(SYMBOL)
        self.path.write_bytes(original)
        with self.assertRaises(WorkspaceUnavailable):
            self.store.plan(SYMBOL)
        with self.assertRaises(WorkspaceUnavailable):
            self.store.anonymous_session()
        self.assertEqual(WorkspaceStore(self.path).plan(SYMBOL)["title"], "Alpha")

    def test_disappeared_cached_file_cannot_be_reinitialized_even_if_it_was_empty(self):
        empty_path = self.path.with_name("empty.json")
        empty_path.write_text(json.dumps(WorkspaceStore._empty()), encoding="utf-8")
        empty = WorkspaceStore(empty_path)
        for store, path in ((self.store, self.path), (empty, empty_path)):
            with self.subTest(empty=store is empty):
                path.unlink()
                with self.assertRaises(WorkspaceUnavailable):
                    store.plan(SYMBOL)
                with self.assertRaises(WorkspaceUnavailable):
                    store.anonymous_session()
                self.assertFalse(path.exists())

    def test_new_missing_file_can_still_initialize_once(self):
        path = self.path.with_name("new.json")
        new = WorkspaceStore(path)
        self.assertIsNone(new.plan(SYMBOL))
        token = new.anonymous_session()
        self.assertTrue(path.is_file())
        self.assertIsNotNone(new.authenticate(token))
        self.assertEqual(new._file_signature, new._disk_signature())

    def test_mutable_return_values_cannot_change_the_validated_cached_state(self):
        plan = self.store.plan(SYMBOL)
        plan["title"] = "unsaved"
        plan["objects"].append({"id": "invalid"})
        risk = self.store.risk(self.user)
        risk["budget"] = -1
        member = self.store.authenticate(self.token)
        member["name"] = "unsaved"
        self.assertEqual(self.store.plan(SYMBOL)["title"], "Alpha")
        self.assertEqual(self.store.plan(SYMBOL)["objects"], [])
        self.assertEqual(self.store.risk(self.user), {})
        self.assertNotEqual(self.store.authenticate(self.token)["name"], "unsaved")

    def test_failed_atomic_write_does_not_mark_an_uncommitted_state_as_cached(self):
        before, signature = self.path.read_bytes(), self.store._file_signature
        with patch("workspace_store.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(WorkspaceUnavailable):
                self.edit(notes="uncommitted")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store._file_signature, signature)
        self.assertEqual(self.store.plan(SYMBOL)["notes"], "original")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_other_writers_atomic_replacement_cannot_be_bound_to_our_in_memory_state(self):
        signature = self.store._file_signature
        peer_file = self.path.with_name("peer.json")
        peer_file.write_bytes(self.path.read_bytes().replace(b'"title":"Alpha"', b'"title":"Bravo"'))
        replace = os.replace
        def concurrent_replace(source, destination):
            replace(source, destination)
            replace(peer_file, destination)
        with patch("workspace_store.os.replace", side_effect=concurrent_replace):
            with self.assertRaises(WorkspaceUnavailable):
                self.edit(notes="mine")
        self.assertEqual(self.store._file_signature, signature)
        self.assertEqual(self.store._state["plans"][SYMBOL]["notes"], "original")
        actual = self.store.plan(SYMBOL)
        self.assertEqual((actual["title"], actual["notes"]), ("Bravo", "original"))

    def test_commit_with_unreadable_result_does_not_hide_a_successfully_written_file(self):
        original = self.store._disk_signature
        count = 0
        def unreadable_commit():
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("metadata unavailable after replace")
            return original()
        with patch.object(self.store, "_disk_signature", side_effect=unreadable_commit):
            with self.assertRaises(WorkspaceUnavailable):
                self.edit(notes="committed")
        self.assertEqual(self.store._state["plans"][SYMBOL]["notes"], "original")
        self.assertEqual(self.store.plan(SYMBOL)["notes"], "committed")

    def test_change_while_validating_is_rejected_instead_of_caching_a_mismatched_snapshot(self):
        self.replace_title("Bravo")
        original = self.store._validate_state
        def replace_during_validation(state):
            original(state)
            content = self.path.read_bytes().replace(b'"title":"Bravo"', b'"title":"Delta"')
            self.path.write_bytes(content)
        with patch.object(self.store, "_validate_state", side_effect=replace_during_validation):
            with self.assertRaises(WorkspaceUnavailable):
                self.store.plan(SYMBOL)
        self.assertEqual(self.store._state["plans"][SYMBOL]["title"], "Alpha")
        self.assertTrue(self.store._load_error)

    def test_metadata_read_error_cannot_return_the_last_cached_plan(self):
        with patch.object(self.store, "_disk_signature", side_effect=OSError("unavailable")):
            with self.assertRaises(WorkspaceUnavailable):
                self.store.plan(SYMBOL)
        self.assertTrue(self.store._load_error)

    def test_migration_and_durable_activity_touch_cache_their_new_file_signatures(self):
        state = json.loads(self.path.read_text(encoding="utf-8"))
        state.pop("mutations")
        self.path.write_text(json.dumps(state), encoding="utf-8")
        migrated = WorkspaceStore(self.path)
        self.assertEqual(migrated._file_signature, migrated._disk_signature())
        with patch("workspace_store.json.load", wraps=json.load) as load:
            self.assertEqual(migrated.plan(SYMBOL)["title"], "Alpha")
            with patch("workspace_store.time.time", return_value=time.time() + 61):
                self.assertIsNotNone(migrated.authenticate(self.token))
            self.assertEqual(migrated._file_signature, migrated._disk_signature())
            self.assertEqual(migrated.plan(SYMBOL)["title"], "Alpha")
            load.assert_not_called()

    def test_warm_cache_still_serializes_against_a_real_subprocess_writer(self):
        ready = self.path.with_name("ready")
        start = self.path.with_name("start")
        attempted = self.path.with_name("attempted")
        script = """import sys,time
from pathlib import Path
from workspace_store import WorkspaceStore
path,user,ready,start,attempted=sys.argv[1:]
store=WorkspaceStore(path)
Path(ready).touch()
deadline=time.monotonic()+8
while not Path(start).exists():
    if time.monotonic()>deadline: raise RuntimeError('start timeout')
    time.sleep(.01)
Path(attempted).touch()
store.patch_plan('SHFE.rb2701',1,{'notes':'child'},[],[],user,'rules','editor_wins')
"""
        process = subprocess.Popen([sys.executable, "-c", script, str(self.path), self.user, str(ready), str(start), str(attempted)],
                                   cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def wait_for(path):
            deadline = time.monotonic() + 8
            while not path.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(.01)
            self.assertTrue(path.exists(), "subprocess did not reach expected checkpoint")
        try:
            wait_for(ready)
            with self.store._locked():
                start.touch()
                wait_for(attempted)
                self.assertIsNone(process.poll())
                self.assertEqual(self.store._state["plans"][SYMBOL]["notes"], "original")
            output, error = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 0, error.decode(errors="replace"))
            self.assertEqual(output, b"")
            self.assertEqual(self.store.plan(SYMBOL)["notes"], "child")
            self.assertEqual(self.edit(title="Mine")["notes"], "child")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
