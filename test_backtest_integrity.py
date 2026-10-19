"""Regression tests for demonstrated time, fill and position-accounting failures.

Offline only. Synthetic fixtures exercise execution, never establish profitability.
"""
import contextlib
import io
import unittest
from unittest.mock import patch

import backtest as bt
import panel_backtest as pb
import real_ubacktest as rb


PRESET = {"symbol": "TEST.X", "name": "test", "tick": 1, "tick_value": 10}


def bar(time, o=100, h=101, low=99, c=100, **extra):
    return {"dt": time, "open": o, "high": h, "low": low, "close": c,
            "volume": 10, "oi": 1, **extra}


def row(direction="long", stop=95, target=130):
    return {"quadrant": "main", "direction": direction, "res_dir": direction,
            "stop_price": stop, "regime": "trend", "score": 90,
            "ev_plan_t1": target, "neck": None, "l1": None, "group": "active"}


class BacktestIntegrityTests(unittest.TestCase):
    def test_15m_bar_is_unavailable_at_its_first_30s_close(self):
        ts = rb._ts("2026-10-19 11:00:00")
        values = [bar("2026-10-19 11:00:00", c=999, h=1000)]
        self.assertEqual(rb._tail_at([ts], values, ts + 30, 900), [])
        self.assertEqual(rb._tail_at([ts], values, ts + 899, 900), [])
        self.assertEqual(rb._tail_at([ts], values, ts + 900, 900), values)
        self.assertEqual(rb._tail_at([ts], values, ts + 30, 30), values)

    def test_aggregation_records_actual_last_subbar_end(self):
        values = [bar("2026-10-19 10:00:00"), bar("2026-10-19 10:30:00")]
        result = rb.agg_1h(values)
        self.assertEqual(result[0]["end_ts"], rb._ts("2026-10-19 10:45:00"))

    def test_top3_uses_previous_trading_day_and_ignores_same_day_future(self):
        presets = [{**PRESET, "symbol": name, "name": name} for name in "ABCD"]
        bars = [bar("2026-10-16 14:00:00"), bar("2026-10-19 09:00:00"),
                bar("2026-10-19 14:00:00")]
        data = {p["symbol"]: (bars, "fixture") for p in presets}
        signal = {"entry_date": "2026-10-19", "entry_time": "2026-10-19 10:00:00"}

        def run(future_scores):
            rows = [{0: {**row(), "score": previous},
                     1: row(), 2: {**row(), "score": future}}
                    for previous, future in zip([100, 90, 80, 0], future_scores)]
            with patch.object(pb, "precompute_downsampled", return_value={}), \
                    patch.object(pb, "precompute_rows", side_effect=rows), \
                    patch.object(pb, "replay_symbol", return_value=[signal]), \
                    patch.object(pb, "compute_trade_stats", return_value=None), \
                    patch.object(pb, "daily_stats", return_value=None), \
                    contextlib.redirect_stdout(io.StringIO()):
                return pb.scheme_b(data, presets, warmup=0)

        a, b = run([100, 90, 80, 0]), run([0, 90, 80, 100])
        self.assertEqual(a["daily_top3"], b["daily_top3"])
        self.assertEqual([p[0] for p in a["daily_top3"]["2026-10-19"]], list("ABC"))
        self.assertEqual(a["validation_status"], "synthetic_demo_only")

    def test_stop_precedes_close_based_partial_profit(self):
        bars = [bar("2026-10-19 09:00:00"),
                bar("2026-10-19 10:00:00", 100, 120, 90, 115),
                bar("2026-10-19 11:00:00", 115, 116, 114, 115)]
        trades = pb.replay_symbol(PRESET, bars, 0, "c6_half", {0: row()}, lots=2)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "stop")
        self.assertEqual(trades[0]["exit_price"], 95)
        self.assertEqual(trades[0]["R"], -1.4)
        self.assertEqual(trades[0]["exits"][0]["lots"], 2)

    def test_two_lot_partial_is_one_trade_with_correct_cost_and_weight(self):
        bars = [bar("2026-10-19 09:00:00"),
                bar("2026-10-19 10:00:00", 100, 107, 99, 106),
                bar("2026-10-19 11:00:00", 106, 107, 99, 101)]
        trades = pb.replay_symbol(PRESET, bars, 0, "c6_half", {0: row()}, lots=2)
        self.assertEqual(len(trades), 1)
        self.assertEqual([e["lots"] for e in trades[0]["exits"]], [1, 1])
        # First lot +6-2, second lot 0-2 = +2, initial two-lot risk = 10.
        self.assertAlmostEqual(trades[0]["R"], .2)
        self.assertAlmostEqual(trades[0]["weighted_R"], .2)
        self.assertEqual(pb.compute_trade_stats(trades)["n"], 1)

    def test_one_lot_cannot_be_split(self):
        bars = [bar("2026-10-19 09:00:00"),
                bar("2026-10-19 10:00:00", 100, 107, 99, 106),
                bar("2026-10-19 11:00:00", 106, 107, 99, 101)]
        trade = pb.replay_symbol(PRESET, bars, 0, "c6_half", {0: row()}, lots=1)[0]
        self.assertEqual(len(trade["exits"]), 1)
        self.assertEqual(trade["exits"][0]["lots"], 1)
        self.assertNotEqual(trade["exit_reason"], "c6_half_profit")
        with self.assertRaises(ValueError):
            pb.replay_symbol(PRESET, bars, 0, lots=1.5)

    def test_gap_stop_fills_at_available_open_both_directions(self):
        for direction, stop, target, gap, expected in (
                ("long", 95, 130, bar("2026-10-19 11:00:00", 90, 92, 89, 91), 90),
                ("short", 105, 70, bar("2026-10-19 11:00:00", 110, 112, 109, 111), 110)):
            bars = [bar("2026-10-19 09:00:00"), bar("2026-10-19 10:00:00"), gap]
            trade = pb.replay_symbol(PRESET, bars, 0, rows={0: row(direction, stop, target)})[0]
            self.assertEqual(trade["exit_price"], expected)
            self.assertEqual(trade["R"], -2.4)

    def test_backtest_entry_path_uses_shared_gap_fill(self):
        bars = [bar("2026-10-19 09:00:00"), bar("2026-10-19 10:00:00"),
                bar("2026-10-19 11:00:00", 90, 92, 89, 91)]
        ev = {"pattern": {}, "plan": {"target1": 130}}
        decision = {"quadrant": "main", "direction": "long", "stop": {"price": 95}}
        with patch.object(bt.double_bottom, "evaluate", return_value=ev), \
                patch.object(bt.double_bottom, "_atr", return_value=1), \
                patch.object(bt.entry_logic, "detect_regime", return_value={"regime": "trend"}), \
                patch.object(bt.entry_logic, "decide", return_value=decision):
            trade = bt.run_symbol(PRESET, bars, 0, 60)[0]
        self.assertEqual(trade["exit_price"], 90)

    def test_all_levels_are_on_contract_tick_grid(self):
        self.assertEqual(bt.aligned_levels(100, 97.5, 105.1, 1, 1), (100, 97, 106))
        self.assertEqual(bt.aligned_levels(100, 102.5, 94.9, -1, 1), (100, 103, 94))

    def test_friday_night_and_after_midnight_belong_to_monday_session(self):
        bars = [bar(t) for t in ["2026-10-16 21:00:00", "2026-10-17 00:00:00",
                                "2026-10-19 09:00:00", "2026-10-19 14:00:00",
                                "2026-10-19 21:00:00", "2026-10-20 09:00:00"]]
        labels = bt.trading_day_labels(bars)
        self.assertEqual(labels[:4], ["2026-10-19"] * 4)
        self.assertEqual(labels[4:], ["2026-10-20"] * 2)
        self.assertEqual(bt.day_close_indices(bars, labels), {3})

    def test_intraday_position_closes_before_next_night(self):
        bars = [bar("2026-10-19 09:00:00"), bar("2026-10-19 10:00:00"),
                bar("2026-10-19 14:00:00"), bar("2026-10-19 21:00:00")]
        trade = pb.replay_symbol(PRESET, bars, 0, rows={0: row()})[0]
        self.assertEqual(trade["exit_time"], "2026-10-19 14:00:00")
        self.assertEqual(trade["exit_reason"], "day_close")
        self.assertEqual(pb.replay_symbol(PRESET, bars, 0, rows={2: row()}), [])

    def test_confirmation_bar_cannot_stop_a_not_yet_open_position(self):
        bars = [bar("2026-10-19 09:00:00"),
                bar("2026-10-19 10:00:00", 90, 120, 80, 110, volume=20),
                bar("2026-10-19 11:00:00")]
        trades = pb.replay_user_symbol(PRESET, bars, 0, {0: row()}, need_conf=True)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_time"], "2026-10-19 11:00:00")
        self.assertEqual(trades[0]["exit_reason"], "end")


if __name__ == "__main__":
    unittest.main()
