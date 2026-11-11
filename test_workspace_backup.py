"""Private, bounded, consistent backups using only temporary local user data."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

import workspace_backup as backup
from workspace_store import WorkspaceStore


SYMBOL = "SHFE.rb2701"


class WorkspaceBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "state" / "workspace.json"
        self.store = WorkspaceStore(self.source)
        self.token = self.store.anonymous_session()
        self.user = self.store.authenticate(self.token)["id"]
        self.store.save_plan(SYMBOL, 0, {"notes": "Original", "objects": []}, self.user, "rules")
        self.now = 1800000000.0
        self.worker = backup.WorkspaceBackup(self.source, clock=lambda: self.now)

    def edit(self, note="Edited"):
        revision = self.store.plan(SYMBOL)["revision"]
        self.store.patch_plan(SYMBOL, revision, {"notes": note}, [], [], self.user, "rules")

    def files(self):
        return sorted(self.worker.directory.glob("workspace-*.tar.gz"))

    def read_source(self, path):
        with tarfile.open(path) as archive:
            return archive.extractfile("workspace.json").read()

    def initial(self):
        self.assertTrue(self.worker.tick())
        self.assertEqual(len(self.files()), 1)

    def test_snapshot_contains_exact_valid_original_and_private_atomic_files(self):
        before = self.source.read_bytes()
        self.initial()
        archive = self.files()[0]
        manifest = backup.verify_archive(archive)
        self.assertEqual(manifest["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.read_source(archive), before)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(manifest["kind"], "scheduled")
        self.assertEqual(list(self.worker.directory.glob("*.tmp")), [])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.worker.directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((self.worker.directory/"status.json").stat().st_mode), 0o600)
        status = json.loads((self.worker.directory/"status.json").read_text())
        self.assertEqual(status["counts"], {"change": 0, "scheduled": 1})
        self.assertNotIn(self.token, json.dumps(status))
        self.assertNotIn("Original", json.dumps(status))

    def test_changes_throttled_30seconds_and_idle_poll_is_metadata_only(self):
        self.initial()
        self.edit()
        self.now += 29
        self.assertFalse(self.worker.tick())
        self.now += 1
        self.assertTrue(self.worker.tick())
        self.now += 31
        with patch.object(self.worker, "_capture", side_effect=AssertionError("Unchanged poll read JSON")), \
                patch.object(self.worker, "_records", side_effect=AssertionError("Unchanged poll scanned archives")):
            self.assertFalse(self.worker.tick())
        self.assertEqual(len(self.files()), 2)

    def test_session_touches_do_not_consume_change_slots_but_full_bytes_are_archived(self):
        self.initial()
        def activity(state):
            for session in state["sessions"].values():
                session["touched"] += 100
            state["members"][self.user]["last_seen"] = "2030-01-01T00:00:00+00:00"
        self.store._change(activity)
        self.now += 31
        self.assertFalse(self.worker.tick())
        self.store.profile(self.user, "Custom profile", "#123456")
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.read_source(self.files()[-1]), self.source.read_bytes())
        self.now += 31
        self.store.save_risk(self.user, {"budget": 1000})
        self.assertTrue(self.worker.tick())

    def test_halfhour_idle_snapshot_and_restart_preserve_timers(self):
        self.initial()
        initial = self.files()[0]
        self.now += 20
        restarted = backup.WorkspaceBackup(self.source, clock=lambda: self.now)
        self.assertFalse(restarted.tick())
        self.now += 1779
        self.assertFalse(restarted.tick())
        self.now += 1
        self.assertTrue(restarted.tick())
        self.assertEqual(len(self.files()), 2)
        self.assertEqual(self.read_source(initial), self.read_source(self.files()[-1]))
        self.assertEqual(backup.verify_archive(self.files()[-1])["kind"], "scheduled")

    def test_retention_is_40_changes_plus_10_scheduled_not_fifty_busy_changes(self):
        self.initial()
        for _ in range(11):
            self.now += 1800
            self.assertTrue(self.worker.tick())
        for index in range(45):
            self.now += 31
            self.edit("Change " + str(index))
            self.assertTrue(self.worker.tick())
        manifests = [backup.verify_archive(path) for path in self.files()]
        self.assertEqual(len(manifests), 50)
        self.assertEqual(sum(item["kind"] == "change" for item in manifests), 40)
        self.assertEqual(sum(item["kind"] == "scheduled" for item in manifests), 10)

    def test_publish_failure_does_not_change_source_or_prune_and_retries_without_new_edit(self):
        self.initial()
        self.edit()
        original = self.source.read_bytes()
        self.now += 31
        with patch.object(self.worker, "_publish", side_effect=OSError("private-secret")), self.assertLogs("silver.backup") as logs:
            self.assertFalse(self.worker.tick())
        self.assertNotIn("private-secret", " ".join(logs.output))
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(len(self.files()), 1)
        self.now += 29
        self.assertFalse(self.worker.tick())
        self.now += 1
        self.assertTrue(self.worker.tick())
        self.assertEqual(len(self.files()), 2)

    def test_corrupt_source_and_failed_archive_verification_never_remove_good_backups(self):
        self.initial()
        old = self.files()[0].read_bytes()
        source = self.source.read_bytes()
        self.source.write_text("broken JSON")
        self.now += 31
        with self.assertLogs("silver.backup"):
            self.assertFalse(self.worker.tick())
        self.assertEqual(self.files()[0].read_bytes(), old)
        self.source.write_bytes(source)
        self.edit("Retry content")
        self.now += 31
        real_verify = backup.verify_archive
        def broken_verification(path):
            if str(path).endswith(".tmp"):
                raise ValueError("fixture verification failure")
            return real_verify(path)
        with patch.object(backup, "verify_archive", side_effect=broken_verification), self.assertLogs("silver.backup"):
            self.assertFalse(self.worker.tick())
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.files()[0].read_bytes(), old)

    def test_corrupt_new_archive_never_counts_toward_retention_or_delays_periodic_snapshot(self):
        self.initial()
        good = self.files()[0]
        self.now += 1800
        label = backup.datetime.fromtimestamp(self.now, backup.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        bad = self.worker.directory / ("workspace-" + label + "-scheduled-012345abcdef.tar.gz")
        bad.write_bytes(b"not a backup")
        restarted = backup.WorkspaceBackup(self.source, clock=lambda: self.now)
        with patch.dict(backup.RETENTION, {"scheduled": 2, "change": 40}):
            self.assertTrue(restarted.tick())
        self.assertTrue(good.exists())
        self.assertFalse(bad.exists())
        self.assertEqual(len(self.files()), 2)

    def test_housekeeping_status_and_retention_recover_without_duplicate_snapshot(self):
        self.initial()
        self.now += 31
        self.edit()
        with patch.object(self.worker, "_save_status", side_effect=OSError("status full")), self.assertLogs("silver.backup"):
            self.assertFalse(self.worker.tick())
        self.assertEqual(len(self.files()), 2)
        self.assertEqual(self.worker.status["state"], "failed")
        self.now += 31
        self.assertFalse(self.worker.tick())
        self.assertEqual(len(self.files()), 2)
        self.assertEqual(self.worker.status["state"], "ok")
        self.assertEqual(json.loads((self.worker.directory/"status.json").read_text())["state"], "ok")

    def test_concurrent_publishers_create_one_snapshot_for_the_same_changes(self):
        workers = [backup.WorkspaceBackup(self.source, clock=lambda: self.now) for _ in range(2)]
        barrier = threading.Barrier(2)
        results = []
        def run(worker):
            barrier.wait(2)
            results.append(worker.tick())
        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.files()), 1)
        backup.verify_archive(self.files()[0])

    def test_snapshot_is_consistent_when_writer_atomically_replaces_during_capture(self):
        before = self.source.read_bytes()
        original_validate = backup._validate_workspace
        replaced = False
        def concurrent_validate(state):
            nonlocal replaced
            if not replaced:
                replaced = True
                self.edit("Concurrent commit")
            return original_validate(state)
        with patch.object(backup, "_validate_workspace", side_effect=concurrent_validate):
            self.initial()
        self.assertEqual(self.read_source(self.files()[0]), before)
        self.assertNotEqual(self.source.read_bytes(), before)
        self.now += 31
        self.assertTrue(self.worker.tick())
        self.assertEqual(self.read_source(self.files()[-1]), self.source.read_bytes())

    def test_verify_archive_checks_full_state_invariants_not_only_hash(self):
        self.initial()
        malformed = json.loads(self.source.read_bytes())
        malformed["plans"][SYMBOL]["revision"] = 900
        raw = json.dumps(malformed).encode()
        semantic = backup._semantic_hash(malformed)
        with self.assertRaises(ValueError):
            self.worker._publish(raw, semantic, "change", self.now+31)
        self.assertEqual(len(self.files()), 1)

    def test_worker_start_stop_and_missing_source_never_create_or_modify_user_data(self):
        missing = self.root / "other-state" / "workspace.json"
        worker = backup.WorkspaceBackup(missing, self.root / "private-backups")
        worker.start()
        worker.stop()
        self.assertFalse(worker.thread.is_alive())
        self.assertFalse(missing.exists())
        self.assertFalse(list(worker.directory.glob("*.tar.gz")))

    def test_all_archives_and_source_corrupt_preserves_damaged_recovery_material(self):
        self.initial()
        archive = self.files()[0]
        archive.write_bytes(b"damaged recovery material")
        self.source.write_text("broken source")
        self.now += 31
        restarted = backup.WorkspaceBackup(self.source, clock=lambda: self.now)
        with self.assertLogs("silver.backup"):
            self.assertFalse(restarted.tick())
        self.assertEqual(archive.read_bytes(), b"damaged recovery material")

    def test_disappeared_existing_source_is_reported_without_deleting_backups(self):
        self.initial()
        self.source.unlink()
        self.now += 31
        with self.assertLogs("silver.backup"):
            self.assertFalse(self.worker.tick())
        self.assertEqual(self.worker.status["state"], "failed")
        self.assertEqual(self.worker.status["last_error"], "FileNotFoundError")
        self.assertEqual(len(self.files()), 1)


if __name__ == "__main__":
    unittest.main()
