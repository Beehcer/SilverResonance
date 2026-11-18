"""Source-revision integration tests. No SDK connection or durable user data.

The revisions here are injected test events, not evidence that the production
provider sent these specific corrections on 2026-11-18.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import app
from workspace_market import WorkspaceMarketCache, timestamp


SYMBOL = "SHFE.ag2702"


def frame():
    begin = datetime.fromisoformat("2026-11-18T21:15:00+08:00")
    times = [int((begin + timedelta(minutes=3*i)).timestamp() * 1e9) for i in range(12)]
    values = {"datetime": times, "open": [10959.0]*12, "high": [10979.0]*12,
              "low": [10952.0]*12, "close": [10971.0]*12, "volume": [6638]*12,
              "open_oi": [312000]*12, "close_oi": [312467]*12}
    return {key: np.asarray(value) for key, value in values.items()}


class ReceivedSource:
    """Model an SDK source tree whose older values differ from its DataFrame."""
    def __init__(self, columns):
        self.columns = deepcopy(columns)
        self.pending = set()
        self.error = False

    def observe(self):
        pending, self.pending = self.pending, set()
        return pending

    def snapshot(self, symbol, period, serial):
        if self.error:
            raise ValueError("test invalid source identity")
        return deepcopy(self.columns)

    def diagnostics(self, symbol, period, limit=3):
        return {"symbol": symbol, "period": period, "origin": "offline_test"}


class SourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.now = timestamp("2026-11-18T21:50:00+08:00")
        self.frame = frame()
        self.source = ReceivedSource(self.frame)
        self.cache = WorkspaceMarketCache(lambda: self.now)
        self.cache.request(SYMBOL)
        self.api = SimpleNamespace(is_serial_ready=lambda serial: True, is_changing=lambda serial: False)
        self.reader = app._WorkspaceSerialReader(self.api, self.cache, sdk_source=self.source)
        self.reader.subscriptions.serials[(SYMBOL, 180)] = self.frame
        self.reader.refresh(True)

    def entry(self):
        return self.cache.data[(SYMBOL, 180)]

    def test_old_source_correction_uses_verified_snapshot_for_full_reload(self):
        before = self.entry()
        self.source.columns["high"][-4] = 10977
        self.source.columns["close"][-4] = 10970
        self.source.pending.add((SYMBOL, 180))
        self.assertTrue(self.reader.observe(True), "raw changes do not rely on DataFrame is_changing")
        self.reader.refresh(True)
        after = self.entry()
        self.assertEqual(after["bars"][-4]["high"], 10977)
        self.assertEqual(after["bars"][-4]["close"], 10970)
        self.assertNotEqual(before["history_token"], after["history_token"])
        self.assertEqual(self.frame["high"][-4], 10979, "SDK DataFrame must not be mutated")
        self.assertEqual(before["bars"][-4]["high"], 10979, "prior published snapshot stays immutable")

    def test_revisions_survive_multiple_pumps_before_throttled_refresh(self):
        self.source.columns["high"][-4] = 10977
        self.source.pending.add((SYMBOL, 180))
        self.reader.observe(True)
        self.reader.observe(False)
        self.source.columns["close"][-3] = 10966
        self.source.pending.add((SYMBOL, 180))
        self.reader.observe(True)
        self.reader.observe(True)
        self.reader.refresh(True)
        self.assertEqual(self.entry()["bars"][-4]["high"], 10977)
        self.assertEqual(self.entry()["bars"][-3]["close"], 10966)
        self.assertNotIn((SYMBOL, 180), self.reader.dirty)

    def test_tail_revision_uses_same_verified_source_as_full_load(self):
        token = self.entry()["history_token"]
        self.source.columns["close"][-1] = 10966
        self.source.pending.add((SYMBOL, 180))
        self.reader.observe(True)
        self.reader.refresh(True)
        self.assertEqual(self.entry()["bars"][-1]["close"], 10966)
        self.assertEqual(self.entry()["history_token"], token)

    def test_invalid_source_retains_old_prices_without_freshness_then_recovers(self):
        before = self.entry()
        self.source.error = True
        self.source.pending.add((SYMBOL, 180))
        self.reader.observe(True)
        self.reader.refresh(True)
        self.assertEqual(self.entry()["bars"], before["bars"])
        self.assertTrue(self.entry()["error"])
        self.assertIn((SYMBOL, 180), self.reader.dirty)
        self.assertEqual(self.reader.source_reports[(SYMBOL, 180)]["error"], "ValueError")
        self.source.error = False
        self.source.columns["close"][-4] = 10970
        self.reader.refresh(True)
        self.assertIsNone(self.entry()["error"])
        self.assertIsNone(self.reader.source_reports[(SYMBOL, 180)]["error"])
        self.assertEqual(self.entry()["bars"][-4]["close"], 10970)

    def test_diagnostic_failure_does_not_stale_prices_or_block_warmup(self):
        key = (SYMBOL, 180)
        ready = Mock()
        warmup = SimpleNamespace(symbols=lambda: [SYMBOL], observe=Mock(return_value=True))
        self.reader.warmup, self.reader.on_ready = warmup, ready
        self.source.columns["close"][-1] = 10966
        self.source.pending.add(key)
        self.reader.observe(True)
        with patch.object(self.source, "diagnostics", side_effect=RuntimeError("test diagnostics failure")):
            self.reader.refresh(True)
        self.assertEqual(self.entry()["bars"][-1]["close"], 10966)
        self.assertIsNone(self.entry()["error"])
        self.assertNotIn(key, self.reader.serial_errors)
        self.assertNotIn(key, self.reader.dirty)
        self.assertIsNone(warmup.observe.call_args.kwargs["error"])
        ready.assert_called_once_with(SYMBOL)
        report = self.reader.source_reports[key]
        self.assertIsNone(report["error"])
        self.assertEqual(report["diagnostic_error"], "RuntimeError")
        self.assertNotIn("published_tail", report, "Failed diagnostics must not re-label older samples as current")

    def test_malformed_diagnostic_sample_is_isolated_and_next_report_recovers(self):
        key = (SYMBOL, 180)
        self.source.columns["close"][-1] = 10966
        self.reader.dirty.add(key)
        with patch.object(self.source, "diagnostics", return_value={"rows": [{"snapshot": {"datetime": None}}]}):
            self.reader.refresh(True)
        self.assertEqual(self.entry()["bars"][-1]["close"], 10966)
        self.assertIsNone(self.entry()["error"])
        self.assertEqual(self.reader.source_reports[key]["diagnostic_error"], "TypeError")
        self.reader.dirty.add(key)
        self.reader.refresh(True)
        self.assertNotIn("diagnostic_error", self.reader.source_reports[key])
        self.assertEqual(self.reader.source_reports[key]["published_tail"][-1]["close"], 10966)
        self.assertIsNone(self.entry()["error"])

    def test_read_only_status_does_not_read_sdk_or_create_subscriptions(self):
        self.source.snapshot = lambda *args: self.fail("HTTP must not read live SDK")
        self.source.observe = lambda *args: self.fail("HTTP must not pump live SDK")
        before = deepcopy(self.reader.source_reports)
        with patch.object(app, "_workspace_reader", self.reader), \
                patch.object(app, "_workspace_warmup", None), \
                patch.object(app, "_workspace_archive_warmer", None):
            status = app._workspace_cache_status_provider()["source_integrity"]
        self.assertTrue(status["enabled"])
        self.assertEqual(status["periods"], list(before.values()))
        self.assertEqual(len(status["periods"][0]["published_tail"]), 3)

    def test_actual_adapter_reconciles_old_sdk_rows_through_cache_and_diagnostic(self):
        from test_workspace_sdk_klines import Source
        source = Source(width=6)
        source.api.is_serial_ready = lambda frame: True
        source.api.is_changing = lambda frame: False
        reader = app._WorkspaceSerialReader(source.api, self.cache, sdk_source=source.adapter)
        reader.subscriptions.serials[(SYMBOL, 180)] = source.frame
        reader.refresh(True)
        generation = self.entry()["history_token"]
        # Provider's received tree changes, public DataFrame remains stale.
        source.update(0, high=10977, close=10970)
        reader.observe(True)
        reader.observe(False)
        reader.refresh(True)
        self.assertEqual(self.entry()["bars"][0]["high"], 10977)
        self.assertNotEqual(generation, self.entry()["history_token"])
        report = reader.source_reports[(SYMBOL, 180)]
        sample = next(row for row in report["rows"] if row["id"] == 0)
        self.assertEqual(sample["frame"]["high"], 10979)
        self.assertEqual(sample["raw"]["high"], 10977)
        self.assertEqual(sample["snapshot"]["high"], 10977)
        self.assertEqual(sample["published"]["high"], 10977)
        self.assertTrue(sample["published_matches_source"])


if __name__ == "__main__":
    unittest.main()
