"""Native three-minute OHLC identity audit, fake frames only; no SDK/network."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import app
import workspace_market
from workspace_market import WorkspaceMarketCache, timestamp
from workspace_history import WorkspaceHistoryArchive


class Column(list):
    def tolist(self):
        return list(self)


def frame(rows):
    result = {key: Column(row[key] for row in rows) for key in ("open", "high", "low", "close", "volume")}
    result["datetime"] = Column(round(timestamp(row["dt"]) * 1e9) for row in rows)
    result["open_oi"] = Column([10000] * len(rows))
    result["close_oi"] = Column([10005] * len(rows))
    return result


class NativeThreeMinuteAudit(unittest.TestCase):
    def setUp(self):
        self.symbol = "SHFE.ag2702"
        begin = datetime.fromisoformat("2026-11-18T21:00:00+08:00")
        self.rows = [{"dt": (begin + timedelta(minutes=3*i)).isoformat(), "open": 10920+i,
                      "high": 10965+i, "low": 10910+i, "close": 10940+i, "volume": 30+i}
                     for i in range(12)]
        self.rows[-2].update(high=10979, close=10955)
        self.rows[-1].update(high=10978, close=10958)
        self.now = timestamp(self.rows[-1]["dt"]) + 30
        retention = workspace_market.data_length_for_period
        window = patch.object(workspace_market, "data_length_for_period", side_effect=lambda period: 12 if period == 180 else retention(period))
        window.start()
        self.addCleanup(window.stop)
        self.cache = WorkspaceMarketCache(lambda: self.now)
        self.cache.request(self.symbol)
        self.source = frame(self.rows)
        api = SimpleNamespace(is_serial_ready=lambda serial: True, is_changing=lambda serial: True)
        self.reader = app._WorkspaceSerialReader(api, self.cache)
        self.reader.subscriptions.serials[(self.symbol, 180)] = self.source
        self.preset = {"symbol": self.symbol, "tick": 1, "tick_value": 15, "name": "沪银"}
        self.refresh()

    def refresh(self):
        self.reader.observe(True)
        self.reader.refresh(True)
        return self.cache.snapshot(self.symbol, self.preset, {"connected": True})

    def assert_source_matches(self, market):
        actual = market["periods"]["m3"]
        expected = app._serial_rows(self.source)
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            self.assertEqual(timestamp(a["dt"]), timestamp(b["dt"]))
            self.assertEqual([a[key] for key in ("open", "high", "low", "close")],
                             [b[key] for key in ("open", "high", "low", "close")])

    def test_same_bar_high_downward_correction_stays_on_its_own_timestamp(self):
        before = self.refresh()
        self.source["high"][-1] = 10976
        after = self.refresh()
        self.assert_source_matches(after)
        self.assertEqual(after["periods"]["m3"][-2]["high"], 10979)
        self.assertEqual(after["periods"]["m3"][-1]["high"], 10976)
        self.assertEqual(before["periods"]["m3"][-1]["high"], 10978, "Published snapshots stay immutable")

    def test_penultimate_close_and_high_revision_replaces_tail_without_changing_neighbors(self):
        before = self.refresh()
        self.source["close"][-2] += 3
        self.source["high"][-2] = 10977
        after = self.refresh()
        self.assert_source_matches(after)
        a, b = before["periods"]["m3"], after["periods"]["m3"]
        self.assertEqual(a[:-2], b[:-2])
        self.assertEqual(a[-1], b[-1])
        self.assertAlmostEqual(sum(row["close"] for row in b[-5:])/5 - sum(row["close"] for row in a[-5:])/5, .6)
        self.assertAlmostEqual(sum(row["close"] for row in b[-10:])/10 - sum(row["close"] for row in a[-10:])/10, .3)

    def test_roll_window_keeps_the_distinct_wick_values_with_their_source_times(self):
        original = self.refresh()["periods"]["m3"]
        new = deepcopy(self.rows[-1]);new["dt"] = (datetime.fromisoformat(new["dt"])+timedelta(minutes=3)).isoformat()
        new.update(high=10984, close=10960)
        self.now += 180
        addition = frame([new])
        for key, column in self.source.items():
            column.pop(0)
            column.extend(addition[key])
        after = self.refresh()
        self.assert_source_matches(after)
        self.assertEqual([row["high"] for row in after["periods"]["m3"][-3:]], [10979,10978,10984])
        self.assertEqual([row["dt"] for row in after["periods"]["m3"][-3:-1]], [row["dt"] for row in original[-2:]])

    def test_older_high_correction_changes_history_generation_and_archive_block(self):
        before = self.refresh()
        generation = self.cache.data[(self.symbol, 180)]["history_token"]
        # Fill otherwise irrelevant periods only for a complete archive fixture.
        before["periods"]["day"] = deepcopy(before["periods"]["m3"])
        before["periods"]["m30"] = deepcopy(before["periods"]["m3"])
        before["period_counts"] = {key:len(rows) for key,rows in before["periods"].items()}
        self.source["high"][-4] += 10
        after = self.refresh()
        self.assert_source_matches(after)
        self.assertNotEqual(generation, self.cache.data[(self.symbol, 180)]["history_token"])
        self.assertNotEqual(before["history_token"], after["history_token"])
        after["periods"]["day"] = deepcopy(before["periods"]["day"])
        after["periods"]["m30"] = deepcopy(before["periods"]["m30"])
        after["period_counts"] = deepcopy(before["period_counts"])
        with tempfile.TemporaryDirectory() as folder:
            archive = WorkspaceHistoryArchive(folder)
            first, second = archive.bootstrap(before), archive.bootstrap(after)
            self.assertNotEqual(first["history"]["m3"][0]["sha256"], second["history"]["m3"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
