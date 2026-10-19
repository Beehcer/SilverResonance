"""走势推演接口：隔离品种、延迟订阅和陈旧数据保护。纯离线运行。"""
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app


def bars(base=100):
    return [{"open": base, "high": base + 2, "low": base - 2,
             "close": base + (i % 3 - 1) * .2, "volume": 100, "oi": 1,
             "dt": (datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=30 * (79-i))).isoformat(sep=" ")}
            for i in range(80)]


class OutlookApiTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(app, "_outlook_data", {}),
                        patch.object(app, "_structure_store", app.outlook_store.OutlookStore()),
                        patch.object(app.config, "MOCK_MODE", False),
                        patch.object(app, "_extra_requests", set()),
                        patch.object(app, "_state", {"connected": True}),
                        patch.object(app.config, "SYMBOL", app.symbols.PRESETS[0]["symbol"])]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = app.app.test_client()
        self.sym = app.config.SYMBOL

    def test_symbol_and_tick_are_isolated(self):
        other = app.symbols.PRESETS[1]
        app._cache_outlook_bars(self.sym, 900, bars(100))
        app._cache_outlook_bars(other["symbol"], 900, bars(200))
        with patch.object(app._structure_store, "analyze", return_value={"status": "ok"}) as build:
            response = self.client.get("/api/outlook", query_string={"symbol": other["symbol"], "period": 900})
        d = response.json
        self.assertEqual(d["symbol"], other["symbol"])
        self.assertEqual(d["bars"][0]["open"], 200)
        self.assertEqual(build.call_args.kwargs["tick"], other["tick"])

    def test_thirty_minute_subscription_only_for_focus(self):
        d = self.client.get("/api/outlook?period=1800").json
        self.assertEqual(d["status"], "loading")
        self.assertIn(1800, app._extra_requests)
        app._extra_requests.clear()
        self.client.get("/api/outlook", query_string={"symbol": app.symbols.PRESETS[1]["symbol"], "period": 1800})
        self.assertFalse(app._extra_requests)

    def test_stale_or_disconnected_never_returns_paths(self):
        app._cache_outlook_bars(self.sym, 900, bars())
        app._outlook_data[(self.sym, 900)]["updated"] = time.time() - 30
        d = self.client.get("/api/outlook?period=900").json
        self.assertTrue(d["stale"])
        self.assertNotIn("analysis", d)
        self.assertNotIn("bars", d)
        app._cache_outlook_bars(self.sym, 900, bars())
        app._state["connected"] = False
        self.assertTrue(self.client.get("/api/outlook?period=900").json["stale"])

    def test_ready_and_insufficient_data(self):
        app._cache_outlook_bars(self.sym, 900, bars())
        d = self.client.get("/api/outlook?period=900").json
        self.assertEqual(d["status"], "ok")
        self.assertEqual(len(d["analysis"]["scenarios"]), 3)
        app._cache_outlook_bars(self.sym, 900, bars()[:10])
        self.assertEqual(self.client.get("/api/outlook?period=900").json["status"], "insufficient")

    def test_historical_chart_is_explicitly_not_realtime(self):
        app._cache_outlook_bars(self.sym, 1800, bars())
        app._outlook_data[(self.sym, 1800)]["market_updated"] = time.time() - 60
        response = self.client.get("/api/outlook").json
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["period"], 1800)
        self.assertFalse(response["realtime"])
        self.assertIn("analysis", response)

    def test_invalid_request_has_no_subscription_side_effect(self):
        for args in ({"period": "bad"}, {"period": 30}, {"symbol": "UNKNOWN.bad"}):
            self.assertEqual(self.client.get("/api/outlook", query_string=args).status_code, 400)
        self.assertFalse(app._extra_requests)


if __name__ == "__main__":
    unittest.main()
