"""Offline risk-budget and manual-ledger tests; no broker/account connectivity."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading import TradingStore, risk_for_plan


PRESET = {"symbol": "TEST.X2701", "tick": 1.0, "tick_value": 10.0}
PLAN = {"direction": "long", "entry": 100, "stop": 90, "target": 130}


class TradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="silver-trading-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.store = TradingStore(self.path)
        self.store.update_settings({"equity": 50000, "max_loss": 220,
                                    "slippage_ticks": 0, "fee_per_lot": 10}, PRESET["symbol"])

    def register(self, lots=1, fee=4, **changes):
        payload = {**PLAN, "lots": lots, **changes}
        if fee is not None:
            payload["entry_fee"] = fee
        return self.store.register(payload, PRESET)

    def risk(self):
        return risk_for_plan(PLAN, self.store.settings(), PRESET,
                             self.store.open_positions(), self.store.idea(PRESET["symbol"]))

    def test_defaults_are_fifty_thousand_equity_and_one_thousand_loss(self):
        empty = TradingStore(Path(self.temp.name) / "new.json")
        self.assertEqual(empty.settings()["equity"], 50000)
        self.assertEqual(empty.settings()["max_loss"], 1000)

    def test_recorded_entry_fee_is_not_reserved_a_second_time(self):
        self.register()
        risk = self.risk()
        # Existing lot: stop100 + remaining fee6 + paid fee4. New lot: 100+10.
        self.assertEqual(risk["open_risk"], 106)
        self.assertEqual(risk["realized_loss"], 4)
        self.assertEqual(risk["remaining_budget"], 110)
        self.assertEqual(risk["rows"][0]["total_loss"], 220)
        self.assertTrue(risk["rows"][0]["allowed"])
        self.assertEqual(risk["max_lots"], 1)
        view = self.store.positions_view()
        self.assertEqual(view["positions"][0]["remaining_risk"], 106)

    def test_unrecorded_entry_fee_retains_full_round_trip_estimate(self):
        self.register(fee=None)
        risk = self.risk()
        self.assertEqual(risk["open_risk"], 110)
        self.assertEqual(risk["realized_loss"], 0)
        self.assertEqual(risk["rows"][0]["total_loss"], 220)
        self.assertFalse(self.store.open_positions()[0]["entry_fee_recorded"])

    def test_partial_close_allocates_entry_fee_by_initial_not_remaining_lots(self):
        position = self.register(lots=2, fee=8)
        self.assertEqual(self.risk()["open_risk"] + self.risk()["realized_loss"], 220)
        self.store.update_position(position["id"], {"action": "close", "lots": 1,
                                                    "price": 100, "fee": 3})
        risk = self.risk()
        self.assertEqual(risk["open_risk"], 106)
        self.assertEqual(risk["realized_loss"], 11)
        self.assertEqual(self.store.open_positions()[0]["initial_lots"], 2)

    def test_missing_partial_close_fee_does_not_forget_recorded_entry_fee(self):
        position = self.register(lots=2, fee=8)
        self.store.update_position(position["id"], {"action": "close", "lots": 1, "price": 100})
        remaining = self.store.open_positions()[0]
        self.assertFalse(remaining["fees_recorded"])
        self.assertTrue(remaining["entry_fee_recorded"])
        self.assertEqual(self.risk()["open_risk"], 106)

    def test_manual_partial_fills_count_as_one_closed_trade_and_pay_fees_once(self):
        position = self.register(lots=2, fee=8)
        self.store.update_position(position["id"], {"action": "close", "lots": 1,
                                                    "price": 100, "fee": 3})
        self.assertEqual(self.store.positions_view()["stats"]["closed_trades"], 0)
        self.store.update_position(position["id"], {"action": "close", "lots": 1,
                                                    "price": 102, "fee": 3})
        stats = self.store.positions_view()["stats"]
        self.assertEqual(stats["closed_trades"], 1)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["realized_pnl"], 6)  # +20 gross -8 entry -3 -3 exit.
        self.assertEqual(self.store.idea(PRESET["symbol"])["realized_pnl"], 6)

    def test_remaining_fee_never_becomes_negative(self):
        self.register(fee=15)
        self.assertEqual(self.risk()["open_risk"], 100)
        self.assertEqual(self.risk()["realized_loss"], 15)

    def test_unknown_fee_blocks_new_plan_lot_recommendation(self):
        self.store.update_settings({"fee_per_lot": None}, PRESET["symbol"])
        self.assertFalse(self.risk()["fee_configured"])
        self.assertEqual(self.risk()["max_lots"], 0)

    def test_closed_missing_fees_cannot_create_retry_budget(self):
        self.store.update_settings({"max_loss": 215})
        position = self.register(fee=None)
        self.store.update_position(position["id"], {"action": "close", "price": 90})
        idea = self.store.idea(PRESET["symbol"])
        self.assertEqual(idea["realized_pnl"], -100)  # actual ledger stays untouched
        self.assertEqual(idea["unrecorded_fee_reserve"], 10)
        risk = self.risk()
        self.assertEqual(risk["recorded_realized_loss"], 100)
        self.assertEqual(risk["realized_loss"], 110)
        self.assertEqual(risk["rows"][0]["total_loss"], 220)
        self.assertEqual(risk["max_lots"], 0)
        self.assertEqual(self.store.positions_view()["stats"]["realized_pnl"], -100)

    def test_missing_close_fee_reserves_only_unrecorded_part(self):
        position = self.register(fee=4)
        self.store.update_position(position["id"], {"action": "close", "price": 90})
        self.assertEqual(self.store.idea(PRESET["symbol"])["realized_pnl"], -104)
        self.assertEqual(self.risk()["unrecorded_fee_reserve"], 6)
        self.assertEqual(self.risk()["realized_loss"], 110)

    def test_missing_entry_fee_deducts_actual_close_fee_from_reserve(self):
        position = self.register(fee=None)
        self.store.update_position(position["id"], {"action": "close", "price": 90, "fee": 3})
        self.assertEqual(self.store.idea(PRESET["symbol"])["realized_pnl"], -103)
        self.assertEqual(self.risk()["unrecorded_fee_reserve"], 7)
        self.assertEqual(self.risk()["realized_loss"], 110)

    def test_complete_actual_fees_override_estimate_without_extra_reserve(self):
        position = self.register(fee=4)
        self.store.update_position(position["id"], {"action": "close", "price": 90, "fee": 3})
        self.assertEqual(self.risk()["unrecorded_fee_reserve"], 0)
        self.assertEqual(self.risk()["realized_loss"], 107)

    def test_mixed_partial_close_fees_only_reserve_missing_lot(self):
        position = self.register(lots=2, fee=8)
        self.store.update_position(position["id"], {"action": "close", "lots": 1, "price": 100, "fee": 3})
        self.store.update_position(position["id"], {"action": "close", "lots": 1, "price": 100})
        self.assertEqual(self.store.idea(PRESET["symbol"])["realized_pnl"], -11)
        self.assertEqual(self.risk()["unrecorded_fee_reserve"], 6)
        self.assertEqual(self.risk()["realized_loss"], 17)

    def test_high_actual_entry_fee_never_creates_negative_closed_fee_reserve(self):
        position = self.register(fee=15)
        self.store.update_position(position["id"], {"action": "close", "price": 90})
        self.assertEqual(self.risk()["unrecorded_fee_reserve"], 0)
        self.assertEqual(self.risk()["realized_loss"], 115)

    def test_failed_atomic_replace_restores_memory_and_original_file(self):
        before = copy.deepcopy(self.store.data)
        disk_before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.register()
        self.assertEqual(self.store.data, before)
        self.assertEqual(self.path.read_bytes(), disk_before)
        self.assertEqual(TradingStore(self.path).open_positions(), [])
        # The same user action can safely be retried once.
        self.register()
        self.assertEqual(len(self.store.open_positions()), 1)

    def test_failed_partial_close_rolls_back_lots_pnl_and_history(self):
        position = self.register(lots=2, fee=8)
        before = copy.deepcopy(self.store.data)
        with patch.object(self.store, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update_position(position["id"], {"action": "close", "lots": 1,
                                                            "price": 102, "fee": 3})
        self.assertEqual(self.store.data, before)
        self.assertEqual(self.store.open_positions()[0]["lots"], 2)

    def test_all_other_mutations_roll_back_on_save_failure(self):
        position = self.register()
        actions = [
            lambda: self.store.update_settings({"max_loss": 300}),
            lambda: self.store.new_idea("TEST.Y2701"),
            lambda: self.store.update_position(position["id"], {"action": "update_stop", "stop": 95}),
            lambda: self.store.record_signal(PRESET["symbol"], {**PLAN, "mode": "confirmed", "status": "ready"},
                                            {"max_lots": 1}, "2026-10-19 10:00:00"),
        ]
        for action in actions:
            before = copy.deepcopy(self.store.data)
            with patch.object(self.store, "_save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    action()
            self.assertEqual(self.store.data, before)

    def test_corrupt_file_cannot_create_ghost_positions(self):
        bad_path = Path(self.temp.name) / "corrupt.json"
        bad_path.write_text("{", encoding="utf-8")
        bad = TradingStore(bad_path)
        before = copy.deepcopy(bad.data)
        with self.assertRaises(ValueError):
            bad.register({**PLAN, "lots": 1, "entry_fee": 4}, PRESET)
        self.assertEqual(bad.data, before)
        self.assertEqual(bad.open_positions(), [])
        self.assertEqual(bad_path.read_text(encoding="utf-8"), "{")

    def test_restart_preserves_entry_fee_allocation_and_integer_lots(self):
        position = self.register(lots=2, fee=8)
        self.store.update_position(position["id"], {"action": "close", "lots": 1, "price": 100})
        reopened = TradingStore(self.path)
        risk = risk_for_plan(PLAN, reopened.settings(), PRESET, reopened.open_positions(),
                             reopened.idea(PRESET["symbol"]))
        self.assertEqual(risk["open_risk"], 106)
        self.assertEqual(reopened.open_positions()[0]["lots"], 1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["positions"][0]["initial_lots"], 2)


if __name__ == "__main__":
    unittest.main()
