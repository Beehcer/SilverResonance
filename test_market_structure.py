"""Offline causal evidence tests for the independent chart overlay."""
import copy
import json
import math
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from market_structure import build_market_structure


def bar(close, open_=None, low=None, high=None):
    o = close if open_ is None else open_
    return {"open": o, "close": close, "high": max(o, close) + 1 if high is None else high,
            "low": min(o, close) - 1 if low is None else low}


def stamp(bars):
    result = [copy.deepcopy(b) for b in bars]
    for i, b in enumerate(result):
        b["dt"] = (datetime(2026, 10, 19, 1) + timedelta(minutes=30 * i)).isoformat(" ")
    return result


def history(extra=(), mirror=False):
    data = [bar(c) for c in [101, 105, 109, 105] * 5] + list(extra) + [bar(96)]
    if mirror:
        data = [{**b, "open": 210 - b["open"], "close": 210 - b["close"],
                 "high": 210 - b["low"], "low": 210 - b["high"]} for b in data]
    return stamp(data)


def reference(data, low=100, high=110, end=19):
    return {"low": low, "high": high, "kind": "range", "start_index": 0, "end_index": end,
            "start_time": data[0]["dt"], "end_time": data[end]["dt"], "buffer": 1, "atr": 4}


def analyze(data, ref=None, **kwargs):
    return build_market_structure(data, reference=ref if ref is not None else reference(data), **kwargs)


def sloped(extra=(), mirror=False):
    data = [bar(c) for c in [112, 113, 116, 119, 116, 113, 110, 111, 114, 117, 114, 111]]
    data += list(extra) + [bar(109)]
    if mirror:
        data = [{**b, "open": 220 - b["open"], "close": 220 - b["close"],
                 "high": 220 - b["low"], "low": 220 - b["high"]} for b in data]
    return stamp(data)


def slope_ref(data):
    return reference(data, low=50, high=170, end=11)


