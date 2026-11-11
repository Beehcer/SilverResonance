"""Warm-cache and incremental HTTP transport, with no market connection."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from workspace_api import register_workspace
from workspace_market import PERIODS, WorkspaceMarketCache, iso, timestamp
from workspace_store import WorkspaceStore


class IncrementalMarketTests(unittest.TestCase):
    def setUp(self):
        self.now = timestamp("2026-11-11T10:35:00+08:00")
        self.allowed = ["EX." + str(i) for i in range(16)]
        self.cache = WorkspaceMarketCache(lambda: self.now, lambda: self.allowed)
        self.code = self.allowed[0]
        self.preset = {"name": "测试", "tick": 1, "tick_value": 10}
        self.rows = {}
        for period in PERIODS.values():
            start = int(self.now // period) * period
            self.rows[period] = [dict(dt=iso(start - (4-i)*period), open=100, high=104,
                                      low=98, close=101, volume=100, oi=10) for i in range(5)]
            self.cache.cache(self.code, period, self.rows[period])

    def snapshot(self, token=None, connected=True, simulated=False, symbol=None):
        return self.cache.snapshot(symbol or self.code, self.preset, {"connected": connected},
                                   simulated=simulated, history_token=token)

    def test_warm_requests_are_only_visited_configured_contracts(self):
        self.assertEqual(self.cache.requested(), [])
        self.snapshot()
        self.now += 10000
        self.assertEqual(self.cache.requested(), [self.code])
        for code in self.allowed:
            self.cache.request(code)
        for i in range(100):
            self.assertFalse(self.cache.request("unknown" + str(i)))
        self.assertEqual(set(self.cache.requested()), set(self.allowed))

    def test_tail_update_roundtrip_matches_full_snapshot(self):
        full = self.snapshot()
        self.now += 1
        for period, rows in self.rows.items():
            tail = copy.deepcopy(rows[-2:])
            tail[-2]["volume"] += 1
            tail[-1]["close"] = 102
            self.assertTrue(self.cache.update_tail(self.code, period, tail))
        delta = self.snapshot(full["history_token"])
        self.assertTrue(delta["incremental"])
        self.assertEqual(delta["period_counts"], {key: 5 for key in PERIODS})
        self.assertEqual([len(rows) for rows in delta["periods"].values()], [2, 2, 2])
        current = self.snapshot()
        for key in PERIODS:
            merged = full["periods"][key][:-2] + delta["periods"][key]
            self.assertEqual(merged, current["periods"][key])
            self.assertTrue(delta["periods"][key][-2]["closed"])
            self.assertFalse(delta["periods"][key][-1]["closed"])
        for field in ("price", "quote_at", "fresh", "freshness", "feed_status", "history_token"):
            self.assertEqual(delta[field], current[field])

    def test_full_cache_read_of_same_window_keeps_token(self):
        full = self.snapshot()
        rows = copy.deepcopy(self.rows[180])
        rows[-1]["volume"] += 1
        self.cache.cache(self.code, 180, rows)
        self.assertTrue(self.snapshot(full["history_token"])["incremental"])

    def test_any_prefix_correction_forces_all_periods_full(self):
        full = self.snapshot()
        rows = copy.deepcopy(self.rows[1800])
        rows[1]["high"] += 1
        self.cache.cache(self.code, 1800, rows)
        result = self.snapshot(full["history_token"])
        self.assertFalse(result["incremental"])
        self.assertEqual([len(rows) for rows in result["periods"].values()], [5, 5, 5])
        self.assertNotEqual(result["history_token"], full["history_token"])

    def test_last_two_time_change_or_new_bar_forces_full(self):
        full = self.snapshot()
        shifted = copy.deepcopy(self.rows[180])
        shifted[-1]["dt"] = iso(timestamp(shifted[-1]["dt"]) + 180)
        self.assertFalse(self.cache.update_tail(self.code, 180, shifted[-2:]))
        self.cache.cache(self.code, 180, shifted)
        changed = self.snapshot(full["history_token"])
        self.assertFalse(changed["incremental"])
        shifted.append({**shifted[-1], "dt": iso(timestamp(shifted[-1]["dt"]) + 180)})
        self.cache.cache(self.code, 180, shifted)
        appended = self.snapshot(changed["history_token"])
        self.assertFalse(appended["incremental"])
        self.assertEqual(appended["period_counts"]["m3"], 6)

    def test_token_bound_to_contract_and_cache_generation(self):
        token = self.snapshot()["history_token"]
        for period, rows in self.rows.items():
            self.cache.cache(self.allowed[1], period, rows)
        self.assertFalse(self.snapshot(token, symbol=self.allowed[1])["incremental"])
        replacement = WorkspaceMarketCache(lambda: self.now)
        for period, rows in self.rows.items():
            replacement.cache(self.code, period, rows)
        self.assertFalse(replacement.snapshot(self.code, self.preset, {}, history_token=token)["incremental"])
        self.assertFalse(self.snapshot("invalid")["incremental"])

    def test_disconnect_idle_and_mock_preserve_truthful_metadata(self):
        full = self.snapshot()
        self.now += 30
        for period in PERIODS.values():
            self.cache.mark_checked(self.code, period)
        for connected, simulated, status in ((True, False, "idle"), (False, False, "disconnected"), (True, True, "mock")):
            result = self.snapshot(full["history_token"], connected, simulated)
            self.assertTrue(result["incremental"])
            self.assertFalse(result["fresh"])
            self.assertEqual(result["feed_status"], status)
            self.assertEqual(result["quote_at"], full["quote_at"])

    def test_snapshots_are_isolated_and_invalid_tail_is_atomic(self):
        full = self.snapshot()
        full["periods"]["m3"][0]["close"] = -99
        self.assertEqual(self.snapshot()["periods"]["m3"][0]["close"], 101)
        tail = copy.deepcopy(self.rows[180][-2:])
        tail[-1]["low"] = 120
        before = self.snapshot()
        self.now += 1
        with self.assertRaises(ValueError):
            self.cache.update_tail(self.code, 180, tail)
        after = self.snapshot(before["history_token"])
        self.assertEqual(after["freshness"], before["freshness"])
        self.assertEqual(after["price"], before["price"])

    def test_loading_token_changes_when_period_becomes_available(self):
        empty = self.snapshot(symbol=self.allowed[2])
        self.assertTrue(empty["history_token"])
        self.assertEqual(empty["period_counts"], {key: 0 for key in PERIODS})
        self.cache.cache(self.allowed[2], 180, self.rows[180])
        loaded = self.snapshot(empty["history_token"], symbol=self.allowed[2])
        self.assertFalse(loaded["incremental"])
        self.assertEqual(loaded["symbol"], self.allowed[2])

    def test_full_retained_history_yields_small_delta_payload(self):
        for period, count in ((86400, 180), (1800, 6000), (180, 10000)):
            start = int(self.now // period) * period
            rows = [dict(dt=iso(start-(count-1-i)*period), open=100, high=104, low=98,
                         close=101, volume=100, oi=10) for i in range(count)]
            self.cache.cache(self.code, period, rows)
        full = self.snapshot()
        delta = self.snapshot(full["history_token"])
        self.assertEqual(delta["period_counts"], dict(day=180, m30=6000, m3=10000))
        self.assertLess(len(json.dumps(delta)), len(json.dumps(full)) / 100)


class IncrementalApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = WorkspaceStore(Path(self.directory.name) / "workspace.json")
        self.full_calls, self.delta_calls = [], []
        self.symbol = "EX.test"

    def application(self, optional=True):
        application = Flask(__name__)
        application.testing = True
        def full(symbol):
            self.full_calls.append(symbol)
            return {"symbol": symbol, "incremental": False}
        def delta(symbol, token):
            self.delta_calls.append((symbol, token))
            return {"symbol": symbol, "incremental": True, "history_token": token}
        register_workspace(application, self.store, full, lambda: [{"symbol": self.symbol}],
                           lambda: {}, lambda *args: {}, market_incremental_provider=delta if optional else None)
        return application

    def test_authenticated_route_dispatches_only_valid_bounded_history(self):
        client = self.application().test_client()
        path = "/api/workspace/market?symbol=" + self.symbol
        self.assertEqual(client.get(path + "&history=token").status_code, 401)
        self.assertEqual(self.delta_calls, [])
        client.get("/api/workspace/session")
        self.assertFalse(client.get(path).json["market"]["incremental"])
        self.assertTrue(client.get(path + "&history=token").json["market"]["incremental"])
        self.assertFalse(client.get(path + "&history=" + "x"*129).json["market"]["incremental"])
        self.assertEqual(self.full_calls, [self.symbol, self.symbol])
        self.assertEqual(self.delta_calls, [(self.symbol, "token")])
        self.assertEqual(client.get("/api/workspace/market?symbol=unknown&history=token").status_code, 400)
        self.assertEqual(len(self.delta_calls), 1)

    def test_one_argument_provider_remains_compatible_with_history_clients(self):
        client = self.application(optional=False).test_client()
        client.get("/api/workspace/session")
        response = client.get("/api/workspace/market?symbol=" + self.symbol + "&history=old-token")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["market"]["incremental"])
        self.assertEqual(self.full_calls, [self.symbol])


if __name__ == "__main__":
    unittest.main()
