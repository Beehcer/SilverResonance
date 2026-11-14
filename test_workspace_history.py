"""History chunks never substitute cached prices for verified live evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from workspace_api import register_workspace
from workspace_history import WorkspaceHistoryArchive, six_month_cutoff
from workspace_market import PERIODS, WorkspaceMarketCache, iso, timestamp
from workspace_store import WorkspaceStore


class HistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = timestamp("2026-11-15T10:00:00+08:00")
        self.root = Path(self.directory.name) / "history"
        self.archive = WorkspaceHistoryArchive(self.root, clock=lambda: self.now)
        self.symbol = "CZCE.MA701"
        self.preset = {"tick": 1, "tick_value": 10, "name": "甲醇"}
        self.cache = WorkspaceMarketCache(clock=lambda: self.now)
        self.rows = {}
        for period in PERIODS.values():
            step = max(period, 86400)
            rows = [{"dt": iso(self.now - (30 - index) * step), "open": 100,
                     "high": 104, "low": 98, "close": 101, "volume": index, "oi": 1}
                    for index in range(31)]
            self.rows[period] = rows
            self.cache.cache(self.symbol, period, rows)

    def full(self):
        return self.cache.snapshot(self.symbol, self.preset, {"connected": True})

    def reconstruct(self, value):
        market = copy.deepcopy(value["market"])
        for period in PERIODS:
            rows = []
            for item in value["history"][period]:
                content = self.archive.read_block(item["sha256"])
                self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
                decoded = json.loads(content)
                self.assertEqual(decoded["symbol"], self.symbol)
                self.assertEqual(decoded["period"], period)
                self.assertEqual(item["count"], len(decoded["bars"]))
                rows.extend(decoded["bars"])
            self.assertEqual(value["history_counts"][period], len(rows))
            market["periods"][period] = rows + market["periods"][period]
        return market

    def test_exact_reassembly_and_first_increment_use_same_snapshot(self):
        full = self.full()
        result = self.archive.bootstrap(full)
        self.assertEqual(self.reconstruct(result), full)
        self.assertTrue(all(len(rows) == 2 for rows in result["market"]["periods"].values()))
        tail = copy.deepcopy(self.rows[180][-2:])
        tail[-1]["close"] = 102
        self.cache.update_tail(self.symbol, 180, tail)
        delta = self.cache.snapshot(self.symbol, self.preset, {"connected": True},
                                    history_token=result["market"]["history_token"])
        self.assertTrue(delta["incremental"])
        merged = self.reconstruct(result)["periods"]["m3"][:-2] + delta["periods"]["m3"]
        self.assertEqual(merged, self.full()["periods"]["m3"])

    def test_content_identity_survives_process_restart_and_read_clocks(self):
        before = self.archive.bootstrap(self.full())
        self.cache = WorkspaceMarketCache(clock=lambda: self.now)
        self.now += 5
        for period, rows in self.rows.items():
            self.cache.cache(self.symbol, period, rows)
        restarted = WorkspaceHistoryArchive(self.root, clock=lambda: self.now)
        after = restarted.bootstrap(self.full())
        self.assertNotEqual(before["market"]["history_token"], after["market"]["history_token"])
        self.assertEqual(before["snapshot_id"], after["snapshot_id"])
        self.assertEqual(before["history"], after["history"])

    def test_tail_changes_reuse_every_history_block(self):
        before = self.archive.bootstrap(self.full())
        tail = copy.deepcopy(self.rows[180][-2:])
        tail[-2]["volume"] += 3
        tail[-1]["close"] = 102
        self.cache.update_tail(self.symbol, 180, tail)
        after = self.archive.bootstrap(self.full())
        self.assertEqual(before["history"], after["history"])
        self.assertNotEqual(before["snapshot_id"], after["snapshot_id"])

    def test_historical_correction_replaces_only_its_period_month(self):
        before = self.archive.bootstrap(self.full())
        corrected = copy.deepcopy(self.rows[180])
        corrected[7]["high"] = 105
        self.cache.cache(self.symbol, 180, corrected)
        after = self.archive.bootstrap(self.full())
        self.assertEqual(before["history"]["day"], after["history"]["day"])
        self.assertEqual(before["history"]["m30"], after["history"]["m30"])
        changes = sum(a["sha256"] != b["sha256"] for a, b in
                      zip(before["history"]["m3"], after["history"]["m3"]))
        self.assertEqual(changes, 1)
        self.assertEqual(self.reconstruct(after), self.full())
        # The old manifest remains readable while an in-flight client finishes.
        self.reconstruct(before)

    def test_coordinate_reference_and_contract_lifecycle_are_part_of_identity(self):
        full = self.full()
        before = self.archive.bootstrap(full)
        other = copy.deepcopy(full)
        other["candle_basis"] = {"m30": "trading_minutes_v1"}
        other["anchor_reference_id"] = "reference-1"
        other["anchor_reference"] = {"m30": {"source_times": [1, 2, 3], "target_base_time": 3}}
        after = self.archive.bootstrap(other)
        self.assertNotEqual(before["snapshot_id"], after["snapshot_id"])
        self.assertEqual(self.reconstruct(after), other)
        prior_cycle = copy.deepcopy(full)
        for rows in prior_cycle["periods"].values():
            for row in rows:
                row["dt"] = row["dt"].replace("2026-", "2016-")
        earlier = self.archive.bootstrap(prior_cycle)
        self.assertNotEqual(before["snapshot_id"], earlier["snapshot_id"])

    def test_persisted_preview_has_no_cursor_or_freshness_permission(self):
        full = self.full()
        self.archive.bootstrap(full)
        restarted = WorkspaceHistoryArchive(self.root, clock=lambda: self.now)
        preview = restarted.preview(self.symbol)
        self.assertEqual(preview["periods"], full["periods"])
        self.assertFalse(preview["fresh"])
        self.assertFalse(preview["connected"])
        self.assertIsNone(preview["history_token"])
        self.assertTrue(preview["partial"])
        self.assertEqual(preview["feed_status"], "preview")
        self.assertIsNone(restarted.preview(self.symbol, source="mock"))
        self.assertIsNone(restarted.preview(self.symbol, expected_reference_id="different"))

    def test_incomplete_cold_snapshot_does_not_overwrite_complete_manifest(self):
        self.archive.bootstrap(self.full())
        empty = WorkspaceMarketCache(clock=lambda: self.now).snapshot(self.symbol, self.preset, {"connected": True})
        self.archive.bootstrap(empty)
        self.assertTrue(self.archive.preview(self.symbol)["periods"]["m3"])

    def test_publication_is_after_atomic_local_commit_and_failure_is_optional(self):
        seen = []
        def enqueue(items):
            self.assertTrue(self.archive.preview(self.symbol))
            self.assertTrue(all(Path(item["path"]).is_file() for item in items))
            seen.extend(items)
            raise ConnectionError("offline")
        self.archive.publication_hook = enqueue
        self.archive.public_url_provider = lambda digest: "https://example.test/history/v1/blocks/" + digest + ".json"
        result = self.archive.bootstrap(self.full())
        self.assertTrue(seen)
        self.assertEqual(self.archive.last_publication_error, "ConnectionError")
        self.assertTrue(all("oss_url" in item for items in result["history"].values() for item in items))
        self.assertEqual(self.reconstruct(result), self.full())

    def test_write_failure_preserves_previous_manifest_and_corruption_is_repaired(self):
        initial = self.archive.bootstrap(self.full())
        manifest = next((self.root / "snapshots").glob("*.json"))
        original = manifest.read_bytes()
        with patch.object(self.archive, "_write_block", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.archive.bootstrap(self.full())
        self.assertEqual(manifest.read_bytes(), original)
        item = initial["history"]["m3"][0]
        path = self.root / "blocks" / (item["sha256"] + ".json")
        path.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.archive.read_block(item["sha256"])
        self.archive.bootstrap(self.full())
        self.archive.read_block(item["sha256"])

    def test_gc_is_bounded_by_archive_and_grace_and_six_calendar_months(self):
        result = self.archive.bootstrap(self.full())
        outside = self.root.parent / "keep.json"
        outside.write_text("private plan", encoding="utf-8")
        self.assertEqual(self.archive.collect_garbage(grace_seconds=0), 0)
        self.now += 220 * 86400
        for path in (self.root / "blocks").glob("*.json"):
            os.utime(path, (self.now, self.now))
        self.assertEqual(self.archive.collect_garbage(), 0)
        self.now += 86401
        removed = self.archive.collect_garbage()
        self.assertEqual(removed, sum(len(items) for items in result["history"].values()))
        self.assertEqual(outside.read_text(encoding="utf-8"), "private plan")
        self.assertIsNone(self.archive.preview(self.symbol))
        with self.assertRaises(ValueError):
            self.archive.read_block("../keep")

    def test_retired_versions_get_full_grace_and_idle_live_versions_stay(self):
        before = self.archive.bootstrap(self.full())
        old_hashes = {item["sha256"] for items in before["history"].values() for item in items}
        for digest in old_hashes:
            path = self.root / "blocks" / (digest + ".json")
            os.utime(path, (self.now - 10 * 86400, self.now - 10 * 86400))
        revised = copy.deepcopy(self.rows[180])
        revised[7]["high"] = 105
        self.cache.cache(self.symbol, 180, revised)
        after = self.archive.bootstrap(self.full())
        active = {item["sha256"] for items in after["history"].values() for item in items}
        retired = old_hashes - active
        self.assertEqual(len(retired), 1)
        self.assertEqual(self.archive.live_hashes(), active)
        self.assertEqual(self.archive.collect_garbage(), 0)
        self.now += 86401
        self.assertEqual(self.archive.collect_garbage(), 1)
        self.assertEqual(self.archive.live_hashes(), active)
        for digest in active:
            self.archive.read_block(digest)

    def test_cold_block_request_count_is_bounded_and_bad_manifest_stops_gc(self):
        now = self.now
        cutoff = six_month_cutoff(now)
        rows = [{"dt": iso(cutoff + index * 86400), "open": 100, "high": 104, "low": 98, "close": 101}
                for index in range(int((now - cutoff) / 86400) + 1)]
        for period in PERIODS.values():
            self.cache.cache(self.symbol, period, rows)
        result = self.archive.bootstrap(self.full())
        self.assertLessEqual(sum(len(items) for items in result["history"].values()), 15)
        self.assertEqual(len(result["history"]["day"]), 1)
        manifest = next((self.root / "snapshots").glob("*.json"))
        manifest.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.archive.collect_garbage(grace_seconds=0)
        repaired = self.archive.bootstrap(self.full())
        self.assertEqual(self.reconstruct(repaired), self.full())
        self.assertTrue(self.archive.preview(self.symbol))


class CalendarRetentionTests(unittest.TestCase):
    def test_calendar_month_end_and_leap_year(self):
        self.assertEqual(iso(six_month_cutoff(timestamp("2026-08-31T10:15:00+08:00"))), "2026-02-28T10:15:00+08:00")
        self.assertEqual(iso(six_month_cutoff(timestamp("2024-08-31T10:15:00+08:00"))), "2024-02-29T10:15:00+08:00")

    def test_visible_history_trimmed_without_trimming_source_or_anchor(self):
        now = timestamp("2026-11-15T10:00:00+08:00")
        cache = WorkspaceMarketCache(clock=lambda: now)
        cutoff = six_month_cutoff(now)
        rows = [{"dt": iso(stamp), "open": 100, "high": 104, "low": 98, "close": 101}
                for stamp in (cutoff - 1, cutoff, now - 180, now)]
        for period in PERIODS.values():
            cache.cache("EX.test", period, rows)
        reference = {"basis": "native_v1", "target_basis": "trading_minutes_v1",
                     "source_times": [int((cutoff - 86400) * 1000), int(now * 1000)],
                     "target_base_time": int(now * 1000)}
        cache.references["EX.test"] = reference
        cache.reference_ids["EX.test"] = "fixed-reference"
        full = cache.snapshot("EX.test", {}, {"connected": False})
        self.assertTrue(all(len(group) == 3 for group in full["periods"].values()))
        self.assertEqual(len(cache.data[("EX.test", 180)]["bars"]), 4)
        self.assertEqual(full["anchor_reference"]["m30"], reference)
        delta = cache.snapshot("EX.test", {}, {"connected": False}, history_token=full["history_token"])
        self.assertTrue(delta["incremental"])
        self.assertEqual(delta["period_counts"], {period: 3 for period in PERIODS})


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = WorkspaceStore(Path(self.directory.name) / "workspace.json")
        self.app = Flask(__name__)
        self.app.testing = True
        self.content = b'{"schema":1}'
        self.digest = hashlib.sha256(self.content).hexdigest()
        register_workspace(self.app, self.store, lambda symbol: {},
                           lambda: [{"symbol": "EX.test"}], lambda: {}, lambda *args: {},
                           market_bootstrap_provider=lambda symbol: {"schema": 1, "symbol": symbol},
                           market_history_provider=lambda digest: self.content)
        self.client = self.app.test_client()

    def test_routes_require_session_and_chunks_have_content_etag(self):
        self.assertEqual(self.client.get("/api/workspace/market/bootstrap?symbol=EX.test").status_code, 401)
        self.assertEqual(self.client.get("/api/workspace/market/history/" + self.digest + ".json").status_code, 401)
        self.client.get("/api/workspace/session")
        response = self.client.get("/api/workspace/market/bootstrap?symbol=EX.test")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        path = "/api/workspace/market/history/" + self.digest + ".json"
        response = self.client.get(path)
        self.assertEqual(response.data, self.content)
        self.assertEqual(response.headers["Cache-Control"], "private, max-age=31536000, immutable")
        self.assertEqual(response.headers["ETag"], '"' + self.digest + '"')
        self.assertEqual(self.client.get(path, headers={"If-None-Match": response.headers["ETag"]}).status_code, 304)
        self.assertEqual(self.client.get("/api/workspace/market/history/not-a-hash.json").status_code, 404)

    def test_restart_and_correction_offer_light_bootstrap_only_to_new_clients(self):
        now = timestamp("2026-11-15T10:00:00+08:00")
        cache = WorkspaceMarketCache(clock=lambda: now)
        rows = [{"dt": iso(now - (8 - index) * 180), "open": 100, "high": 104,
                 "low": 98, "close": 101} for index in range(9)]
        for period in PERIODS.values():
            cache.cache("EX.test", period, rows)
        archive = WorkspaceHistoryArchive(Path(self.directory.name) / "blocks", clock=lambda: now)
        def market(symbol, history=None):
            return cache.snapshot(symbol, {}, {"connected": True}, history_token=history)
        app = Flask("history-recovery")
        app.testing = True
        register_workspace(app, self.store, market, lambda: [{"symbol": "EX.test"}],
                           lambda: {}, lambda *args: {}, market_incremental_provider=market,
                           market_bootstrap_provider=lambda symbol: archive.bootstrap(market(symbol)))
        client = app.test_client()
        client.get("/api/workspace/session")
        initial = client.get("/api/workspace/market/bootstrap?symbol=EX.test").get_json()["bootstrap"]
        token = initial["market"]["history_token"]
        path = "/api/workspace/market?symbol=EX.test&history=" + token
        self.assertTrue(client.get(path + "&history_fallback=bootstrap").get_json()["market"]["incremental"])
        cache = WorkspaceMarketCache(clock=lambda: now)
        for period in PERIODS.values():
            cache.cache("EX.test", period, rows)
        self.assertEqual(client.get(path + "&history_fallback=bootstrap").get_json(), {"status": "bootstrap_required"})
        self.assertFalse(client.get(path).get_json()["market"]["incremental"])
        restarted = client.get("/api/workspace/market/bootstrap?symbol=EX.test").get_json()["bootstrap"]
        self.assertEqual(initial["history"], restarted["history"])
        self.assertEqual(initial["snapshot_id"], restarted["snapshot_id"])
        revised = copy.deepcopy(rows)
        revised[1]["high"] = 105
        cache.cache("EX.test", 180, revised)
        path = "/api/workspace/market?symbol=EX.test&history=" + restarted["market"]["history_token"]
        self.assertEqual(client.get(path + "&history_fallback=bootstrap").get_json(), {"status": "bootstrap_required"})
        self.assertFalse(client.get(path).get_json()["market"]["incremental"])
        corrected = client.get("/api/workspace/market/bootstrap?symbol=EX.test").get_json()["bootstrap"]
        self.assertEqual(restarted["history"]["day"], corrected["history"]["day"])
        self.assertEqual(restarted["history"]["m30"], corrected["history"]["m30"])
        self.assertNotEqual(restarted["history"]["m3"], corrected["history"]["m3"])


if __name__ == "__main__":
    unittest.main()
