"""Offline contract tests for the fixed 30-minute structure presentation.

All market caches, stores, connection status and clocks are isolated. These
tests never start a market session or write the application's actual state.
"""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from trading import TradingStore
from test_trade_plan import bars, small


class StructurePresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="structure-presentation-")
        self.addCleanup(self.temp.cleanup)
        self.store = TradingStore(Path(self.temp.name) / "ledger.json")
        self.sym = app.symbols.PRESETS[0]["symbol"]
        self.stamp = datetime(2026, 10, 19, 10, tzinfo=timezone(timedelta(hours=8)))
        self.clock = self.stamp.timestamp() + 5
        for replacement in (
            patch.object(app, "_trading_store", self.store),
            patch.object(app, "_structure_store", app.outlook_store.OutlookStore()),
            patch.object(app, "_outlook_data", {}),
            patch.object(app, "_extra_requests", set()),
            patch.object(app, "_state", {"connected": True, "generation": 17, "combo": "intraday30"}),
            patch.object(app.config, "MOCK_MODE", False),
            patch.object(app.config, "SYMBOL", self.sym),
            patch.object(app.time, "time", return_value=self.clock),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.primary = self.dated(bars(), 30)
        self.short = self.dated(small(), 3)
        self.short[-1].update(open=112, close=112, high=112.5, low=111.5)
        app._cache_outlook_bars(self.sym, 1800, self.primary)
        app._cache_outlook_bars(self.sym, 180, self.short)
        self.store.update_settings({"fee_per_lot": 0}, self.sym)
        self.client = app.app.test_client()

    def dated(self, source, minutes):
        result = copy.deepcopy(source)
        for i, item in enumerate(result):
            item["dt"] = (self.stamp - timedelta(minutes=minutes * (len(result) - 1 - i))).strftime("%Y-%m-%d %H:%M:%S")
        return result

    def bundle(self, **query):
        return self.client.get("/api/trade_plan", query_string={"symbol": self.sym, **query}).json

    def assert_no_execution(self, bundle):
        self.assertNotIn("plan", bundle)
        context = bundle.get("structure_context")
        if context is not None:
            self.assertFalse(context["plan"])
            self.assertFalse(context["risk"])

    def test_live_context_and_plan_share_identity_reference_and_risk(self):
        result = self.bundle()
        context = result["structure_context"]
        chart = self.client.get("/api/outlook", query_string={"symbol": self.sym, "period": 1800}).json["analysis"]
        self.assertEqual(result["plan"]["status"], "ready")
        self.assertTrue(context["realtime"])
        self.assertEqual((context["symbol"], context["period"]), (self.sym, 1800))
        self.assertEqual(context["structure_id"], result["plan"]["structure_id"])
        self.assertEqual(context["structure_id"], chart["structure_tracking"]["active"]["id"])
        self.assertEqual(context["phase"], chart["state"]["key"])
        self.assertEqual(context["reference_end"], result["plan"]["reference"]["end"])
        self.assertEqual(context["range"], chart["range"])
        self.assertEqual(context["plan"], result["plan"])
        self.assertEqual(context["risk"], result["risk"])

    def test_last_six_completed_bars_exclude_live_and_live_changes_do_not_rewrite_them(self):
        before = self.bundle()["structure_context"]
        expected = [{key: bar[key] for key in ("dt", "open", "high", "low", "close")}
                    for bar in self.primary[-7:-1]]
        self.assertEqual(before["completed"], expected)
        self.assertEqual(len(before["completed"]), 6)
        changed = copy.deepcopy(self.primary)
        changed[-1].update(open=900, close=900, low=1, high=1500)
        app._cache_outlook_bars(self.sym, 1800, changed)
        after = self.bundle()["structure_context"]
        for field in ("completed", "structure_id", "phase", "range", "reference_end", "buffer", "atr"):
            self.assertEqual(after[field], before[field], field)
        self.assertNotIn(changed[-1]["dt"], [b["dt"] for b in after["completed"]])

    def test_auxiliary_chart_period_cannot_replace_thirty_minute_context(self):
        before = self.bundle()["structure_context"]
        for period in (900, 3600):
            alternate = self.dated(bars(), period // 60)
            for item in alternate:
                for field in ("open", "high", "low", "close"):
                    item[field] += 1000
            app._cache_outlook_bars(self.sym, period, alternate)
            chart = self.client.get("/api/outlook", query_string={"symbol": self.sym, "period": period}).json
            self.assertEqual(chart["period"], period)
            after = self.bundle(period=period)["structure_context"]
            for field in ("symbol", "period", "structure_id", "phase", "range", "completed", "reference_end"):
                self.assertEqual(after[field], before[field], (period, field))

    def test_other_symbol_receives_only_its_own_structure_context(self):
        other = app.symbols.PRESETS[1]["symbol"]
        primary, short = copy.deepcopy(self.primary), copy.deepcopy(self.short)
        for item in primary + short:
            for field in ("open", "high", "low", "close"):
                item[field] += 1000
        app._cache_outlook_bars(other, 1800, primary)
        app._cache_outlook_bars(other, 180, short)
        first = self.bundle()["structure_context"]
        second = self.bundle(symbol=other)["structure_context"]
        self.assertEqual(second["symbol"], other)
        self.assertNotEqual(second["structure_id"], first["structure_id"])
        self.assertGreater(second["range"]["low"], first["range"]["high"])
        self.assertEqual(second["completed"][-1]["close"], primary[-2]["close"])

    def test_disconnect_or_either_stale_clock_preserves_only_historical_context(self):
        first = self.bundle()["structure_context"]
        for cause in ("disconnect", 180, 1800):
            with self.subTest(cause=cause):
                app._state["connected"] = cause != "disconnect"
                for period in (180, 1800):
                    app._outlook_data[(self.sym, period)]["market_updated"] = self.clock - (21 if period == cause else 0)
                result = self.bundle()
                self.assertEqual(result["status"], "error")
                self.assert_no_execution(result)
                context = result["structure_context"]
                self.assertFalse(context["realtime"])
                self.assertEqual(context["structure_id"], first["structure_id"])
                self.assertEqual(context["completed"], first["completed"])

    def test_missing_short_series_keeps_history_but_missing_primary_has_no_context(self):
        first = self.bundle()["structure_context"]
        app._outlook_data.pop((self.sym, 180))
        missing_short = self.bundle()
        self.assertEqual(missing_short["status"], "loading")
        self.assert_no_execution(missing_short)
        self.assertFalse(missing_short["structure_context"]["realtime"])
        self.assertEqual(missing_short["structure_context"]["structure_id"], first["structure_id"])
        app._outlook_data.pop((self.sym, 1800))
        missing_primary = self.bundle()
        self.assertEqual(missing_primary["status"], "loading")
        self.assertIsNone(missing_primary["structure_context"])

    def test_context_keeps_server_risk_block_instead_of_technical_candidate(self):
        self.store.update_settings({"fee_per_lot": None}, self.sym)
        result = self.bundle()
        context = result["structure_context"]
        self.assertEqual(context["plan"]["technical_status"], "candidate")
        self.assertEqual(context["plan"]["status"], "risk_blocked")
        self.assertEqual(context["risk"]["max_lots"], 0)
        self.assertFalse(any(row["allowed"] for row in context["risk"]["rows"]))
        self.assertEqual(context["plan"], result["plan"])

    def test_storage_failure_never_leaves_a_ready_plan_inside_context(self):
        self.assertEqual(self.bundle()["plan"]["status"], "ready")
        with patch.object(self.store, "record_signal", side_effect=OSError("offline test disk failure")):
            failed_record = self.bundle()
        self.assertEqual(failed_record["status"], "error")
        self.assert_no_execution(failed_record)
        app._structure_store.load_error = "offline test corrupted structure"
        failed_structure = self.bundle()
        self.assertEqual(failed_structure["status"], "error")
        self.assert_no_execution(failed_structure)
        self.assertIsNone(failed_structure["structure_context"])

    def test_cache_advancing_during_plan_calculation_does_not_mix_snapshots(self):
        expected = self.bundle()["structure_context"]
        next_bars = copy.deepcopy(self.primary)
        next_bars.append({**next_bars[-1], "dt": (self.stamp + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")})
        original = app.trade_plan.build_plan

        def build_with_cache_advance(*args, **kwargs):
            result = original(*args, **kwargs)
            app._cache_outlook_bars(self.sym, 1800, next_bars)
            return result

        with patch.object(app.trade_plan, "build_plan", side_effect=build_with_cache_advance):
            result = self.bundle()
        context = result["structure_context"]
        self.assertNotEqual(app._outlook_data[(self.sym, 1800)]["bars"][-2]["dt"], context["completed"][-1]["dt"])
        self.assertEqual(context["completed"], expected["completed"])
        self.assertEqual(context["structure_id"], result["plan"]["structure_id"])
        self.assertEqual(context["completed"][-1]["dt"], result["plan"]["decision_bar_time"])
        self.assertEqual(context["reference_end"], result["plan"]["reference"]["end"])


if __name__ == "__main__":
    unittest.main()
