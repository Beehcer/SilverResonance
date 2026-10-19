"""Scenario geometry regressions from the 3068 chart, plus causal live/mirror tests.

The 32-bar OHLC excerpt is embedded and fixed: tests do not read application
state, contact a quote provider, or use later prices to assert profitability.
"""
import copy
import json
import math
import unittest
from datetime import datetime, timedelta

from market_structure import build_market_structure


ROWS = [
    ("2026-10-15 09:00:00", 3058, 3064, 3053, 3059),
    ("2026-10-15 09:30:00", 3059, 3062, 3055, 3060),
    ("2026-10-15 10:00:00", 3060, 3066, 3056, 3063),
    ("2026-10-15 10:30:00", 3063, 3069, 3060, 3067),
    ("2026-10-15 11:00:00", 3067, 3068, 3061, 3065),
    ("2026-10-15 13:30:00", 3065, 3084, 3063, 3074),
    ("2026-10-15 14:00:00", 3074, 3077, 3069, 3072),
    ("2026-10-15 14:30:00", 3072, 3076, 3068, 3071),
    ("2026-10-15 21:00:00", 3070, 3074, 3061, 3065),
    ("2026-10-15 21:30:00", 3065, 3077, 3065, 3071),
    ("2026-10-15 22:00:00", 3071, 3075, 3068, 3073),
    ("2026-10-15 22:30:00", 3073, 3075, 3071, 3073),
    ("2026-10-16 09:00:00", 3073, 3078, 3068, 3069),
    ("2026-10-16 09:30:00", 3069, 3072, 3065, 3066),
    ("2026-10-16 10:00:00", 3066, 3071, 3065, 3070),
    ("2026-10-16 10:30:00", 3070, 3079, 3070, 3079),
    ("2026-10-16 11:00:00", 3079, 3083, 3078, 3080),
    ("2026-10-16 13:30:00", 3079, 3080, 3074, 3075),
    ("2026-10-16 14:00:00", 3075, 3076, 3071, 3073),
    ("2026-10-16 14:30:00", 3073, 3076, 3072, 3074),
    ("2026-10-16 21:00:00", 3069, 3082, 3069, 3078),
    ("2026-10-16 21:30:00", 3078, 3081, 3074, 3080),
    ("2026-10-16 22:00:00", 3080, 3088, 3078, 3086),
    ("2026-10-16 22:30:00", 3086, 3088, 3084, 3086),
    ("2026-10-19 09:00:00", 3087, 3094, 3082, 3091),
    ("2026-10-19 09:30:00", 3091, 3096, 3088, 3089),
    ("2026-10-19 10:00:00", 3089, 3090, 3082, 3084),
    ("2026-10-19 10:30:00", 3085, 3085, 3080, 3081),
    ("2026-10-19 11:00:00", 3081, 3085, 3081, 3081),
    ("2026-10-19 13:30:00", 3082, 3082, 3073, 3074),
    ("2026-10-19 14:00:00", 3074, 3077, 3071, 3072),
    ("2026-10-19 14:30:00", 3072, 3073, 3066, 3068),
]
MIRROR = 6200


def fixture(price=3068, mirror=False):
    bars = [dict(zip(("dt", "open", "high", "low", "close"), row)) for row in ROWS]
    bars[-1].update(close=price, high=max(bars[-1]["high"], price), low=min(bars[-1]["low"], price))
    ref = {"low": 3053, "high": 3102, "kind": "reference",
           "start_time": "2026-10-09 22:30:00", "end_time": "2026-10-16 22:00:00"}
    if mirror:
        bars = [dict(b, open=MIRROR-b["open"], close=MIRROR-b["close"],
                     high=MIRROR-b["low"], low=MIRROR-b["high"]) for b in bars]
        ref.update(low=MIRROR-3102, high=MIRROR-3053)
    return bars, ref


def analyze(price=3068, mirror=False, tick=1):
    bars, ref = fixture(price, mirror)
    return build_market_structure(bars, tick=tick, period=1800, reference=ref)