class MarketStructureTests(unittest.TestCase):
    def test_contract_json_pure_and_deterministic(self):
        data = history([bar(96)])
        ref = reference(data)
        saved = copy.deepcopy((data, ref))
        first = analyze(data, ref)
        self.assertEqual(first, analyze(data, ref))
        self.assertEqual((data, ref), saved)
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        self.assertEqual(first["decision_bar_time"], data[-2]["dt"])
        self.assertEqual(first["completed_index"], len(data) - 2)
        self.assertEqual(len(first["scenarios"]), 4)

    def test_unfinished_bar_keeps_completed_facts_but_revalidates_scenarios(self):
        data = history([bar(96), bar(96.2), bar(96.5), bar(96.1)])
        original = analyze(data)
        changed = copy.deepcopy(data)
        changed[-1].update(open=500, high=999, low=1, close=300)
        later = analyze(changed)
        for field in ("phase", "reference", "candidate", "pivots", "trend_lines", "events", "clues", "observation_zone", "atr", "buffer"):
            self.assertEqual(original[field], later[field], field)
        self.assertEqual(original["hierarchy"], later["hierarchy"])
        self.assertNotEqual(original["scenarios"], later["scenarios"])
        for s in later["scenarios"]:
            if s["drawable"]:
                self.assertEqual(s["path"][0], 300)
            else:
                self.assertEqual(s["path"], [])
        self.assertFalse(next(s for s in later["scenarios"] if s["role"] == "consolidation")["drawable"])

    def test_pivot_waits_for_two_completed_right_bars(self):
        data = sloped()
        full = analyze(data, slope_ref(data))
        self.assertTrue(any(p["index"] == 9 and p["kind"] == "high" for p in full["pivots"]))
        self.assertTrue(any(line["anchors"][-1]["index"] == 9 for line in full["trend_lines"]))
        short = copy.deepcopy(data[:-1])  # former final confirmation is now live.
        result = build_market_structure(short)
        self.assertEqual(result["status"], "insufficient")  # 11 completed, never force the 12th live to complete.
        longer = stamp([bar(111)] * 8 + [bar(c) for c in [112, 113, 116, 119, 116, 113, 110, 111, 114, 117, 114, 111]] + [bar(109)])
        before = build_market_structure(longer[:-1])
        after = build_market_structure(longer)
        self.assertFalse(any(p["index"] == 17 and p["kind"] == "high" for p in before["pivots"]))
        p = next(p for p in after["pivots"] if p["index"] == 17 and p["kind"] == "high")
        self.assertEqual(p["confirmed_index"], 19)
        self.assertEqual(p["confirmed_time"], longer[19]["dt"])

    def test_lines_and_breaks_only_begin_after_second_anchor_confirmation(self):
        data = sloped([bar(109), bar(110), bar(111), bar(112), bar(119)])
        result = analyze(data, slope_ref(data))
        line = next(x for x in result["trend_lines"] if x["kind"] == "resistance")
        self.assertEqual([p["index"] for p in line["anchors"]], [3, 9])
        self.assertEqual(line["confirmed_index"], 11)
        self.assertEqual(line["status"], "broken")
        self.assertEqual(line["break_index"], 16)
        self.assertEqual(line["end_index"], len(data) - 2)
        self.assertTrue(all(e["index"] > line["confirmed_index"] for e in result["events"] if e["line_id"] == line["id"]))
        self.assertIn("不自动确认反转", next(e["detail"] for e in result["events"] if e["kind"] == "line_break_up"))

    def test_line_retest_and_reclaim_are_long_short_symmetric(self):
        sequence = [bar(109), bar(110), bar(111), bar(112), bar(119), bar(118, open_=116, low=116, high=119)]
        for mirror, kind in ((False, "resistance"), (True, "support")):
            with self.subTest(mirror=mirror):
                data = sloped(sequence, mirror)
                result = analyze(data, slope_ref(data))
                line = next(x for x in result["trend_lines"] if x["kind"] == kind)
                self.assertEqual(line["status"], "retested")
                changed = sloped(sequence + [bar(112)], mirror)
                reclaimed = analyze(changed, slope_ref(changed))
                line2 = next(x for x in reclaimed["trend_lines"] if x["kind"] == kind)
                self.assertEqual(line2["id"], line["id"])
                self.assertEqual(line2["status"], "reclaimed")
                self.assertTrue(any(e["kind"] == "line_reclaimed" for e in reclaimed["events"]))

    def test_future_volatility_does_not_rewrite_old_line_buffer_or_break(self):
        sequence = [bar(109), bar(110), bar(111), bar(112), bar(119)]
        data = sloped(sequence)
        before = analyze(data, slope_ref(data))
        data2 = sloped(sequence + [bar(118, high=160, low=60)])
        after = analyze(data2, slope_ref(data2))
        old = next(x for x in before["trend_lines"] if x["kind"] == "resistance")
        new = next(x for x in after["trend_lines"] if x["kind"] == "resistance")
        self.assertEqual((old["id"], old["buffer"], old["break_index"]), (new["id"], new["buffer"], new["break_index"]))
        event = next(e for e in before["events"] if e["kind"] == "line_break_up")
        self.assertIn(event, after["events"])

    def test_live_crossing_is_separate_and_does_not_confirm_break(self):
        data = sloped([bar(109), bar(110), bar(111), bar(112)])
        before = analyze(data, slope_ref(data))
        data[-1]["close"] = 130
        after = analyze(data, slope_ref(data))
        for key in ("phase", "events", "trend_lines", "pivots", "clues"):
            self.assertEqual(before[key], after[key])
        self.assertIsNotNone(after["live_watch"])
        self.assertEqual(after["live_watch"]["direction"], "up")
        self.assertFalse(after["live_watch"]["confirmed"])
        self.assertIn("未收盘确认", after["live_watch"]["label"])

    def test_line_break_can_pause_inside_wide_frozen_reference(self):
        sequence = [bar(109), bar(110), bar(111), bar(112), bar(119)]
        for mirror, direction in ((False, "up"), (True, "down")):
            data = sloped(sequence + [bar(119.1), bar(119.3), bar(119.1)], mirror)
            result = analyze(data, slope_ref(data))
            self.assertEqual(result["phase"]["key"], "line_pause_" + direction)
            self.assertEqual(result["candidate"]["source"], "trend_line")
            self.assertEqual(result["candidate"]["bars"], 3)
            self.assertEqual((result["reference"]["low"], result["reference"]["high"]), (50, 170))
            self.assertTrue(any(e["kind"] == "line_no_follow_through" for e in result["events"]))
        fewer = sloped(sequence + [bar(119.1), bar(119.3)])
        self.assertIsNone(analyze(fewer, slope_ref(fewer))["candidate"])

    def test_intact_line_path_does_not_claim_a_break_already_happened(self):
        data = sloped()
        result = analyze(data, slope_ref(data))
        self.assertEqual(result["phase"]["key"], "line_intact_down")
        self.assertIn("越过局部线", result["scenarios"][2]["label"])
        self.assertNotIn("收回破位", result["scenarios"][2]["label"])

    def test_observation_zone_and_triggers_align_to_tick_and_remain_positive(self):
        data = sloped([bar(109), bar(110), bar(111), bar(112), bar(119)])
        for tick in (1, .5, .25):
            result = analyze(data, slope_ref(data), tick=tick)
            zone = result["observation_zone"]
            self.assertIsNotNone(zone)
            for value in (zone["low"], zone["high"]):
                self.assertGreater(value, 0)
                self.assertAlmostEqual(value / tick, round(value / tick))
            for scenario in result["scenarios"]:
                self.assertTrue(all(p > 0 and math.isfinite(p) for p in scenario["path"]))
        data[-1]["close"] = 0
        result = analyze(data, slope_ref(data))
        self.assertGreater(result["price"], 0)
        self.assertTrue(all(all(p > 0 for p in s["path"]) for s in result["scenarios"]))

    def test_one_or_two_stalled_bars_are_not_consolidation(self):
        for n in (0, 1, 2):
            data = history([bar(96)] + [bar(96.2)] * n)
            result = analyze(data)
            self.assertIsNone(result["candidate"])
            self.assertNotIn("pause", result["phase"]["key"])
            clue = [c for c in result["clues"] if c["key"] == "early_no_follow_through"]
            self.assertEqual(bool(clue), n > 0)
            if clue:
                self.assertEqual(clue[0]["label"], "暂未延续，整理证据不足")

    def test_three_overlapping_nonprogress_bars_make_only_candidate_symmetric(self):
        for mirror, direction in ((False, "down"), (True, "up")):
            data = history([bar(96), bar(96.2), bar(96.5), bar(96.1)], mirror)
            result = analyze(data)
            self.assertIsNotNone(result["candidate"])
            self.assertEqual(result["phase"]["key"], direction + "_pause")
            self.assertEqual(result["candidate"]["bars"], 3)
            self.assertIn("不确认震荡", result["candidate"]["reason"])
            self.assertEqual(result["reference"]["low"], 100)
            self.assertEqual(result["reference"]["high"], 110)
            self.assertEqual({s["direction"] for s in result["scenarios"]}, {"up", "down", "range"})

    def test_new_lows_or_nonoverlap_do_not_fake_no_follow_through(self):
        for extra in ([bar(96), bar(95), bar(93), bar(91)], [bar(96), bar(96), bar(98.5), bar(96)]):
            result = analyze(history(extra))
            self.assertIsNone(result["candidate"])
        continuation = analyze(history([bar(96), bar(92), bar(91)]))
        self.assertEqual(continuation["phase"]["key"], "down_continuation")

    def test_reference_break_retest_return_and_tick_thresholds(self):
        result = analyze(history([bar(96), bar(98), bar(97, open_=99, high=100, low=96)]))
        self.assertEqual(result["phase"]["key"], "down_retest")
        self.assertEqual((result["observation_zone"]["low"], result["observation_zone"]["high"]), (99, 101))
        returned = analyze(history([bar(96), bar(103)]))
        self.assertEqual(returned["phase"]["key"], "down_reclaimed")
        self.assertEqual(returned["phase"]["bias"], "neutral")
        data = history([bar(98)])
        ref = reference(data)
        ref["buffer"] = 3
        wide = analyze(data, ref, tick=3)
        self.assertFalse(any(e["kind"] == "down_break" for e in wide["events"]))
        self.assertTrue(any(e["kind"] == "down_break" for e in analyze(data)["events"]))

    def test_long_upper_wick_is_only_a_clue_and_live_wick_is_not_used(self):
        data = history([bar(105, open_=105, low=104, high=112)])
        result = analyze(data)
        clue = next(c for c in result["clues"] if c["key"] == "upper_wick")
        self.assertIn("不代表看空概率", clue["detail"])
        self.assertFalse(any(e["kind"] == "down_retest" for e in result["events"]))
        changed = history()
        changed[-1] = {**changed[-1], **bar(105, low=104, high=140)}
        self.assertFalse(any(c["key"] == "upper_wick" for c in analyze(changed)["clues"]))

    def test_gap_is_a_clue_and_paths_require_retest_not_chase(self):
        data = history([bar(96, open_=95), bar(96.1)])
        data[-3]["dt"] = "2026-10-20 21:00:00"
        data[-2]["dt"] = "2026-10-20 21:30:00"
        data[-1]["dt"] = "2026-10-20 22:00:00"
        result = analyze(data)
        self.assertTrue(any(c["key"] == "session_gap_down" for c in result["clues"]))
        down = next(s for s in result["scenarios"] if s["direction"] == "down")
        self.assertIn("低开", down["no_chase"])
        self.assertIn("不把跳空视为确认", down["no_chase"])
        self.assertIn("完成收盘", down["condition"])

    def test_targets_are_confirmed_historical_obstacles_or_none(self):
        result = analyze(history([bar(96)]))
        for s in result["scenarios"]:
            if s["target"] is not None:
                self.assertTrue(any(p["price"] == s["target"] for p in result["pivots"]))
            self.assertNotIn("概率", s["condition"])
            self.assertNotIn("%", s["condition"])
        down = next(s for s in result["scenarios"] if s["direction"] == "down")
        self.assertIsNone(down["target"])
        self.assertIn("缺少", down["target_basis"])

    def test_bad_input_and_time_cannot_silently_cross_gaps(self):
        for data in (None, [], history()[:10]):
            self.assertEqual(build_market_structure(data)["status"], "insufficient")
        for value in (None, float("nan"), -1, True):
            data = history()
            data[5]["high"] = value
            self.assertEqual(analyze(data)["status"], "insufficient")
        for key, value in (("completed", False), ("unfinished", True)):
            data = history()
            data[5][key] = value
            self.assertEqual(analyze(data)["status"], "insufficient")
        data = history()
        data[5]["dt"] = data[4]["dt"]
        self.assertEqual(analyze(data)["status"], "insufficient")
        self.assertEqual(build_market_structure(history(), tick=math.nan)["status"], "insufficient")

    def test_old_reference_uses_supplied_scale_and_future_reference_is_rejected(self):
        data = history([bar(96), bar(97)])
        ref = reference(data)
        ref.update(start_time="2026-10-01 09:00:00", end_time="2026-10-01 14:30:00", start_index=-1, end_index=-1)
        result = analyze(data, ref)
        self.assertEqual(result["buffer"], 1)
        self.assertEqual(result["reference"]["end_index"], -1)
        ref["end_time"] = "2027-10-01 14:30:00"
        self.assertEqual(analyze(data, ref)["status"], "insufficient")

    def test_indices_are_relative_to_original_input_after_lookback_crop(self):
        data = stamp([bar(c) for c in [101, 105, 109, 105] * 35] + [bar(96), bar(97)])
        ref = reference(data, end=139)
        result = analyze(data, ref)
        self.assertEqual(result["completed_index"], len(data) - 2)
        self.assertTrue(all(p["time"] == data[p["index"]]["dt"] for p in result["pivots"]))
        self.assertTrue(all(e["time"] == data[e["index"]]["dt"] for e in result["events"]))

    def test_no_candidate_has_no_invented_range_path(self):
        for data, ref in ((history([bar(96)]), None), (sloped(), slope_ref(sloped()))):
            result = analyze(data, ref)
            self.assertIsNone(result["candidate"])
            consolidation = next(s for s in result["scenarios"] if s["role"] == "consolidation")
            self.assertEqual(consolidation["path"], [])
            self.assertFalse(consolidation["drawable"])
            self.assertIsNone(consolidation["trigger"])
            self.assertIsNone(consolidation["target"])

    def test_candidate_only_draws_its_actual_completed_range_while_live_inside(self):
        data = history([bar(96), bar(96.2), bar(96.5), bar(96.1)])
        result = analyze(data)
        s = next(s for s in result["scenarios"] if s["role"] == "consolidation")
        self.assertTrue(s["drawable"])
        self.assertEqual(s["path"], [result["price"], result["candidate"]["low"], result["candidate"]["high"]])
        data[-1]["close"] = result["candidate"]["low"] - 2
        changed = analyze(data)
        self.assertEqual(result["candidate"], changed["candidate"])
        s = next(s for s in changed["scenarios"] if s["role"] == "consolidation")
        self.assertFalse(s["drawable"])
        self.assertEqual(s["status"], "invalid")

    def test_directional_scenarios_never_end_on_wrong_side_or_manufacture_target(self):
        for mirror in (False, True):
            data = sloped([bar(109), bar(110), bar(111), bar(112), bar(119)], mirror)
            ref = slope_ref(data)
            for live in (55, 80, 100, 118, 130, 165, 180):
                data[-1]["close"] = live
                result = analyze(data, ref)
                for s in result["scenarios"]:
                    if not s["drawable"]:
                        self.assertEqual(s["path"], [])
                        continue
                    self.assertEqual(s["path"][0], live)
                    if s["direction"] == "range":
                        continue
                    if s["direction"] == "up":
                        self.assertGreater(s["target"], max(live, s["trigger"]["price"], s["trigger"]["high"]))
                    else:
                        self.assertLess(s["target"], min(live, s["trigger"]["price"], s["trigger"]["low"]))
                    self.assertIn(s["target_source"]["source"], ("confirmed_swing", "frozen_reference", "local_reference"))
                    self.assertEqual(s["path"][-1], s["target"])

    def test_real_rebar_fixture_direction_hierarchy_and_live_premise_regression(self):
        path = Path(__file__).parent / "backtest" / "rebar-30m-ui-fixture.json"
        if not path.exists():
            self.skipTest("offline public rebar fixture is not installed")
        data = json.loads(path.read_text(encoding="utf-8"))["bars"]
        ref = {"low": 3053, "high": 3102, "kind": "reference", "start_index": 51, "end_index": 110, "atr": 7.5, "buffer": 2}
        data[-1]["close"] = 3068
        result = analyze(data, ref)
        self.assertEqual(result["decision_bar_time"], "2026-10-19 14:00:00")
        self.assertEqual(result["phase"]["scope"], "local")
        self.assertEqual(result["hierarchy"]["direction"], "neutral")
        self.assertIn("局部回升支撑被破", result["phase"]["label"])
        self.assertEqual(result["key_levels"]["current_support"]["price"], 3065)
        self.assertEqual([(x["price"], x["tier"]) for x in result["key_levels"]["supports"]], [(3065, "local"), (3061, "local"), (3053, "outer")])
        self.assertEqual(result["key_levels"]["outer_low"]["price"], 3053)
        self.assertEqual(result["key_levels"]["local_rebound"]["low"], 3072)
        direct = next(s for s in result["scenarios"] if s["role"] == "direct")
        self.assertEqual(direct["path"], [3068, 3065])
        self.assertEqual(result["selected_scenario"], direct["key"])
        retest = next(s for s in result["scenarios"] if s["role"] == "retest")
        self.assertEqual(retest["target"], 3065)
        self.assertLess(retest["target"], 3068)
        reclaim = next(s for s in result["scenarios"] if s["role"] == "reclaim")
        self.assertIn("参考带内", reclaim["label"])
        self.assertNotIn("反转", reclaim["label"])
        self.assertEqual(reclaim["target_source"]["tier"], "local")
        self.assertIsNone(result["candidate"])
        self.assertFalse(next(s for s in result["scenarios"] if s["role"] == "consolidation")["drawable"])
        for price in (3074, 3075, 3088, 3053, 3040):
            data[-1]["close"] = price
            changed = analyze(data, ref)
            for k in ("phase", "hierarchy", "pivots", "events", "trend_lines", "candidate", "observation_zone"):
                self.assertEqual(result[k], changed[k], k)
            if price in (3074, 3075):
                self.assertEqual(changed["selected_scenario"], "retest_down")
            elif price == 3088:
                self.assertEqual(changed["selected_scenario"], "reclaim_up")
                self.assertTrue(all(not s["drawable"] for s in changed["scenarios"] if s["role"] in ("retest", "direct")))
            elif price == 3053:
                self.assertEqual(changed["selected_scenario"], "direct_down")
                direct = next(s for s in changed["scenarios"] if s["role"] == "direct")
                self.assertEqual(direct["status"], "target_passed")
                self.assertFalse(direct["drawable"])
                self.assertEqual(direct["path"], [])
                self.assertNotEqual(changed["selected_scenario"], "reclaim_up")
            else:
                self.assertIsNone(changed["selected_scenario"])
                self.assertTrue(all(not s["drawable"] for s in changed["scenarios"]))


if __name__ == "__main__":
    unittest.main()
