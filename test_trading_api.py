"""Offline integration tests: plan identity, live-state gates and local records."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import app
import trading
from test_trade_plan import bars, small


class TradingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sym = app.symbols.PRESETS[0]["symbol"]
        self.preset = app.symbols.find(self.sym)
        self.store = trading.TradingStore(Path(self.temp.name) / "records.json")
        for p in (patch.object(app, "_trading_store", self.store),
                  patch.object(app, "_structure_store", app.outlook_store.OutlookStore()),
                  patch.object(app.config, "MOCK_MODE", False),
                  patch.object(app, "_outlook_data", {}),
                  patch.object(app, "_extra_data", {}),
                  patch.object(app, "_extra_requests", set()),
                  patch.object(app, "_overview", {}),
                  patch.object(app, "_state", {"connected": True, "generation": 7,
                                               "combo": "intraday30", "result": None}),
                  patch.object(app.config, "SYMBOL", self.sym)):
            p.start()
            self.addCleanup(p.stop)
        primary = bars()
        stamp = datetime.now().replace(second=0, microsecond=0)
        for i, b in enumerate(primary):
            b["dt"] = (stamp - timedelta(minutes=30 * (len(primary)-1-i))).isoformat(sep=" ")
        app._cache_outlook_bars(self.sym, 1800, primary)
        app._cache_outlook_bars(self.sym, 180, small()[:-1] + [dict(small()[-1], close=112)])
        self.client = app.app.test_client()

    def test_defaults_and_unknown_fees_never_allow_candidate(self):
        settings = self.client.get("/api/trading/settings").json["settings"]
        self.assertEqual((settings["equity"], settings["max_loss"]), (50000, 1000))
        response = self.client.get("/api/trade_plan").json
        self.assertEqual((response["symbol"], response["period"], response["generation"]), (self.sym, 1800, 7))
        self.assertEqual(response["plan"]["status"], "risk_blocked")
        self.assertFalse(response["risk"]["fee_configured"])
        self.assertEqual(response["risk"]["max_lots"], 0)
        self.assertFalse(any(row["allowed"] for row in response["risk"]["rows"]))

    def test_entry_route_and_plan_use_same_calculation(self):
        self.store.update_settings({"fee_per_lot": 0}, self.sym)
        plan = self.client.get("/api/trade_plan").json
        entry = self.client.get("/api/entry_check").json
        self.assertEqual(plan["plan"], entry["plan"])
        self.assertEqual(plan["plan"]["status"], "ready")
        self.assertEqual(entry["max_lots"], plan["risk"]["max_lots"])
        self.assertEqual(entry["position_pct"], 0)
        self.assertEqual(entry["quadrant"], "confirm")

    def test_plan_chart_and_overview_share_structure_and_store_failure_blocks(self):
        self.store.update_settings({"fee_per_lot": 0}, self.sym)
        plan = self.client.get("/api/trade_plan").json["plan"]
        view = self.client.get("/api/outlook").json["analysis"]
        active = view["structure_tracking"]["active"]
        summary = app._outlook_summary(self.sym, app._outlook_data[(self.sym, 1800)]["bars"])
        self.assertEqual(plan["structure_id"], active["id"])
        self.assertEqual(plan["structure_phase"], active["phase"])
        self.assertEqual(summary["structure_id"], active["id"])
        self.assertEqual(plan["reference"]["end"], view["range"]["end_time"])
        app._structure_store.load_error = "test: corrupted structure file"
        for route in ("/api/trade_plan", "/api/entry_check", "/api/outlook"):
            response = self.client.get(route).json
            self.assertEqual(response["status"], "error")
            self.assertNotIn("plan", response)
            self.assertIn("corrupted", response["message"])

    def test_mock_tracking_does_not_write_real_store(self):
        with patch.object(app.config, "MOCK_MODE", True), \
             patch.object(app, "_mock_structure_store", app.outlook_store.OutlookStore()), \
             patch.object(app._structure_store, "analyze", side_effect=AssertionError("mock touched real store")):
            response = self.client.get("/api/outlook").json
            self.assertEqual(response["status"], "ok")

    def test_disconnected_and_stale_have_no_plan(self):
        app._state["connected"] = False
        response = self.client.get("/api/trade_plan").json
        self.assertEqual(response["status"], "error")
        self.assertNotIn("plan", response)
        app._state["connected"] = True
        app._outlook_data[(self.sym, 180)]["market_updated"] = time.time() - 21
        self.assertNotIn("plan", self.client.get("/api/trade_plan").json)

    def test_repeated_snapshots_do_not_refresh_market_or_enable_plan(self):
        self.store.update_settings({"fee_per_lot": 0}, self.sym)
        primary = copy.deepcopy(app._outlook_data[(self.sym, 1800)]["bars"])
        short = copy.deepcopy(app._outlook_data[(self.sym, 180)]["bars"])
        old_market = time.time() - 21
        for period in (180, 1800):
            app._outlook_data[(self.sym, period)]["market_updated"] = old_market
        app._cache_outlook_bars(self.sym, 1800, primary)
        app._cache_outlook_bars(self.sym, 180, short)
        self.assertEqual(app._outlook_data[(self.sym, 180)]["market_updated"], old_market)
        self.assertGreater(app._outlook_data[(self.sym, 180)]["updated"], old_market)
        response = self.client.get("/api/trade_plan").json
        self.assertEqual(response["status"], "error")
        self.assertIn("超过20秒未变动", response["message"])
        self.assertNotIn("plan", response)
        self.assertNotIn(self.sym, app._registered_prices())
        # 历史推演仍可看，只有实时计划与持仓现价暂停。
        self.assertEqual(self.client.get("/api/outlook?period=1800").json["status"], "ok")

    def test_only_actual_content_change_refreshes_its_symbol_and_period(self):
        other = app.symbols.PRESETS[1]["symbol"]
        now = time.time()
        app._outlook_data[(self.sym, 180)]["market_updated"] = now - 30
        app._outlook_data[(self.sym, 1800)]["market_updated"] = now - 30
        changed = copy.deepcopy(app._outlook_data[(self.sym, 180)]["bars"])
        changed[-1]["volume"] += 1
        app._cache_outlook_bars(other, 180, changed)
        self.assertEqual(app._outlook_data[(self.sym, 180)]["market_updated"], now - 30)
        app._cache_outlook_bars(self.sym, 180, changed)
        self.assertGreaterEqual(app._outlook_data[(self.sym, 180)]["market_updated"], now)
        self.assertEqual(app._outlook_data[(self.sym, 1800)]["market_updated"], now - 30)
        self.assertIn(self.sym, app._registered_prices())
        self.assertNotIn("plan", self.client.get("/api/trade_plan").json)

    def test_first_load_of_old_history_has_no_live_grace_period(self):
        app._outlook_data.clear()
        tz = timezone(timedelta(hours=8))
        now = datetime(2026, 10, 19, 12, 40, tzinfo=tz).timestamp()
        b = bars()
        s = small()[:-1] + [dict(small()[-1], close=112)]
        b[-1]["dt"], s[-1]["dt"] = "2026-10-19 11:00:00", "2026-10-19 11:27:00"
        with patch.object(app.time, "time", return_value=now):
            app._cache_outlook_bars(self.sym, 1800, b)
            app._cache_outlook_bars(self.sym, 180, s)
            self.assertEqual(app._outlook_data[(self.sym, 180)]["updated"], now)
            self.assertEqual(app._outlook_data[(self.sym, 180)]["market_updated"],
                             datetime(2026, 10, 19, 11, 30, tzinfo=tz).timestamp())
            self.assertNotIn("plan", self.client.get("/api/trade_plan").json)
            self.assertFalse(app._registered_prices())
            # 当前未完成棒的结束时间在未来时，时钟使用实际接收时刻。
            s[-1]["dt"] = "2026-10-19 12:39:00"
            app._cache_outlook_bars(self.sym, 180, s)
            self.assertEqual(app._outlook_data[(self.sym, 180)]["market_updated"], now)

    def test_existing_stop_breach_blocks_new_risk(self):
        self.store.update_settings({"fee_per_lot": 0}, self.sym)
        self.store.register({"direction": "long", "lots": 1, "entry": 120,
                             "stop": 115, "target": 140}, self.preset)
        response = self.client.get("/api/trade_plan").json
        self.assertEqual(response["plan"]["technical_status"], "candidate")
        self.assertEqual(response["plan"]["status"], "risk_blocked")
        self.assertEqual(response["risk"]["max_lots"], 0)
        self.assertTrue(response["positions"][0]["stop_breached"])
        self.assertFalse(any(r["allowed"] for r in response["risk"]["rows"]))

    def test_other_symbol_never_receives_focus_data(self):
        other = app.symbols.PRESETS[1]["symbol"]
        response = self.client.get("/api/trade_plan", query_string={"symbol": other}).json
        self.assertEqual((response["symbol"], response["status"]), (other, "loading"))
        app._extra_data[1800] = {"symbol": self.sym, "bars": bars(), "updated": time.time()}
        for route in ("/api/kline", "/api/double_bottom"):
            response = self.client.get(route, query_string={"symbol": other, "period": 1800}).json
            self.assertEqual((response["symbol"], response["status"]), (other, "loading"))
            self.assertNotIn("bars", response)
            self.assertNotIn("result", response)

    def test_publish_rejects_old_symbol_and_combo(self):
        app._publish_focus_result("UNKNOWN", app.config.get_combo("intraday30"), {"price": 5})
        self.assertIsNone(app.get_state()["result"])
        app._publish_focus_result(self.sym, {"key": "different"}, {"price": 5})
        self.assertIsNone(app.get_state()["result"])
        app._publish_focus_result(self.sym, app.config.get_combo("intraday30"), {"price": 6})
        self.assertEqual(app.get_state()["result"]["symbol"], self.sym)
        app._set_state(connected=False)
        self.assertIsNone(app.get_state()["result"])

    def test_invalid_settings_and_unknown_identity_preserve_defaults(self):
        for payload in ({"equity": -1}, {"max_loss": 60000}, {"slippage_ticks": -1},
                        {"fee_per_lot": "NaN"}, {"symbol": "UNKNOWN"}, []):
            self.assertEqual(self.client.post("/api/trading/settings", json=payload).status_code, 400)
        self.assertEqual(self.store.settings()["max_loss"], 1000)

    def test_manual_position_round_trip_and_budget_history(self):
        tick = self.preset["tick"]
        payload = {"symbol": self.sym, "direction": "long", "lots": 2,
                   "entry": 100 * tick, "stop": 90 * tick, "target": 130 * tick,
                   "mode": "early", "entry_fee": 4}
        response = self.client.post("/api/positions", json=payload)
        self.assertEqual(response.status_code, 200)
        position = response.json["positions"][0]
        self.assertEqual(self.client.post("/api/trading/idea", json={"symbol": self.sym}).status_code, 400)
        before = copy.deepcopy(self.store.data)
        bad = self.client.post(f"/api/positions/{position['id']}", json={"action": "close", "lots": .5, "price": 99 * tick})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.store.data, before)
        response = self.client.post(f"/api/positions/{position['id']}", json={"action": "close", "lots": 2, "price": 95 * tick, "fee": 4})
        self.assertTrue(response.json["ok"])
        self.assertFalse(response.json["positions"])
        self.assertEqual(response.json["stats"]["closed_trades"], 1)
        self.assertLess(self.store.idea(self.sym)["realized_pnl"], 0)
        self.assertEqual(self.client.post("/api/trading/idea", json={"symbol": self.sym}).status_code, 200)
        self.assertEqual(self.store.idea(self.sym)["realized_pnl"], 0)
        self.assertEqual(len(self.store.positions_view(self.sym)["history"]), 2)

    def test_stale_overview_has_no_candidate(self):
        app._overview[self.sym] = {"name": "test", "decision": {"quadrant": "confirm"}, "lamp": "red"}
        app._state["connected"] = False
        card = self.client.get("/api/overview").json["symbols"][0]
        self.assertEqual(card["decision"]["quadrant"], "standby")
        self.assertEqual(card["lamp"], "gray")

    def test_overview_stale_gate_checks_both_market_clocks(self):
        app._overview[self.sym] = {"name": "test", "decision": {"quadrant": "confirm"}, "lamp": "red"}
        for stale_period in (180, 1800):
            with self.subTest(stale_period=stale_period):
                for period in (180, 1800):
                    app._outlook_data[(self.sym, period)]["market_updated"] = time.time()
                app._outlook_data[(self.sym, stale_period)]["market_updated"] = time.time() - 21
                card = self.client.get("/api/overview").json["symbols"][0]
                self.assertEqual(card["decision"]["quadrant"], "standby")
                self.assertTrue(card["stale"])


if __name__ == "__main__":
    unittest.main()