class ScenarioGeometryTests(unittest.TestCase):
    def assert_geometry(self, result):
        current = result["price"]
        for scenario in result["scenarios"]:
            with self.subTest(key=scenario["key"], price=current):
                self.assertIn("drawable", scenario)
                path = scenario["path"]
                if not scenario["drawable"]:
                    self.assertEqual(path, [])
                    continue
                self.assertGreaterEqual(len(path), 2)
                self.assertEqual(path[0], current)
                self.assertTrue(all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0 for p in path))
                direction = scenario["direction"]
                if direction not in ("up", "down"):
                    continue
                target = scenario["target"]
                self.assertIsNotNone(target, "directional drawings need a real reference beyond their trigger")
                trigger = (scenario.get("trigger") or {}).get("price", current)
                if direction == "down":
                    self.assertLess(target, min(current, trigger))
                    self.assertLess(path[-1], current)
                else:
                    self.assertGreater(target, max(current, trigger))
                    self.assertGreater(path[-1], current)

    def test_fixture_reproduces_local_broken_support_inside_outer_reference(self):
        result = analyze()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["price"], 3068)
        self.assertEqual(result["phase"]["direction"], "down")
        support = next(line for line in result["trend_lines"] if line["kind"] == "support")
        self.assertEqual([p["price"] for p in support["anchors"]], [3065, 3069])
        self.assertAlmostEqual(support["current_price"], 3074.71428571)
        self.assertEqual(support["status"], "broken")
        self.assertEqual(support["break_time"], "2026-10-19 14:00:00")
        self.assertEqual((result["reference"]["low"], result["reference"]["high"]), (3053, 3102))
        self.assertIsNone(result["candidate"])

    def test_3069_is_not_a_bearish_target_after_price_has_reached_3068(self):
        result = analyze()
        bearish = [s for s in result["scenarios"] if s["direction"] == "down"]
        self.assertTrue(bearish)
        for scenario in bearish:
            if scenario["target"] is not None:
                self.assertLess(scenario["target"], 3068)
                self.assertNotEqual(scenario["target"], 3069)
        self.assert_geometry(result)

    def test_live_prices_on_both_sides_never_draw_targets_behind_current_price(self):
        for mirror in (False, True):
            for price in (3040, 3053, 3065, 3068, 3074, 3078, 3088, 3102, 3120):
                with self.subTest(price=price, mirror=mirror):
                    self.assert_geometry(analyze(price, mirror))

    def test_no_candidate_means_no_invented_consolidation_zigzag(self):
        result = analyze()
        self.assertIsNone(result["candidate"])
        consolidation = [s for s in result["scenarios"] if s.get("role") == "consolidation"]
        self.assertTrue(consolidation, "unproven consolidation should be an explicit text condition")
        for scenario in consolidation:
            self.assertFalse(scenario["drawable"])
            self.assertEqual(scenario["path"], [])
            self.assertNotIn("observing", scenario["status"])

    def test_direct_downside_is_visible_as_observation_and_never_entry_permission(self):
        result = analyze()
        direct = next(s for s in result["scenarios"] if s.get("role") == "direct" and s["direction"] == "down")
        self.assertTrue(direct["no_chase"])
        if direct["drawable"]:
            self.assertEqual(direct["path"][0], 3068)
            self.assertLess(direct["path"][-1], 3068)
            self.assertTrue(all(p <= 3068 for p in direct["path"]))
        for scenario in result["scenarios"]:
            self.assertNotIn(scenario["status"], ("ready", "entry", "buy", "sell"))

    def test_outer_edges_remain_separate_from_local_support_and_reclaim(self):
        result = analyze()
        levels = result["key_levels"]
        self.assertEqual(levels["outer_low"]["price"], 3053)
        self.assertEqual(levels["outer_high"]["price"], 3102)
        self.assertEqual(levels["outer_low"]["tier"], "outer")
        self.assertEqual(levels["outer_high"]["tier"], "outer")
        self.assertEqual(levels["current_support"]["tier"], "local")
        self.assertLess(levels["current_support"]["price"], 3068)
        local = next(s for s in result["scenarios"] if s.get("role") == "reclaim")
        self.assertEqual(local["scope"], "local")
        self.assertLess(local["trigger"]["price"], levels["outer_high"]["price"])
        self.assertNotIn("反转成立", local["label"])
        self.assertNotIn("大反转", local["label"])

    def test_live_crossing_changes_possible_paths_but_not_completed_facts(self):
        before = analyze()
        changed = analyze(3088)
        for field in ("phase", "reference", "candidate", "pivots", "trend_lines", "events", "clues", "decision_bar_time"):
            self.assertEqual(before[field], changed[field], field)
        old = {s["key"]: s for s in before["scenarios"]}
        new = {s["key"]: s for s in changed["scenarios"]}
        self.assertTrue(any(old[k].get("status") != new[k].get("status") or
                            old[k]["path"][1:] != new[k]["path"][1:] for k in old.keys() & new.keys()))
        invalid = [s for s in changed["scenarios"] if s["status"] in ("invalid", "target_passed", "no_target")]
        self.assertTrue(invalid)
        self.assertTrue(all(not s["drawable"] and not s["path"] for s in invalid))
        self.assert_geometry(changed)

    def test_entering_the_observation_band_defaults_to_retest_not_direct_extension(self):
        for mirror, direction in ((False, "down"), (True, "up")):
            result = analyze(3074, mirror)
            self.assertEqual(result["selected_scenario"], "retest_" + direction)
            selected = next(s for s in result["scenarios"] if s["key"] == result["selected_scenario"])
            self.assertEqual(selected["status"], "observing")

    def test_exhausted_same_direction_reference_does_not_default_to_opposite_bounce(self):
        for mirror, direction in ((False, "down"), (True, "up")):
            result = analyze(3053, mirror)
            self.assertEqual(result["selected_scenario"], "direct_" + direction)
            selected = next(s for s in result["scenarios"] if s["key"] == result["selected_scenario"])
            self.assertIn(selected["status"], ("target_passed", "no_target"))
            self.assertFalse(selected["drawable"])
            self.assertEqual(selected["path"], [])

    def test_directional_mirror_preserves_targets_and_path_geometry(self):
        down, up = analyze(), analyze(mirror=True)
        self.assertEqual((down["phase"]["direction"], up["phase"]["direction"]), ("down", "up"))
        opposite = {"down": "up", "up": "down", "range": "range", "neutral": "neutral"}
        for scenario in down["scenarios"]:
            mirrored = next(s for s in up["scenarios"] if s.get("role") == scenario.get("role") and
                            s["direction"] == opposite[scenario["direction"]])
            self.assertEqual(scenario["drawable"], mirrored["drawable"])
            self.assertEqual(scenario["status"], mirrored["status"])
            self.assertEqual(len(scenario["path"]), len(mirrored["path"]))
            for a, b in zip(scenario["path"], mirrored["path"]):
                self.assertAlmostEqual(a + b, MIRROR)
            if scenario["target"] is not None:
                self.assertAlmostEqual(scenario["target"] + mirrored["target"], MIRROR)

    def test_targets_are_existing_confirmed_prices_and_align_to_tick(self):
        for tick in (1, .5, .25):
            result = analyze(tick=tick)
            known = {p["price"] for p in result["pivots"]} | {3053, 3102}
            for scenario in result["scenarios"]:
                target = scenario["target"]
                if target is None:
                    continue
                self.assertIn(target, known)
                self.assertEqual(scenario["target_source"]["price"], target)
                self.assertIn(scenario["target_source"]["tier"], ("local", "outer"))
                self.assertAlmostEqual(target / tick, round(target / tick))
                trigger = (scenario.get("trigger") or {}).get("price")
                if trigger is not None:
                    self.assertAlmostEqual(trigger / tick, round(trigger / tick))

    def test_real_candidate_can_draw_only_its_observed_band(self):
        # Separate synthetic case: an outer break followed by three genuinely
        # overlapping completed bars. It does not assume a new local pivot is absent.
        start = datetime(2026, 10, 19, 1)
        closes = [101, 105, 109, 105] * 5 + [96, 96.5, 96, 96.5, 96]
        bars = [{"dt": (start + timedelta(minutes=30*i)).isoformat(" "),
                 "open": close, "high": close + 1, "low": close - 1, "close": close}
                for i, close in enumerate(closes)]
        ref = {"low": 100, "high": 110, "kind": "range", "start_time": bars[0]["dt"],
               "end_time": bars[19]["dt"], "atr": 4, "buffer": 1}
        result = build_market_structure(bars, tick=.5, period=1800, reference=ref)
        self.assertIsNotNone(result["candidate"])
        scenario = next(s for s in result["scenarios"] if s.get("role") == "consolidation")
        self.assertTrue(scenario["drawable"])
        self.assertEqual(scenario["path"][0], result["price"])
        for point in scenario["path"][1:]:
            self.assertGreaterEqual(point, result["candidate"]["low"])
            self.assertLessEqual(point, result["candidate"]["high"])

    def test_computation_is_pure_deterministic_and_json_safe(self):
        bars, reference = fixture()
        original = copy.deepcopy((bars, reference))
        first = build_market_structure(bars, reference=reference)
        second = build_market_structure(bars, reference=reference)
        self.assertEqual((bars, reference), original)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)

    def test_continuations_are_conditional_sourced_levels_in_both_directions(self):
        for mirror in (False, True):
            result = analyze(3068, mirror)
            branch = next(s for s in result['scenarios'] if s['role'] == 'direct')
            expected = [3061, 3053] if not mirror else [MIRROR - 3061, MIRROR - 3053]
            self.assertEqual([step['price'] for step in branch['continuations']], expected)
            previous = branch['target']
            for step in branch['continuations']:
                self.assertEqual(step['from'], previous)
                self.assertEqual(step['source']['price'], step['price'])
                self.assertFalse(step['confirmed'])
                self.assertIn('完成收盘', step['condition'])
                if mirror:
                    self.assertLess(previous, step['trigger'])
                    self.assertLess(step['trigger'], step['price'])
                else:
                    self.assertGreater(previous, step['trigger'])
                    self.assertGreater(step['trigger'], step['price'])
                previous = step['price']
            self.assertIn('不直接判定', branch['at_reference'])

    def test_withdrawn_paths_cannot_keep_secondary_targets(self):
        for p in (3040, 3053, 3088, 3120):
            for branch in analyze(p)['scenarios']:
                if not branch['drawable']:
                    self.assertFalse(branch.get('continuations'))


if __name__ == "__main__":
    unittest.main()
