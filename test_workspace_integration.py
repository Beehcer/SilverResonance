"""Offline integration checks; no feed connection or persisted visitor creation."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app
from workspace_market import WorkspaceMarketCache


class WorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.symbol = app.symbols.PRESETS[0]["symbol"]

    def test_shared_home_does_not_require_host_feed_credentials(self):
        with patch.object(app.user_settings, "is_configured", side_effect=AssertionError("read host credentials")):
            for route in ("/", "/workspace"):
                response = self.client.get(route, environ_overrides={"REMOTE_ADDR": "192.168.1.99"})
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"workspace.js", response.data)

    def test_shared_visitors_cannot_read_or_change_host_tools(self):
        for route in ("/monitor", "/setup", "/api/trading/settings", "/api/positions", "/api/overview", "/board-demo"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route, environ_overrides={"REMOTE_ADDR": "192.168.1.99"}).status_code, 403)
                self.assertEqual(self.client.get(route, headers={"X-Forwarded-For": "192.168.1.99"}).status_code, 403)

    def test_market_provider_has_explicit_configuration_state_and_no_focus_change(self):
        focus = app.config.SYMBOL
        cache = WorkspaceMarketCache()
        with patch.object(app, "_workspace_market", cache), patch.object(app, "get_state", return_value={"boot_phase": "need_config", "connected": False}):
            market = app._workspace_market_provider(self.symbol)
            self.assertTrue(market["setup_required"])
            self.assertIn("/setup", market["message"])
            self.assertFalse(market["fresh"])
            self.assertEqual(app.config.SYMBOL, focus)
            self.assertIn(self.symbol, cache.requested())
        with self.assertRaises(ValueError):
            app._workspace_market_provider("UNKNOWN")

    def test_mock_workspace_bars_evolve_without_rebuilding_history(self):
        series = app._mock_series(2500, 30, .2, 1)
        first = dict(series[0])
        previous_close = series[-1]["close"]
        result = app._mock_continuous_series(series, .2, 1)
        self.assertIs(result, series)
        self.assertEqual(result[0], first)
        self.assertLessEqual(abs(result[-1]["close"] - previous_close), 1)
        last = result[-1]
        self.assertLessEqual(last["low"], min(last["open"], last["close"]))
        self.assertGreaterEqual(last["high"], max(last["open"], last["close"]))
        advanced = app._mock_continuous_series(series, .2, 1, advance=True)
        self.assertEqual(len(advanced), 30)
        self.assertNotEqual(advanced[0], first)

    def test_mock_workspace_timestamps_stay_attached_when_advancing(self):
        now = int(datetime(2026, 11, 10, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        series = app._mock_timestamped_series(2500, 180, .2, 1, now=now)
        last_dt = series[-1]["dt"]
        previous_dt = series[-2]["dt"]
        app._mock_continuous_series(series, .2, 1)
        self.assertEqual(series[-1]["dt"], last_dt)
        app._mock_continuous_series(series, .2, 1, advance=True)
        self.assertEqual(series[-2]["dt"], last_dt)
        expected = datetime.fromisoformat(last_dt).timestamp() + 180
        self.assertEqual(datetime.fromisoformat(series[-1]["dt"]).timestamp(), expected)
        self.assertNotEqual(previous_dt, last_dt)

    def test_mock_history_only_contains_weekday_trading_sessions(self):
        now = int(datetime(2026, 11, 10, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())
        stamps = app._mock_trading_times(300, 180, now)
        self.assertEqual(len(stamps), 300)
        self.assertLessEqual(stamps[-1], now)
        for stamp in stamps:
            local = datetime.fromtimestamp(stamp, timezone(timedelta(hours=8)))
            self.assertLess(local.weekday(), 5)
            minutes = local.hour * 60 + local.minute
            self.assertTrue(540 <= minutes < 615 or 630 <= minutes < 690
                            or 810 <= minutes < 900 or 1260 <= minutes < 1380)

    def test_mock_periods_are_aggregated_from_the_same_price_series(self):
        series = app._mock_timestamped_series(2500, 180, .2, 1, now=(1794270000//1800)*1800, count=30)
        m3 = app._mock_aggregate_series(series, 180)
        m30 = app._mock_aggregate_series(series, 1800)
        day = app._mock_aggregate_series(series, 86400)
        self.assertEqual(len(m30), 3)
        self.assertEqual(len(day), 1)
        self.assertEqual(m3[-1]["close"], series[-1]["close"])
        self.assertEqual(m30[-1]["close"], series[-1]["close"])
        self.assertEqual(day[-1]["close"], series[-1]["close"])
        for bars in (m3, m30, day):
            for bar in bars:
                self.assertLessEqual(bar["low"], min(bar["open"], bar["close"]))
                self.assertGreaterEqual(bar["high"], max(bar["open"], bar["close"]))

    def test_large_mock_history_stays_near_the_contract_price(self):
        series = app._mock_timestamped_series(2500, 180, .2, 1,
                                              now=1794270000, count=2000)
        self.assertEqual(len(series), 2000)
        self.assertLess(abs(series[-1]["close"]-2500), 250)
        for bar in series:
            self.assertGreater(bar["low"], 0)
            self.assertLessEqual(bar["low"], min(bar["open"], bar["close"]))
            self.assertGreaterEqual(bar["high"], max(bar["open"], bar["close"]))

    def test_static_assets_are_available_to_shared_browser(self):
        for name in ("workspace.js", "workspace-chart.js", "workspace.css"):
            with self.client.get("/static/" + name, environ_overrides={"REMOTE_ADDR": "192.168.1.99"}) as response:
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
