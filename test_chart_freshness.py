"""Offline market-cache regressions. No real SDK session or application state writes."""
import copy
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import app
from trading import TradingStore


class ChartFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 15, 20, tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.sym = app.symbols.PRESETS[0]["symbol"]
        self.temp = tempfile.TemporaryDirectory(prefix="chart-freshness-")
        self.addCleanup(self.temp.cleanup)
        self.store = TradingStore(Path(self.temp.name) / "ledger.json")
        for replacement in (
            patch.object(app, "_trading_store", self.store),
            patch.object(app, "_structure_store", app.outlook_store.OutlookStore()),
            patch.object(app, "_outlook_data", {}),
            patch.object(app, "_overview", {}),
            patch.object(app, "_extra_data", {}),
            patch.object(app, "_extra_requests", set()),
            patch.object(app, "_state", {"connected": True, "connection_status": "connected", "generation": 1}),
            patch.object(app.config, "MOCK_MODE", False),
            patch.object(app.config, "SYMBOL", self.sym),
            patch.object(app.time, "time", side_effect=lambda: self.now),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.client = app.app.test_client()

    def bars(self, period=1800, last=None, size=80):
        end = self.now - 5 if last is None else last
        return [{"dt": datetime.fromtimestamp(end - (size - 1 - i) * period,
                                              timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
                 "open": 100, "high": 103, "low": 97, "close": 100 + (i % 3 - 1),
                 "volume": i + 20, "oi": 1} for i in range(size)]

    def test_quiet_subscription_is_latest_available_not_stale(self):
        raw = self.bars()
        original = copy.deepcopy(raw)
        app._cache_chart_bars(self.sym, 1800, raw)
        initial = app._extra_data[1800].copy()
        self.now += 31
        app._cache_chart_bars(self.sym, 1800, raw)
        result = self.client.get("/api/kline?period=1800").json
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["freshness"]["status"], "latest_available")
        self.assertFalse(result["realtime"])
        self.assertEqual(result["updated"], self.now)
        self.assertEqual(result["freshness"]["changed_at"], initial["changed_at"])
        self.assertEqual(result["market_updated"], initial["market_updated"])
        self.assertEqual(result["bars"][-1]["dt"], raw[-1]["dt"])
        self.assertEqual(raw, original)  # chart indicators cannot mutate SDK/raw snapshots
        self.assertNotIn("ma5", app._extra_data[1800]["full"][-1])

    def test_old_final_bar_waits_for_next_and_daily_has_no_invented_close(self):
        last = datetime(2026, 10, 19, 14, 30, tzinfo=timezone(timedelta(hours=8))).timestamp()
        raw = self.bars(last=last)
        app._cache_chart_bars(self.sym, 1800, raw)
        response = self.client.get("/api/outlook?period=1800").json
        self.assertEqual(response["freshness"]["status"], "latest_available")
        self.assertEqual(response["freshness"]["last_bar_state"], "awaiting_next")
        self.assertEqual(response["freshness"]["last_completed_time"], raw[-2]["dt"])
        self.assertEqual(response["bars"][-1], raw[-1])
        self.assertEqual(response["freshness"]["session"], "unknown")
        app._cache_chart_bars(self.sym, 86400, self.bars(86400, last=last))
        daily = self.client.get("/api/kline?period=86400").json
        self.assertEqual(daily["freshness"]["last_bar_state"], "unknown")
        self.assertFalse(daily["realtime"])

    def test_new_live_bar_is_included_by_both_chart_endpoints(self):
        raw = self.bars()
        raw[-1].update(high=123, close=122)
        app._cache_chart_bars(self.sym, 1800, raw)
        for route in ("/api/kline?period=1800", "/api/outlook?period=1800"):
            result = self.client.get(route).json
            self.assertEqual(result["freshness"]["status"], "receiving")
            self.assertEqual(result["freshness"]["last_bar_state"], "forming")
            self.assertEqual(result["bars"][-1]["close"], 122)
            self.assertTrue(result["realtime"])

    def test_stale_and_disconnected_are_distinct_and_return_no_live_paths(self):
        app._cache_chart_bars(self.sym, 1800, self.bars())
        self.now += 21
        for route in ("/api/kline?period=1800", "/api/outlook?period=1800"):
            response = self.client.get(route).json
            self.assertEqual(response["freshness"]["status"], "stale")
            self.assertNotIn("bars", response)
            self.assertNotIn("analysis", response)
        app._state.update(connected=False, connection_status="reconnecting")
        response = self.client.get("/api/outlook").json
        self.assertEqual(response["freshness"]["status"], "disconnected")
        self.assertEqual(response["freshness"]["connection_status"], "reconnecting")

    def test_quiet_or_future_clock_never_enables_trade_plan(self):
        primary, small = self.bars(), self.bars(180)
        app._cache_outlook_bars(self.sym, 1800, primary)
        app._cache_outlook_bars(self.sym, 180, small)
        self.now += 21
        app._cache_outlook_bars(self.sym, 1800, primary)
        app._cache_outlook_bars(self.sym, 180, small)
        quiet = self.client.get("/api/trade_plan").json
        self.assertNotIn("plan", quiet)
        self.assertEqual(quiet["freshness"]["status"], "latest_available")
        self.assertFalse(quiet["structure_context"]["plan"])
        self.assertFalse(app._registered_prices())
        app._outlook_data[(self.sym, 1800)]["market_updated"] = self.now + 60
        app._outlook_data[(self.sym, 180)]["market_updated"] = self.now + 60
        self.assertNotIn("plan", self.client.get("/api/trade_plan").json)
        self.assertFalse(app._registered_prices())

    def test_future_source_bar_is_not_realtime(self):
        app._cache_chart_bars(self.sym, 1800, self.bars(last=self.now + 3600))
        response = self.client.get("/api/outlook").json
        self.assertFalse(response["realtime"])
        self.assertEqual(response["freshness"]["last_bar_state"], "unknown")

    def test_one_future_series_cannot_be_hidden_by_other_series_current_clock(self):
        for future_period in (180, 1800):
            for period in (180, 1800):
                app._cache_outlook_bars(self.sym, period, self.bars(period))
                app._outlook_data[(self.sym, period)]["market_updated"] = self.now + (60 if period == future_period else 0)
            self.assertNotIn("plan", self.client.get("/api/trade_plan").json)

    def test_cache_stop_blocks_plan_even_if_market_timestamp_is_recent(self):
        for period in (1800, 180):
            app._cache_outlook_bars(self.sym, period, self.bars(period))
            app._outlook_data[(self.sym, period)]["updated"] = self.now - 21
        response = self.client.get("/api/trade_plan").json
        self.assertNotIn("plan", response)
        self.assertEqual(response["freshness"]["status"], "stale")
        self.assertFalse(app._registered_prices())

    def test_requested_periods_continue_and_recovery_reads_same_serial(self):
        snapshots = {(self.sym, p): self.bars(p) for p in (180, 1800, 900, 86400)}
        api = Mock()
        api.get_kline_serial.side_effect = lambda sym, period, size: snapshots[(sym, period)]
        reader = app._KlineSubscriptions(api)
        app._extra_requests.update((900, 86400))
        app._refresh_focus_charts(self.sym, reader.get)
        old = app._extra_data[900]["market_updated"]
        self.now += 31
        app._state.update(connected=False, connection_status="reconnecting")
        app._refresh_focus_charts(self.sym, reader.get)
        self.assertEqual(app._extra_data[900]["market_updated"], old)
        app._note_feed_poll(received=True, notifications=[{"conn_id": "md", "code": 2019112902}])
        snapshots[(self.sym, 900)][-1].update(close=102)
        app._refresh_focus_charts(self.sym, reader.get)
        response = self.client.get("/api/kline?period=900&refresh=1").json
        self.assertEqual(response["bars"][-1]["close"], 102)
        self.assertEqual(response["freshness"]["status"], "receiving")
        self.assertTrue(response["refresh_requested"])
        self.assertEqual(api.get_kline_serial.call_count, 4)
        self.assertEqual(set(app._extra_data), {180, 1800, 900, 86400})

    def test_individual_refresh_failure_expires_only_that_series(self):
        app._extra_requests.add(900)
        snapshots = {period: self.bars(period) for period in (180, 1800, 900)}
        app._refresh_focus_charts(self.sym, lambda sym, period: snapshots[period])
        self.now += 21
        def getter(sym, period):
            if period == 900:
                raise ValueError("offline synthetic serial error")
            return snapshots[period]
        app._refresh_focus_charts(self.sym, getter)
        self.assertEqual(self.client.get("/api/kline?period=900").json["freshness"]["status"], "stale")
        self.assertEqual(self.client.get("/api/kline?period=1800").json["freshness"]["status"], "latest_available")

    def test_wait_timeout_is_not_connection_failure_and_business_clock_is_separate(self):
        app._note_feed_poll(received=True)
        received = app._state["feed_received_at"]
        self.now += 35
        app._note_feed_poll(received=False)
        self.assertTrue(app._state["connected"])
        self.assertEqual(app._state["feed_checked_at"], self.now)
        self.assertEqual(app._state["feed_received_at"], received)
        app._note_feed_poll(received=True, notifications=[{"conn_id": "td", "code": 2019112911}])
        self.assertTrue(app._state["connected"])
        app._note_feed_poll(received=True, notifications=[{"conn_id": "md", "code": 2019112911}])
        self.assertFalse(app._state["connected"])
        app._note_feed_poll(received=False)
        self.assertFalse(app._state["connected"])
        app._note_feed_poll(received=True, market_changed=True)
        self.assertTrue(app._state["connected"])

    def test_conflicting_connection_notifications_remain_closed_until_new_evidence(self):
        notes = [{"conn_id": "md", "code": code} for code in (2019112902, 2019112911)]
        app._note_feed_poll(received=True, notifications=notes, market_changed=True)
        self.assertFalse(app._state["connected"])
        app._note_feed_poll(received=True, notifications=[{"conn_id": "md", "code": 2019112902}])
        self.assertTrue(app._state["connected"])

    def test_refresh_does_not_change_symbol_or_directly_create_sdk(self):
        with patch.object(app, "_tqsdk_loop", side_effect=AssertionError("HTTP must not log in")):
            loading = self.client.get("/api/kline?period=900&refresh=1").json
            self.assertEqual(loading["status"], "loading")
            self.assertIn(900, app._extra_requests)
            self.client.get("/api/outlook?period=3600&refresh=1")
            self.assertIn(3600, app._extra_requests)
            before = set(app._extra_requests)
            other = app.symbols.PRESETS[1]["symbol"]
            self.client.get("/api/outlook", query_string={"symbol": other, "period": 900, "refresh": 1})
            self.assertEqual(app._extra_requests, before)
            self.assertEqual(app.config.SYMBOL, self.sym)

    def test_structure_presentation_passes_market_evidence_without_enabling_history(self):
        raw = self.bars(last=self.now - 7200)
        app._cache_outlook_bars(self.sym, 1800, raw)
        primary = app._outlook_data[(self.sym, 1800)]
        evidence = {"phase": {"key": "trend_break_watch", "label": "趋势线变化观察"}}
        analysis = {"status": "ok", "market_structure": evidence, "price": 100}
        context = app._structure_presentation(self.sym, primary, None, analysis,
                                              plan={"status": "ready"}, risk={"max_lots": 2})
        self.assertEqual(context["market_structure"], evidence)
        self.assertEqual(context["freshness"]["status"], "latest_available")
        self.assertFalse(context["realtime"])
        self.assertFalse(context["plan"])
        self.assertFalse(context["risk"])
        self.assertEqual(context["completed"][-1]["dt"], raw[-2]["dt"])

    def test_overview_market_fields_are_strings(self):
        analysis = {"status": "ok", "market_structure": {
            "phase": {"key": "watch", "label": "趋势线变化观察", "summary": "等待后续完成棒"}}}
        with patch.object(app, "_tracked_outlook", return_value=analysis):
            summary = app._outlook_summary(self.sym, self.bars())
        self.assertEqual(summary["market_phase"], "趋势线变化观察")
        self.assertEqual(summary["market_summary"], "等待后续完成棒")

    def overview_card(self):
        return next(card for card in self.client.get("/api/overview").json["symbols"] if card["symbol"] == self.sym)

    def test_quiet_overview_keeps_reference_price_and_structure_but_blocks_live_candidate(self):
        primary = self.bars(last=self.now - 7200)
        app._cache_outlook_bars(self.sym, 1800, primary)
        app._cache_outlook_bars(self.sym, 180, self.bars(180, last=self.now - 7200))
        app._overview[self.sym] = {"name": "test", "price": 999, "lamp": "red",
                                   "decision": {"plan_status": "ready", "quadrant": "confirm", "max_lots": 2},
                                   "outlook": app._outlook_summary(self.sym, primary)}
        before = copy.deepcopy(app._overview)
        ledger = copy.deepcopy(self.store.data)
        card = self.overview_card()
        self.assertTrue(card["display_available"])
        self.assertEqual(card["view_mode"], "preparation")
        self.assertEqual(card["freshness"]["status"], "latest_available")
        self.assertEqual(card["price"], primary[-1]["close"])
        self.assertEqual(card["price_bar_time"], primary[-1]["dt"])
        self.assertEqual(card["outlook"], before[self.sym]["outlook"])
        self.assertIn("preparation", card["outlook"])
        self.assertFalse(card["realtime"])
        self.assertTrue(card["stale"])  # legacy execution freshness flag
        self.assertEqual(card["decision"]["plan_status"], "preparation")
        self.assertEqual(card["decision"]["quadrant"], "standby")
        self.assertEqual(card["decision"]["max_lots"], 0)
        self.assertEqual(card["lamp"], "gray")
        self.assertNotIn("plan", self.client.get("/api/trade_plan").json)
        self.assertEqual(app._overview, before)
        self.assertEqual(self.store.data, ledger)

    def test_overview_missing_small_series_or_slow_auxiliary_warmup_still_allows_30m_preparation(self):
        primary = self.bars(last=self.now - 7200)
        app._cache_outlook_bars(self.sym, 1800, primary)
        card = self.overview_card()
        self.assertTrue(card["display_available"])
        self.assertEqual(card["view_mode"], "preparation")
        self.assertEqual(card["outlook"]["period"], 1800)
        self.assertEqual(card["confirmation_freshness"]["status"], "loading")
        self.assertFalse(card["realtime"])

    def test_overview_cache_failure_or_disconnect_remains_unavailable(self):
        for period in (1800, 180):
            app._cache_outlook_bars(self.sym, period, self.bars(period))
        self.now += 21
        card = self.overview_card()
        self.assertEqual(card["view_mode"], "unavailable")
        self.assertEqual(card["freshness"]["status"], "stale")
        self.assertFalse(card["display_available"])
        app._state["connected"] = False
        card = self.overview_card()
        self.assertEqual(card["freshness"]["status"], "disconnected")
        self.assertFalse(card["display_available"])
        self.assertEqual(card["decision"]["max_lots"], 0)

    def test_overview_future_market_clock_cannot_restore_a_live_candidate(self):
        for period in (1800, 180):
            app._cache_outlook_bars(self.sym, period, self.bars(period))
        app._overview[self.sym] = {"name": "test", "decision": {"plan_status": "ready", "quadrant": "confirm", "max_lots": 2}}
        for future_period in (1800, 180):
            for period in (1800, 180):
                app._outlook_data[(self.sym, period)]["market_updated"] = self.now + (60 if period == future_period else 0)
            card = self.overview_card()
            self.assertFalse(card["realtime"])
            self.assertEqual(card["decision"]["quadrant"], "standby")
            self.assertEqual(card["decision"]["max_lots"], 0)

    def test_overview_can_resume_live_without_reviving_ready_when_either_series_is_quiet(self):
        for period in (1800, 180):
            app._cache_outlook_bars(self.sym, period, self.bars(period))
        decision = {"plan_status": "ready", "quadrant": "confirm", "max_lots": 1}
        app._overview[self.sym] = {"name": "test", "decision": decision, "lamp": "green"}
        self.assertTrue(self.overview_card()["realtime"])
        self.assertEqual(self.overview_card()["decision"], decision)
        for period in (1800, 180):
            app._outlook_data[(self.sym, period)]["market_updated"] = self.now - 21
            card = self.overview_card()
            self.assertTrue(card["display_available"])
            self.assertEqual(card["view_mode"], "preparation")
            self.assertEqual(card["decision"]["max_lots"], 0)
            app._outlook_data[(self.sym, period)]["market_updated"] = self.now
        self.assertEqual(self.overview_card()["decision"], decision)

    def test_real_loop_keeps_focus_charts_subscribed_before_auxiliary_warmup(self):
        """Exercise the actual loop with an in-process fake SDK, never the installed SDK."""
        outer = self
        class Column(list):
            def tolist(self):
                return list(self)
        class FakeAPI:
            def __init__(self, **kwargs):
                self.calls, self.waits, self.closed = [], 0, False
            def get_quote(self, sym):
                return types.SimpleNamespace(datetime="2026-10-19 15:20:00", last_price=100, expired=False)
            def get_kline_serial(self, sym, period, length):
                self.calls.append((sym, period))
                raw = outer.bars(period) if period != 30 else []
                frame = {key: Column([bar[key] for bar in raw]) for key in ("open", "high", "low", "close", "volume")}
                frame["datetime"] = Column([int(app._bar_timestamp(bar["dt"]) * 1e9) for bar in raw])
                return frame
            def wait_update(self, **kwargs):
                self.waits += 1
                if self.waits == 5:
                    raise RuntimeError("intentional offline test stop")
                outer.now += 15  # also crosses the former 30-second fatal warmup timeout
                return False
            def is_changing(self, serial):
                return False
            def close(self):
                self.closed = True
        api = FakeAPI()
        notify = Mock()
        notify.get_notifies.side_effect = [[{"conn_id": "md", "code": 2019112901}], [], []]
        fake_module = types.SimpleNamespace(TqApi=Mock(return_value=api), TqAuth=Mock(),
                                            TqNotify=Mock(return_value=notify))
        combo = {"key": "offline", "trend": 900, "filter": 180, "trigger": 30,
                 "trend_label": "15分", "filter_label": "3分", "trigger_label": "30秒"}
        app._extra_requests.add(86400)
        with patch.dict(sys.modules, {"tqsdk": fake_module}), \
             patch.object(app.config, "TQ_USER", ""), \
             patch.object(app, "_current_combo", return_value=combo), \
             patch.object(app, "_consume_symbol_request", return_value=None), \
             patch.object(app, "_consume_combo_request", return_value=None), \
             patch.object(app.symbols, "refresh_from_api"), \
             patch.object(app.symbols, "rollover_if_expired", return_value=self.sym), \
             patch.object(app.symbols, "apply_symbol_params"), \
             patch.object(app, "_global_pass", side_effect=AssertionError("auxiliary warmup not ready")), \
             patch.object(app, "_dump_signals_state", side_effect=AssertionError("must not write actual state")):
            app._tqsdk_loop()
        self.assertEqual(api.waits, 5)
        self.assertTrue(api.closed)
        self.assertEqual(fake_module.TqApi.call_count, 1)
        self.assertEqual(len(api.calls), len(set(api.calls)))
        self.assertEqual(set(app._extra_data), {180, 1800, 86400})
        self.assertIn((self.sym, 1800), app._outlook_data)
        self.assertEqual(app._extra_data[1800]["full"][-1]["dt"], app._outlook_data[(self.sym, 1800)]["bars"][-1]["dt"])


if __name__ == "__main__":
    unittest.main()
