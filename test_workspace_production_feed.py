"""Production-only SDK routing; fake provider, no login or durable user writes."""
import copy
import math
import sys
import types
import unittest
from unittest.mock import Mock, patch

import app
from workspace_market import WorkspaceMarketCache, timestamp


class Finished(BaseException):
    pass


class Column(list):
    def tolist(self):
        return list(self)


class FakeAPI:
    def __init__(self, owner, steps):
        self.owner, self.steps = owner, list(steps)
        self.calls, self.quote_calls, self.frames = [], [], {}
        self.closed = False
        self.quotes = {p["symbol"]: types.SimpleNamespace(last_price=100.0,
                       datetime="2026-11-11 10:35:00", expired=False) for p in owner.presets}
        self.changed = False
        self.notifications = []
        self.observed = []

    def get_quote_list(self, codes):
        self.quote_calls.append(list(codes))
        return [self.quotes[code] for code in codes]

    def get_quote(self, code):
        return self.quotes[code]

    def get_kline_serial(self, code, period, length):
        self.calls.append((code, period, length))
        end = int(self.owner.now // period) * period
        frame = {key: Column([value] * length) for key, value in
                 (("open", 100.0), ("high", 104.0), ("low", 98.0), ("close", 101.0),
                  ("volume", 100), ("open_oi", 1000), ("close_oi", 1010))}
        frame["datetime"] = Column([(end - (length-1-index)*period) * 1000000000 for index in range(length)])
        self.frames[(code, period)] = frame
        return frame

    def is_changing(self, item, keys=None):
        return self.changed

    def wait_update(self, **kwargs):
        self.observed.append(app.get_state())
        if not self.steps:
            raise Finished()
        step = self.steps.pop(0)
        self.owner.monotonic = step.get("at", self.owner.monotonic)
        self.owner.now = self.owner.started + self.owner.monotonic
        self.changed = step.get("changed", False)
        self.notifications = step.get("notifications", [])
        if step.get("advance_price"):
            for frame in self.frames.values():
                frame["close"][-1] = 102.0
        if step.get("timeout"):
            raise TimeoutError()
        return step.get("received", False)

    def get_notifies(self):
        return self.notifications

    def close(self):
        self.closed = True


class ProductionFeedTests(unittest.TestCase):
    def setUp(self):
        self.started = self.now = timestamp("2026-11-11T10:35:00+08:00")
        self.monotonic = 0.0
        self.presets = [{"symbol": "EX.p" + str(index), "tick": 1, "tick_value": 10, "name": "test"}
                        for index in range(16)]
        self.cache = WorkspaceMarketCache(lambda: self.now)
        for replacement in (
            patch.object(app.symbols, "PRESETS", self.presets),
            patch.object(app, "_workspace_market", self.cache),
            patch.object(app, "_workspace_live_quotes", {}),
            patch.object(app, "_state", {**copy.copy(app._state), "connected": True}),
            patch.object(app.time, "time", side_effect=lambda: self.now),
            patch.object(app.time, "monotonic", side_effect=lambda: self.monotonic),
            patch.dict(app.app.config, {"WORKSPACE_PRODUCTION": True}),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def run_session(self, api):
        with self.assertRaises(Finished):
            app._workspace_tqsdk_session(api, api)

    def test_no_viewers_subscribe_only_quotes_not_large_history(self):
        api = FakeAPI(self, [{"received": True}])
        self.run_session(api)
        self.assertEqual(api.quote_calls, [[p["symbol"] for p in self.presets]])
        self.assertEqual(api.calls, [])
        quotes = app._workspace_quotes_provider()
        self.assertEqual(len(quotes), 16)
        self.assertTrue(all(q["price"] == 100 and q["source"] == "live" for q in quotes))

    def test_history_is_full_reused_and_conversion_is_not_per_tick(self):
        symbol = self.presets[1]["symbol"]
        self.cache.request(symbol)
        api = FakeAPI(self, [{"at": 0, "received": True}, {"at": .2, "received": True},
                             {"at": 1.2, "received": True, "advance_price": True}])
        with patch.object(app, "_serial_rows", wraps=app._serial_rows) as convert:
            self.run_session(api)
        self.assertEqual(api.calls, [(symbol, 86400, 180), (symbol, 1800, 6000), (symbol, 180, 10000)])
        self.assertEqual(convert.call_count, 6)
        market = self.cache.snapshot(symbol, self.presets[1], app.get_state())
        self.assertEqual([len(market["periods"][key]) for key in ("day", "m30", "m3")], [180, 6000, 10000])
        self.assertEqual(market["price"], 102)
        self.assertTrue(all(item["checked_recent"] for item in market["freshness"].values()))
        self.assertTrue(market["periods"]["m3"][-2]["closed"])
        self.assertFalse(market["periods"]["m3"][-1]["closed"])

    def test_multiple_viewers_use_only_their_own_three_periods(self):
        for preset in self.presets[2:4]:
            self.cache.request(preset["symbol"])
        api = FakeAPI(self, [{"received": False}, {"at": .5, "received": False}])
        self.run_session(api)
        self.assertEqual(len(api.calls), 6)
        self.assertEqual({call[0] for call in api.calls}, {p["symbol"] for p in self.presets[2:4]})
        self.assertEqual(len(set(api.calls)), 6)

    def test_disconnect_wins_and_timeout_does_not_fake_recovery(self):
        api = FakeAPI(self, [
            {"received": True, "changed": True, "notifications": [{"conn_id": "md", "code": 2019112911}]},
            {"at": 1, "timeout": True},
            {"at": 2, "received": True, "changed": True},
        ])
        self.run_session(api)
        self.assertFalse(api.observed[1]["connected"])
        self.assertFalse(api.observed[2]["connected"])
        self.assertTrue(api.observed[3]["connected"])
        self.assertEqual(api.observed[3]["connection_status"], "connected")

    def test_timeout_keeps_idle_quote_and_unchanged_bar_clock(self):
        symbol = self.presets[0]["symbol"]
        self.cache.request(symbol)
        api = FakeAPI(self, [{"received": True}, {"at": 30, "timeout": True}])
        self.run_session(api)
        market = self.cache.snapshot(symbol, self.presets[0], app.get_state())
        self.assertTrue(market["connected"])
        self.assertFalse(market["fresh"])
        self.assertEqual(market["feed_status"], "idle")
        self.assertNotEqual(market["quote_at"], market["as_of"])

    def test_invalid_quote_price_is_null_and_connection_state_is_current(self):
        for invalid in (math.nan, math.inf, -1, 0, True, None):
            with self.subTest(invalid=invalid):
                app._cache_workspace_quotes({self.presets[0]["symbol"]: types.SimpleNamespace(last_price=invalid, datetime="")})
                self.assertIsNone(app._workspace_quotes_provider()[0]["price"])
        app._state["connected"] = False
        self.assertFalse(app._workspace_quotes_provider()[0]["connected"])

    def test_actual_sdk_entry_routes_production_without_legacy_analysis(self):
        api = FakeAPI(self, [{"received": True}, {"received": True}])
        fake_module = types.SimpleNamespace(TqApi=Mock(return_value=api), TqAuth=Mock(), TqNotify=Mock(return_value=api))
        with patch.dict(sys.modules, {"tqsdk": fake_module}), \
                patch.object(app.config, "TQ_USER", ""), \
                patch.object(app.config, "SYMBOL", self.presets[0]["symbol"]), \
                patch.object(app.symbols, "refresh_from_api"), \
                patch.object(app.symbols, "rollover_if_expired", return_value=self.presets[0]["symbol"]), \
                patch.object(app.symbols, "apply_symbol_params"), \
                patch.object(app, "_global_pass", side_effect=AssertionError("legacy scoring must not run")) as radar, \
                patch.object(app, "_refresh_focus_charts", side_effect=AssertionError("legacy charts must not run")), \
                patch.object(app, "_dump_signals_state", side_effect=AssertionError("legacy state must not be written")) as dump:
            with self.assertRaises(Finished):
                app._tqsdk_loop()
        fake_module.TqApi.assert_called_once()
        radar.assert_not_called()
        dump.assert_not_called()
        self.assertTrue(api.closed)
        self.assertEqual(api.calls, [])

    def test_desktop_quote_provider_still_reads_existing_radar(self):
        code = self.presets[0]["symbol"]
        with patch.dict(app.app.config, {"WORKSPACE_PRODUCTION": False}), \
                patch.object(app, "_outlook_data", {(code, 180): {"bars": [{"close": 321, "dt": "old"}], "updated": 5}}):
            self.assertEqual(app._workspace_quotes_provider()[0]["price"], 321)


if __name__ == "__main__":
    unittest.main()
