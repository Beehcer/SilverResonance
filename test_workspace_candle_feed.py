"""Production-reader integration with one fake SDK and saved public OHLC only.

No TqApi, authentication, network, durable workspace, or user's plan is opened.
The fixture's 15m input is derived from complete 3m bins; it does not claim an
independent provider comparison at 15m.
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
from workspace_market import WorkspaceMarketCache, timestamp, trading_day


FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "pta_20261112_m3.json"
SCHEDULE = {"day": [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]],
            "night": [["21:00:00", "23:00:00"]]}


class Column(list):
    def tolist(self):
        return list(self)


def aggregate_source(children, start):
    return {"dt": start.isoformat(timespec="seconds"), "open": children[0]["open"],
            "high": max(row["high"] for row in children),
            "low": min(row["low"] for row in children), "close": children[-1]["close"],
            "volume": sum(row["volume"] for row in children),
            "oi": sum(row["oi"] for row in children)}


def clock_bins(source, seconds):
    groups = {}
    for row in source:
        stamp = datetime.fromisoformat(row["dt"])
        if seconds == 86400:
            day = trading_day(stamp.timestamp())
            start = stamp.replace(year=day.year, month=day.month, day=day.day, hour=0, minute=0, second=0)
        else:
            start = stamp.replace(minute=stamp.minute // (seconds // 60) * (seconds // 60), second=0)
        groups.setdefault(start, []).append(row)
    result = []
    for start, children in groups.items():
        if seconds == 900:
            assert len(children) == 5 and children[0]["dt"] == start.isoformat(), "15m fixture bin is incomplete"
        result.append(aggregate_source(children, start))
    return result


def frame(rows):
    values = {key: Column() for key in ("datetime", "open", "high", "low", "close", "volume", "open_oi", "close_oi")}
    for row in rows:
        values["datetime"].append(round(timestamp(row["dt"]) * 1_000_000_000))
        for key in ("open", "high", "low", "close", "volume"):
            values[key].append(row[key])
        values["open_oi"].append(100000)
        values["close_oi"].append(100000 + row["oi"])
    return values


class SingleFakeAPI:
    def __init__(self, owner, series):
        self.owner = owner
        self.frames = {period: frame(rows) for period, rows in series.items()}
        self.loop = asyncio.new_event_loop()
        owner.addCleanup(self.loop.close)
        self.calls = []
        self.changed = True
        self.unready = set()
        self.owner_thread = threading.get_ident()

    def create_task(self, coroutine):
        return self.loop.create_task(coroutine)

    def get_kline_serial(self, symbol, period, length):
        self.owner.assertIs(asyncio.get_running_loop(), self.loop)
        self.owner.assertEqual(threading.get_ident(), self.owner_thread)
        self.calls.append((symbol, period, length))
        return self.frames[period]

    def is_serial_ready(self, serial):
        return id(serial) not in self.unready

    def is_changing(self, serial, keys=None):
        return self.changed

    def append(self, period, row):
        addition = frame([row])
        for key, values in self.frames[period].items():
            values.extend(addition[key])


class CandleFeedTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.symbol = self.fixture["symbol"]
        self.now = timestamp(self.fixture["as_of"])
        self.preset = {"symbol": self.symbol, "tick": 2, "tick_value": 10, "name": "PTA"}
        self.references = {}
        self.reference_calls = []
        self.cache = WorkspaceMarketCache(lambda: self.now, reference_provider=self.reference)
        self.cache.request(self.symbol)
        self.quote = SimpleNamespace(trading_time=deepcopy(SCHEDULE), last_price=5994,
                                     datetime="2026-11-12 11:29:59")
        source = deepcopy(self.fixture["bars"])
        self.series = {180: source, 900: clock_bins(source, 900),
                       1800: clock_bins(source, 1800), 86400: clock_bins(source, 86400)}

    def reference(self, symbol, proposed):
        self.reference_calls.append(symbol)
        if symbol not in self.references:
            self.references[symbol] = deepcopy(proposed)
        return deepcopy(self.references[symbol])

    def reader(self, series=None, ready=True):
        api = SingleFakeAPI(self, series or self.series)
        if not ready:
            api.unready.add(id(api.frames[900]))
        reader = app._WorkspaceSerialReader(api, self.cache, quotes={self.symbol: self.quote}, trading_minutes=True)
        reader.schedule_requested()
        reader.schedule_requested()
        self.assertEqual(api.calls, [])
        api.loop.run_until_complete(asyncio.sleep(0))
        reader.schedule_requested()
        reader.refresh(True)
        return api, reader

    def snapshot(self, connected=True, **kwargs):
        return self.cache.snapshot(self.symbol, self.preset, {"connected": connected}, **kwargs)

    def test_one_api_four_subscriptions_once_and_saved_pta_shape(self):
        api, reader = self.reader()
        reader.schedule_requested()
        reader.subscribe_requested()
        reader.refresh(True)
        self.assertEqual(api.calls, [(self.symbol, 86400, 180), (self.symbol, 1800, 6000),
                                     (self.symbol, 180, 10000), (self.symbol, 900, 10000)])
        market = self.snapshot()
        candles = market["periods"]["m30"]
        self.assertEqual([candles[-1][key] for key in ("open", "high", "low", "close")], [5996, 6010, 5990, 5994])
        self.assertEqual(sum(row["close"] for row in candles[-5:]) / 5, 5990.8)
        self.assertEqual(candles[-1]["dt"], "2026-11-12T11:15:00+08:00")
        self.assertEqual(candles[-1]["end_time"], "2026-11-12T13:45:00+08:00")
        self.assertFalse(candles[-1]["closed"])
        self.assertEqual(market["candle_basis"], {"m30": "trading_minutes_v1"})
        self.assertEqual(market["anchor_reference"]["m30"]["source_times"][-1], round(timestamp("2026-11-12T11:00:00+08:00") * 1000))
        self.assertEqual(self.reference_calls, [self.symbol])
        self.assertEqual(market["history_coverage"]["m30"]["source_period_seconds"], 900)
        self.assertEqual(market["feed_status"], "idle")

    def test_900_tail_update_changes_candle_without_rebuilding_older_days(self):
        api, reader = self.reader()
        before = self.snapshot()
        aggregator = reader.aggregators[self.symbol]
        api.frames[900]["close"][-1] = 5992
        api.frames[180]["close"][-1] = 5992
        self.now += 2
        reader.observe(True)
        with patch.object(aggregator, "replace", wraps=aggregator.replace) as replace, \
                patch.object(aggregator, "update_tail", wraps=aggregator.update_tail) as update:
            reader.refresh(True)
        update.assert_called_once()
        replace.assert_not_called()
        after = self.snapshot(history_token=before["history_token"])
        self.assertTrue(after["incremental"])
        self.assertEqual(after["anchor_reference_id"], before["anchor_reference_id"])
        self.assertEqual(after["periods"]["m30"][-1]["close"], 5992)
        self.assertEqual(aggregator.last_rebuilt_days, ("2026-11-12",))

    def test_lunch_append_finishes_same_group_then_next_group_begins(self):
        api, reader = self.reader()
        before = self.snapshot()
        afternoon = {"dt": "2026-11-12T13:30:00+08:00", "open": 5994, "high": 6002,
                     "low": 5990, "close": 6000, "volume": 123, "oi": 7}
        api.append(900, afternoon)
        api.append(180, afternoon)
        self.now = timestamp("2026-11-12T13:32:00+08:00")
        reader.observe(True)
        reader.refresh(True)
        continued = self.snapshot()
        self.assertEqual(continued["period_counts"]["m30"], before["period_counts"]["m30"])
        self.assertEqual(continued["periods"]["m30"][-1]["dt"], "2026-11-12T11:15:00+08:00")
        self.assertEqual(continued["periods"]["m30"][-1]["close"], 6000)
        self.assertTrue(continued["periods"]["m30"][-1]["complete"])
        self.assertFalse(continued["periods"]["m30"][-1]["closed"])
        following = {**afternoon, "dt": "2026-11-12T13:45:00+08:00", "open": 6000, "close": 6002}
        api.append(900, following)
        api.append(180, following)
        self.now = timestamp("2026-11-12T13:46:00+08:00")
        reader.observe(True)
        reader.refresh(True)
        after = self.snapshot()
        self.assertEqual(after["period_counts"]["m30"], before["period_counts"]["m30"] + 1)
        self.assertEqual(after["periods"]["m30"][-1]["dt"], "2026-11-12T13:45:00+08:00")
        self.assertTrue(after["periods"]["m30"][-2]["closed"])
        self.assertEqual(after["anchor_reference_id"], before["anchor_reference_id"])

    def test_failed_900_source_cannot_renew_m30_freshness(self):
        api, reader = self.reader()
        before = self.snapshot()
        self.now += 2
        api.frames[900]["low"][-1] = 7000
        reader.observe(True)
        reader.refresh(True)
        failed = self.snapshot()
        self.assertEqual(failed["periods"]["m30"], before["periods"]["m30"])
        self.assertEqual(failed["freshness"]["m30"]["checked_at"], before["freshness"]["m30"]["checked_at"])
        self.assertFalse(failed["freshness"]["m30"]["checked_recent"])
        self.assertFalse(failed["fresh"])
        self.assertEqual(failed["feed_status"], "stale")
        api.frames[900]["low"][-1] = 5990
        reader.refresh(True)
        self.assertTrue(self.snapshot()["freshness"]["m30"]["checked_recent"])

    def test_not_ready_900_never_exposes_native_as_new_basis(self):
        api, reader = self.reader(ready=False)
        waiting = self.snapshot()
        self.assertEqual(waiting["periods"]["m30"], [])
        self.assertNotIn("candle_basis", waiting)
        self.assertFalse(waiting["fresh"])
        self.assertEqual(waiting["feed_status"], "loading")
        api.unready.clear()
        reader.refresh(True)
        self.assertEqual(self.snapshot()["candle_basis"], {"m30": "trading_minutes_v1"})
        self.assertEqual(len(api.calls), 4)

    def test_czce_decade_gap_removed_from_prices_but_retained_for_anchors(self):
        old = {"dt": "2017-01-04T09:00:00+08:00", "open": 4500, "high": 4504,
               "low": 4496, "close": 4502, "volume": 12, "oi": 1}
        series = deepcopy(self.series)
        series[900] = [old, {**old, "dt": "2017-01-04T09:15:00+08:00"}] + series[900]
        series[1800] = [old] + series[1800]
        api, reader = self.reader(series)
        result = self.snapshot()
        self.assertTrue(all(row["dt"].startswith("2026-") for row in result["periods"]["m30"]))
        self.assertEqual(result["anchor_reference"]["m30"]["source_times"][0], round(timestamp(old["dt"]) * 1000))
        coverage = result["history_coverage"]["m30"]
        self.assertEqual(coverage["reason"], "reused_contract_code_decade_gap")
        self.assertEqual(coverage["excluded_before"], self.series[900][0]["dt"])
        self.assertEqual(coverage["available_source_bars"], len(self.series[900]))
        self.assertEqual([row["close"] for row in result["periods"]["m30"][-5:]], [5978, 5986, 6000, 5996, 5994])

    def test_disconnect_keeps_prior_candles_and_check_time(self):
        api, reader = self.reader()
        before = self.snapshot()
        self.now += 30
        reader.refresh(False)
        after = self.snapshot(connected=False)
        self.assertEqual(after["periods"]["m30"], before["periods"]["m30"])
        self.assertEqual(after["freshness"]["m30"]["checked_at"], before["freshness"]["m30"]["checked_at"])
        self.assertFalse(after["fresh"])
        self.assertEqual(after["feed_status"], "disconnected")

    def test_latest_aggregate_with_missing_source_slot_is_not_fresh(self):
        series = deepcopy(self.series)
        series[900] = series[900][:-1]  # Missing the 11:15 source slot.
        afternoon = {"dt": "2026-11-12T13:30:00+08:00", "open": 5994,
                     "high": 6002, "low": 5990, "close": 6000, "volume": 123, "oi": 7}
        series[900].append(afternoon)
        series[180].append(afternoon)
        series[1800].append(afternoon)
        self.now = timestamp("2026-11-12T13:31:00+08:00")
        self.reader(series)
        market = self.snapshot()
        last = market["periods"]["m30"][-1]
        self.assertEqual(last["dt"], "2026-11-12T11:15:00+08:00")
        self.assertEqual(last["quality"], "incomplete")
        self.assertEqual(last["missing_slots"], 1)
        self.assertFalse(market["fresh"])


if __name__ == "__main__":
    unittest.main()
