"""Offline regression tests: no SDK imports, account, or network needed."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest

from workspace_candles import aggregate_m30, TradingMinuteAggregator, TZ


DAY = [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]]


def schedule(end="23:00:00"):
    return {"day": deepcopy(DAY), "night": [["21:00:00", end]] if end else []}


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def rows(start, count, price=6000, minutes=3):
    start = dt(start)
    return [{"dt": (start + timedelta(minutes=minutes * index)).isoformat(),
             "open": price + index, "high": price + index + 2,
             "low": price + index - 2, "close": price + index + 1,
             "volume": index + 1, "oi": index - 3}
            for index in range(count)]


def full_day(day):
    return rows(day + "T09:00", 25) + rows(day + "T10:30", 20) + rows(day + "T13:30", 30)


def full_day_15(day):
    return rows(day + "T09:00", 5, minutes=15) + rows(day + "T10:30", 4, minutes=15) + rows(day + "T13:30", 6, minutes=15)


class TradingClockCandlesTests(unittest.TestCase):
    def test_cross_morning_break_and_lunch_without_reset(self):
        source = full_day("2026-11-12")
        result = aggregate_m30(source, schedule(), as_of="2026-11-12T15:00:00+08:00")
        self.assertEqual([r["dt"][11:16] for r in result], ["09:00", "09:30", "10:00", "10:45", "11:15", "13:45", "14:15", "14:45"])
        self.assertEqual(result[2]["end_time"][11:16], "10:45")
        self.assertEqual(result[4]["end_time"][11:16], "13:45")
        self.assertEqual(result[-1]["trading_minutes"], 15)
        self.assertTrue(all(r["closed"] and r["complete"] for r in result))

    def test_missing_source_never_shifts_later_boundaries(self):
        source = full_day("2026-11-12")
        missing = source.pop(21)
        result = aggregate_m30(source, schedule(), as_of="2026-11-12T15:00+08:00")
        damaged = result[2]
        self.assertEqual(damaged["dt"][11:16], "10:00")
        self.assertEqual(damaged["missing_times"], [missing["dt"]])
        self.assertEqual(damaged["observed_slots"], 9)
        self.assertFalse(damaged["closed"])
        self.assertFalse(damaged["complete"])
        self.assertEqual(damaged["quality"], "incomplete")
        self.assertEqual(result[3]["dt"][11:16], "10:45")
        self.assertEqual(result[3]["open"], rows("2026-11-12T10:30", 20)[5]["open"])

    def test_missing_whole_group_does_not_invent_prices(self):
        source = rows("2026-11-12T09:00", 10) + rows("2026-11-12T10:00", 5) + rows("2026-11-12T10:30", 5)
        result = aggregate_m30(source, schedule(), as_of="2026-11-12T10:45+08:00")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["unobserved_prior_groups"], 1)
        self.assertEqual(result[1]["dt"][11:16], "10:00")

    def test_latest_lunch_group_has_future_end_and_no_missing_slots(self):
        source = rows("2026-11-12T11:15", 5, 5996)
        result = aggregate_m30(source, schedule(), as_of="2026-11-12T11:45+08:00")
        self.assertEqual(len(result), 1)
        last = result[-1]
        self.assertEqual(last["dt"][11:16], "11:15")
        self.assertEqual(last["end_time"][11:16], "13:45")
        self.assertEqual((last["observed_slots"], last["pending_slots"], last["missing_slots"]), (5, 5, 0))
        self.assertFalse(last["closed"])
        self.assertFalse(last["complete"])
        self.assertEqual(last["quality"], "live")

    def test_initial_partial_group_dropped_or_explicitly_flagged(self):
        source = rows("2026-11-12T09:03", 19)
        result = aggregate_m30(source, schedule())
        self.assertEqual([row["dt"][11:16] for row in result], ["09:30"])
        retained = aggregate_m30(source, schedule(), drop_partial_start=False)
        self.assertTrue(retained[0]["truncated_start"])
        self.assertEqual(retained[0]["quality"], "incomplete")
        self.assertEqual(retained[0]["source_start"][11:16], "09:03")

    def test_open_high_low_close_volume_and_oi_aggregate_exactly(self):
        source = rows("2026-11-12T09:00", 10)
        result = aggregate_m30(source, schedule())[0]
        self.assertEqual([result[k] for k in ("open", "high", "low", "close", "volume", "oi")], [6000, 6011, 5998, 6010, 55, 15])
        self.assertTrue(result["complete"])
        self.assertFalse(result["closed"])
        source[-1]["closed"] = True
        self.assertTrue(aggregate_m30(source, schedule())[0]["closed"])

    def test_all_supported_night_endings_and_midnight(self):
        for endpoint, count in (("23:00:00", 40), ("25:00:00", 80), ("26:30:00", 110), ("01:00:00", 80), ("02:30:00", 110)):
            with self.subTest(endpoint=endpoint):
                source = rows("2026-11-12T21:00", count)
                result = aggregate_m30(source, schedule(endpoint), as_of=dt(source[-1]["dt"]) + timedelta(minutes=3))
                self.assertEqual(len(result), count // 10)
                self.assertTrue(all(row["source_trade_day"] == "2026-11-13" and row["closed"] for row in result))
                self.assertEqual(result[-1]["end_time"], (dt("2026-11-12T21:00") + timedelta(minutes=count * 3)).isoformat())

    def test_friday_night_saturday_early_belong_to_monday(self):
        source = rows("2026-11-13T21:00", 110) + full_day("2026-11-16")
        result = aggregate_m30(source, schedule("26:30:00"), as_of="2026-11-16T15:00+08:00")
        self.assertTrue(all(row["source_trade_day"] == "2026-11-16" for row in result))
        self.assertEqual(result[11]["dt"][11:16], "09:00")
        self.assertEqual(result[13]["dt"][11:16], "10:00")

    def test_reset_at_trade_day_not_continuous_across_two_days(self):
        source = full_day("2026-11-12") + rows("2026-11-12T21:00", 10)
        result = aggregate_m30(source, schedule(), as_of="2026-11-12T21:30+08:00")
        self.assertEqual(result[-2]["dt"][11:16], "14:45")
        self.assertEqual(result[-2]["expected_slots"], 5)
        self.assertEqual(result[-1]["dt"][11:16], "21:00")
        self.assertEqual(result[-1]["source_trade_day"], "2026-11-13")

    def test_day_only_products_use_same_day_clock(self):
        for product in ("DCE.jd", "CZCE.AP"):
            with self.subTest(product=product):
                result = aggregate_m30(full_day("2026-11-12"), schedule(None))
                self.assertEqual(result[2]["dt"][11:16], "10:00")
                self.assertEqual(result[4]["end_time"][11:16], "13:45")

    def test_calendar_maps_holiday_night_without_inventing_prices(self):
        source = rows("2026-09-30T21:00", 40) + full_day("2026-10-08")
        result = aggregate_m30(source, schedule(), as_of="2026-10-08T15:00+08:00", trading_days=["2026-09-30", "2026-10-08"])
        self.assertTrue(all(row["source_trade_day"] == "2026-10-08" for row in result))
        self.assertEqual(result[4]["dt"], "2026-10-08T09:00:00+08:00")
        self.assertTrue(all(row["calendar_quality"] == "provided" for row in result))

    def test_cancelled_holiday_night_not_synthesized(self):
        result = aggregate_m30(full_day("2026-10-08"), schedule(), as_of="2026-10-08T15:00+08:00", trading_days=["2026-09-30", "2026-10-08"])
        self.assertEqual(len(result), 8)
        self.assertTrue(all(row["dt"].startswith("2026-10-08") for row in result))
        self.assertTrue(all(row["complete"] for row in result))

    def test_invalid_rows_or_schedule_fail_instead_of_silent_omission(self):
        source = rows("2026-11-12T09:00", 2)
        for bad in ([source[0], source[0]], list(reversed(source)), rows("2026-11-12T09:01", 1), rows("2026-11-12T10:15", 1)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    aggregate_m30(bad, schedule())
        for update in ({"close": float("nan")}, {"high": 0}, {"volume": -1}, {"oi": True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                aggregate_m30([{**source[0], **update}], schedule())
        for bad in (None, {}, {"day": DAY, "night": None}, {"day": [["09:00", "10:16"]], "night": []}):
            with self.subTest(schedule=bad), self.assertRaises(ValueError):
                aggregate_m30(source, bad)

    def test_no_input_mutation_and_explicit_observation_watermark(self):
        source = rows("2026-11-12T09:00", 10)
        saved = deepcopy(source)
        settings = schedule()
        aggregate_m30(source, settings)
        self.assertEqual(source, saved)
        self.assertEqual(settings, schedule())
        with self.assertRaises(ValueError):
            aggregate_m30(source, settings, as_of="2026-11-12T09:00+08:00")

    def test_fifteen_minute_source_uses_same_clock_and_detects_gap(self):
        source = full_day_15("2026-11-12")
        result = aggregate_m30(source, schedule(), source_period=900, as_of="2026-11-12T15:00+08:00")
        self.assertEqual([r["dt"][11:16] for r in result], ["09:00", "09:30", "10:00", "10:45", "11:15", "13:45", "14:15", "14:45"])
        self.assertEqual(result[2]["end_time"][11:16], "10:45")
        self.assertEqual(result[4]["end_time"][11:16], "13:45")
        self.assertEqual(result[-1]["expected_slots"], 1)
        self.assertTrue(all(row["closed"] for row in result))
        source.pop(4)  # Missing 10:00 must not turn 10:30 into a new group start.
        damaged = aggregate_m30(source, schedule(), source_period=900, as_of="2026-11-12T15:00+08:00")
        self.assertEqual(damaged[2]["dt"][11:16], "10:00")
        self.assertEqual(damaged[2]["source_start"][11:16], "10:30")
        self.assertEqual(damaged[2]["missing_slots"], 1)
        self.assertEqual(damaged[3]["dt"][11:16], "10:45")

    def test_pta_shape_regression_without_private_fixture(self):
        values = [("09:00", 5988, 5940), ("09:15", 5940, 5978),
                  ("09:30", 5978, 5990), ("09:45", 5990, 5986),
                  ("10:00", 5986, 5996), ("10:30", 5998, 6000),
                  ("10:45", 6000, 5984), ("11:00", 5984, 5996),
                  ("11:15", 5996, 5994)]
        source = [{"dt": f"2026-11-12T{clock}:00+08:00", "open": opening,
                   "high": max(opening, close) + 2, "low": min(opening, close) - 2,
                   "close": close, "volume": 1, "oi": 0}
                  for clock, opening, close in values]
        source[-1].update(high=6010, low=5990)
        result = aggregate_m30(source, schedule(), source_period=900, as_of="2026-11-12T11:45+08:00")
        self.assertEqual([result[-1][key] for key in ("open", "high", "low", "close")], [5996, 6010, 5990, 5994])
        self.assertEqual(sum(row["close"] for row in result[-5:]) / 5, 5990.8)

    def test_pta_saved_snapshot_when_present(self):
        path = Path(__file__).parent / "backtest" / "pta-kline-incident-20261112.json"
        if not path.exists():
            self.skipTest("Private offline incident snapshot not available; synthetic regressions remain required")
        market = json.loads(path.read_text(encoding="utf-8"))["market"]
        result = aggregate_m30(market["periods"]["m3"], schedule(), as_of=market["as_of"], trading_days=[row["dt"][:10] for row in market["periods"]["day"]])
        last = result[-1]
        self.assertEqual([last[key] for key in ("open", "high", "low", "close")], [5996, 6010, 5990, 5994])
        self.assertEqual(sum(row["close"] for row in result[-5:]) / 5, 5990.8)
        self.assertEqual(last["dt"], "2026-11-12T11:15:00+08:00")
        self.assertEqual(last["end_time"], "2026-11-12T13:45:00+08:00")
        self.assertEqual(last["missing_slots"], 0)
        # Construct complete 15m clock bins from this saved source to verify both
        # input resolutions take the same grouping path. This is not evidence
        # about an independently fetched native 15m feed.
        groups = {}
        for row in market["periods"]["m3"]:
            stamp = dt(row["dt"])
            start = stamp.replace(minute=stamp.minute // 15 * 15, second=0)
            groups.setdefault(start, []).append(row)
        source15 = []
        for start, children in groups.items():
            if len(children) != 5 or dt(children[0]["dt"]) != start:
                continue
            source15.append({"dt": start.isoformat(), "open": children[0]["open"],
                             "high": max(row["high"] for row in children),
                             "low": min(row["low"] for row in children), "close": children[-1]["close"],
                             "volume": sum(row["volume"] for row in children),
                             "oi": sum(row["oi"] for row in children)})
        result15 = aggregate_m30(source15, schedule(), source_period=900, as_of=market["as_of"])
        compare = lambda data: [{key: row[key] for key in ("dt", "end_time", "open", "high", "low", "close")} for row in data[-30:]]
        self.assertEqual(compare(result15), compare(result))


class IncrementalTradingClockTests(unittest.TestCase):
    def test_same_day_tail_rebuild_matches_full_and_shares_older_days(self):
        source = full_day_15("2026-11-10") + full_day_15("2026-11-11") + rows("2026-11-12T09:00", 2, minutes=15)
        builder = TradingMinuteAggregator(schedule(), source_period=900)
        old = builder.replace(source)
        first_bar = old[0]
        payload = deepcopy(source[-2:])
        payload[-1].update(close=6001, high=6004)
        payload += rows("2026-11-12T09:30", 1, minutes=15)
        updated_source = source[:-2] + payload
        result = builder.update_tail(payload, appended=1)
        self.assertEqual(result, aggregate_m30(updated_source, schedule(), source_period=900))
        self.assertIs(result[0], first_bar)
        self.assertEqual(builder.last_rebuilt_days, ("2026-11-12",))
        self.assertEqual(builder.coverage["available_source_bars"], len(updated_source))
        self.assertFalse(builder.coverage["history_complete"])

    def test_cross_trading_day_rebuilds_old_tail_and_new_day(self):
        source = full_day_15("2026-11-12")
        builder = TradingMinuteAggregator(schedule(), source_period=900)
        builder.replace(source)
        payload = source[-2:] + rows("2026-11-12T21:00", 2, minutes=15)
        result = builder.update_tail(payload, appended=2)
        self.assertEqual(result, aggregate_m30(source + payload[2:], schedule(), source_period=900))
        self.assertEqual(builder.last_rebuilt_days, ("2026-11-12", "2026-11-13"))

    def test_retention_slides_first_day_without_shift_or_old_snapshot_mutation(self):
        source = full_day_15("2026-11-11") + rows("2026-11-12T09:00", 2, minutes=15)
        builder = TradingMinuteAggregator(schedule(), source_period=900, max_source_bars=len(source))
        old = builder.replace(source)
        saved = deepcopy(old)
        payload = source[-2:] + rows("2026-11-12T09:30", 1, minutes=15)
        result = builder.update_tail(payload, appended=1)
        rolled = (source + payload[2:])[1:]
        self.assertEqual(result, aggregate_m30(rolled, schedule(), source_period=900))
        self.assertEqual(old, saved)
        self.assertEqual(result[0]["dt"][11:16], "09:30")
        self.assertEqual(builder.coverage["dropped_initial_source_bars"], 1)
        self.assertEqual(builder.last_rebuilt_days, ("2026-11-11", "2026-11-12"))

    def test_removing_entire_first_day_keeps_full_reload_equivalence(self):
        source = full_day_15("2026-11-11")[-1:] + full_day_15("2026-11-12")
        builder = TradingMinuteAggregator(schedule(), source_period=900, max_source_bars=len(source))
        builder.replace(source)
        payload = source[-2:] + rows("2026-11-12T21:00", 1, minutes=15)
        actual = builder.update_tail(payload, appended=1)
        expected = aggregate_m30((source + payload[2:])[1:], schedule(), source_period=900)
        self.assertEqual(actual, expected)

    def test_invalid_increment_is_atomic_and_full_correction_supported(self):
        source = full_day_15("2026-11-12")
        builder = TradingMinuteAggregator(schedule(), source_period=900)
        builder.replace(source)
        saved = deepcopy(builder.bars)
        bad_inputs = [(source[-1:], 0), (source[-3:-1], 0), ([{**source[-2], "high": 1}, source[-1]], 0)]
        for payload, appended in bad_inputs:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                builder.update_tail(payload, appended=appended)
            self.assertEqual(builder.bars, saved)
        corrected = deepcopy(source)
        corrected[0]["high"] += 10
        self.assertEqual(builder.replace(corrected), aggregate_m30(corrected, schedule(), source_period=900))

    def test_tail_with_missing_new_slot_marks_gap_without_wrong_next_group(self):
        source = rows("2026-11-12T09:00", 2, minutes=15)
        builder = TradingMinuteAggregator(schedule(), source_period=900)
        builder.replace(source)
        payload = source[-2:] + rows("2026-11-12T09:45", 1, minutes=15)
        result = builder.update_tail(payload, appended=1)
        self.assertEqual(result[-1]["dt"][11:16], "09:30")
        self.assertEqual(result[-1]["missing_slots"], 1)
        self.assertEqual(result[-1]["quality"], "incomplete")

    def test_three_minute_increment_across_break_and_lunch(self):
        full = full_day("2026-11-12")
        builder = TradingMinuteAggregator(schedule(), source_period=180)
        builder.replace(full[:24])
        size = 24
        for append_count in (2, 10, 10, 29):
            added = full[size:size + append_count]
            if not added:
                break
            payload = full[size - 2:size] + added
            size += len(added)
            result = builder.update_tail(payload, appended=len(added))
            self.assertEqual(result, aggregate_m30(full[:size], schedule()))

    def test_fewer_than_two_initial_rows_and_empty_result_coverage(self):
        source = rows("2026-11-12T09:15", 1, minutes=15)
        builder = TradingMinuteAggregator(schedule(), source_period=900)
        self.assertEqual(builder.replace(source), [])
        self.assertEqual(builder.coverage["dropped_initial_source_bars"], 1)
        payload = source + rows("2026-11-12T09:30", 1, minutes=15)
        actual = builder.update_tail(payload, appended=1)
        self.assertEqual(actual, aggregate_m30(payload, schedule(), source_period=900))

    def test_missing_first_slot_of_later_day_is_gap_not_window_truncation(self):
        source = full_day_15("2026-11-12")
        builder = TradingMinuteAggregator(schedule(), source_period=900)
        builder.replace(source)
        new = rows("2026-11-12T21:15", 1, minutes=15)
        actual = builder.update_tail(source[-2:] + new, appended=1)
        expected = aggregate_m30(source + new, schedule(), source_period=900)
        self.assertEqual(actual, expected)
        self.assertFalse(actual[-1]["truncated_start"])
        self.assertEqual(actual[-1]["quality"], "incomplete")


if __name__ == "__main__":
    unittest.main()
