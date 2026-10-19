import copy
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

import outlook
from trade_plan import build_plan


def bar(close=105, low=None, high=None, open_=None, dt=None):
    return {"open": close if open_ is None else open_, "close": close,
            "low": close - 0.5 if low is None else low,
            "high": close + 0.5 if high is None else high, "dt": dt,
            "volume": 100, "oi": 1}


def bars(tail=None, older_target=True, live=112):
    history = [bar(c) for c in [100.5, 103, 107, 109.5, 107, 103] * 10]
    prefix = [bar() for _ in range(40)] if older_target else []
    if prefix:
        prefix[20] = bar(105, high=130)
        prefix[25] = bar(105, low=80)
    tail = tail or [bar() for _ in range(6)] + [bar(113), bar(112, low=110)]
    out = prefix + history + tail + [bar(live)]
    for i, b in enumerate(out):
        b["dt"] = f"bar-{i}"
    return out


def small(direction="long"):
    return [bar(100 + i if direction == "long" else 120 - i) for i in range(12)]


def tracked_fixture():
    source = bars()
    start = datetime(2026, 10, 1, 9)
    for i, item in enumerate(source):
        item["dt"] = (start + timedelta(minutes=30 * i)).isoformat(sep=" ")
    analysis = outlook.build_outlook(source, tick=1, period=1800)
    reference = analysis["range"]
    reference["start_time"] = source[reference["start_index"]]["dt"]
    reference["end_time"] = source[reference["end_index"]]["dt"]
    for pivot in analysis["pivots"]:
        pivot["time"] = source[pivot["index"]]["dt"]
        pivot["confirmed_time"] = source[pivot["index"] + outlook.PIVOT_CONFIRM]["dt"]
    return source, analysis


class TradePlanTests(unittest.TestCase):
    def test_supplied_analysis_freezes_boundary_and_stop_without_rebuilding(self):
        source, analysis = tracked_fixture()
        analysis["range"]["high"] = 111
        analysis["state"]["summary"] = "冻结参考上沿111，回踩已确认"
        with patch.object(outlook, "build_outlook", side_effect=AssertionError("must not rebuild")):
            plan = build_plan(source, small(), live_price=114, analysis=analysis)
            invalid = build_plan(source, small(), live_price=109.5, analysis=analysis)
        self.assertEqual(plan["status"], "candidate")
        self.assertEqual(plan["stop"], 110)
        self.assertEqual(plan["target"], 130)
        self.assertEqual(plan["structure"]["summary"], analysis["state"]["summary"])
        self.assertEqual(invalid["status"], "invalid")
        self.assertIn("110", invalid["invalidation"])

    def test_supplied_failure_state_overrides_fresh_window_hold_state(self):
        source, analysis = tracked_fixture()
        self.assertEqual(build_plan(source, small())["status"], "candidate")
        analysis["state"] = {"key": "up_failed", "label": "上破收回", "bias": "neutral",
                             "summary": "跟踪中的原结构突破已经失效"}
        plan = build_plan(source, small(), mode="early", analysis=analysis)
        self.assertEqual(plan["status"], "invalid")
        self.assertEqual(plan["structure"]["summary"], analysis["state"]["summary"])
        self.assertIsNone(plan["target"])

    def test_stable_reference_times_and_historical_target_survive_window_rolloff(self):
        source, analysis = tracked_fixture()
        expected = {"start": analysis["range"]["start_time"], "end": analysis["range"]["end_time"]}
        analysis["range"]["start_index"] = -1
        analysis["range"]["end_index"] = -1
        for pivot in analysis["pivots"]:
            pivot["index"] = -1
        short_window = source[-9:]
        plan = build_plan(short_window, small(), analysis=analysis)
        self.assertEqual(plan["status"], "candidate")
        self.assertEqual(plan["reference"], expected)
        self.assertNotEqual(plan["reference"]["start"], short_window[-1]["dt"])
        self.assertEqual(plan["target"], 130)

    def test_invalid_reference_indices_do_not_index_current_bars(self):
        source = bars()
        for index in (-1, -1000, 10000, None, True):
            with self.subTest(index=index):
                analysis = outlook.build_outlook(source, tick=1, period=1800)
                analysis["range"].update(start_index=index, end_index=index)
                plan = build_plan(source, small(), analysis=analysis)
                self.assertEqual(plan["reference"], {"start": None, "end": None})
                self.assertIsNone(plan["target"])
                self.assertEqual(plan["status"], "waiting")

    def test_target_requires_confirmation_before_frozen_reference_end(self):
        source, analysis = tracked_fixture()
        analysis["range"].update(start_index=-1, end_index=-1)
        analysis["pivots"] = [{"kind": "high", "index": -1, "price": 130,
                               "time": source[20]["dt"], "confirmed_time": source[-2]["dt"]}]
        self.assertIsNone(build_plan(source, small(), analysis=analysis)["target"])
        del analysis["pivots"][0]["confirmed_time"]
        self.assertIsNone(build_plan(source, small(), analysis=analysis)["target"])
        analysis["pivots"][0]["confirmed_time"] = analysis["range"]["end_time"]
        self.assertEqual(build_plan(source, small(), analysis=analysis)["target"], 130)

    def test_legacy_analysis_and_default_behavior_remain_identical_and_readonly(self):
        source = bars()
        analysis = outlook.build_outlook(source, tick=1, period=1800)
        original = copy.deepcopy(analysis)
        self.assertEqual(build_plan(source, small()), build_plan(source, small(), analysis=analysis))
        self.assertEqual(analysis, original)
        self.assertEqual(build_plan(None, analysis=analysis)["status"], "insufficient")

    def test_confirmed_requires_completed_retest_and_small_confirmation(self):
        d = build_plan(bars(), small(), tick=1)
        self.assertEqual((d["status"], d["direction"], d["setup"]), ("candidate", "long", "retest_confirm"))
        self.assertTrue(all(c["met"] for c in d["conditions"]))
        self.assertLess(d["stop"], d["entry"])
        self.assertGreater(d["target"], d["entry"])
        self.assertEqual(d["target"], 130)
        self.assertEqual(build_plan(bars(), None)["status"], "waiting")
        self.assertEqual(build_plan(bars(), small("short"))["status"], "waiting")

    def test_breakout_alone_waits_in_confirmed_mode_but_can_be_early(self):
        b = bars([bar() for _ in range(7)] + [bar(113)], live=113)
        self.assertEqual(build_plan(b, small())["status"], "waiting")
        d = build_plan(b, mode="early")
        self.assertEqual(d["status"], "candidate")
        self.assertEqual(d["label"], "试探候选")
        self.assertFalse(d["conditions"][1]["met"])
        self.assertFalse(d["conditions"][2]["met"])
        self.assertIn("尚缺", d["summary"])

    def test_early_boundary_requires_directional_rejection_not_only_proximity(self):
        tail = [bar() for _ in range(6)] + [bar(100.5), bar(101, open_=100.5, low=100)]
        d = build_plan(bars(tail, live=101), mode="early")
        self.assertEqual((d["status"], d["setup"], d["direction"]), ("candidate", "boundary_probe", "long"))
        self.assertEqual(d["target"], 110)
        # 阻力下方、没有拒绝方向，不自动做空。
        flat_top = [bar(109) for _ in range(8)]
        d = build_plan(bars(flat_top, live=109), mode="early")
        self.assertEqual(d["status"], "waiting")
        self.assertEqual(d["direction"], "none")

    def test_short_mirror(self):
        tail = [bar() for _ in range(6)] + [bar(97), bar(98, high=100)]
        d = build_plan(bars(tail, live=98), small("short"))
        self.assertEqual((d["status"], d["direction"]), ("candidate", "short"))
        self.assertGreater(d["stop"], d["entry"])
        self.assertLess(d["target"], d["entry"])

    def test_missing_historical_target_does_not_manufacture_rr_target(self):
        d = build_plan(bars(older_target=False), small())
        self.assertEqual(d["status"], "waiting")
        self.assertIsNone(d["target"])
        self.assertIn("不按风险倍数", d["summary"])

    def test_sixty_bar_input_keeps_boundary_setup_but_waits_after_breakout(self):
        # 60根输入尚无参考段以外的旧障碍；不能让缩短历史产生虚构目标。
        short_history = bars(older_target=False)[-60:]
        self.assertEqual(len(short_history), 60)
        d = build_plan(short_history, small())
        self.assertEqual((d["status"], d["setup"]), ("waiting", "retest_confirm"))
        self.assertIsNone(d["target"])
        self.assertTrue(d["conditions"][1]["met"])
        tail = [bar() for _ in range(6)] + [bar(100.5), bar(101, open_=100.5, low=100)]
        d = build_plan(bars(tail, older_target=False, live=101)[-60:], mode="early")
        self.assertEqual((d["status"], d["setup"]), ("candidate", "boundary_probe"))
        self.assertEqual(d["target"], 110)

    def test_preserving_older_history_is_required_for_breakout_target(self):
        full = bars()
        d = build_plan(full, small())
        truncated = build_plan(full[-80:], small())
        self.assertEqual(d["status"], "candidate")
        self.assertEqual(d["target"], 130)
        self.assertEqual(truncated["status"], "waiting")
        self.assertIsNone(truncated["target"])
        # 截断只损失目标证据，不改变已完成的回踩事实。
        self.assertEqual(d["structure"], truncated["structure"])
        self.assertEqual(d["conditions"][:3], truncated["conditions"][:3])

    def test_false_breakout_is_invalid_even_with_small_confirmation(self):
        b = bars([bar() for _ in range(6)] + [bar(113), bar(107)], live=107)
        d = build_plan(b, small(), mode="early")
        self.assertEqual(d["status"], "invalid")

    def test_live_price_sets_entry_and_risk_but_not_closed_evidence(self):
        a = build_plan(bars(), small(), live_price=112)
        b = build_plan(bars(), small(), live_price=114)
        self.assertEqual(b["status"], "candidate")
        self.assertEqual((a["entry"], b["entry"]), (112, 114))
        self.assertEqual(a["signal_price"], b["signal_price"])
        self.assertEqual(a["structure"], b["structure"])
        self.assertEqual(a["conditions"][:3], b["conditions"][:3])
        self.assertGreater(b["entry"] - b["stop"], a["entry"] - a["stop"])

    def test_live_price_stop_target_and_chasing_gates(self):
        for price, expected in ((108, "invalid"), (131, "invalid"), (120, "waiting"), (110, "waiting")):
            with self.subTest(price=price):
                d = build_plan(bars(), small(), live_price=price)
                self.assertEqual(d["status"], expected)
        # 不允许现价穿越首个历史障碍后改取更远目标。
        self.assertEqual(build_plan(bars(), small(), live_price=131)["target"], 130)

    def test_unfinished_bars_cannot_confirm_or_create_target(self):
        b = bars(older_target=False)
        b[-1] = bar(112, high=200)
        d = build_plan(b, small())
        self.assertEqual(d["status"], "waiting")
        self.assertIsNone(d["target"])
        s = small("short")
        s[-1] = bar(200)
        d = build_plan(bars(), s)
        self.assertEqual(d["status"], "waiting")
        self.assertFalse(d["conditions"][2]["met"])

    def test_tick_grid_and_no_cross_call_mutation(self):
        a = build_plan(bars(), small(), tick=0.5, live_price=112.1)
        b = build_plan(bars(), small(), tick=1, live_price=112.1)
        self.assertEqual((a["entry"], b["entry"]), (112.5, 113))
        self.assertEqual(a, build_plan(bars(), small(), tick=0.5, live_price=112.1))
        for d, tick in ((a, 0.5), (b, 1)):
            for key in ("entry", "stop", "target"):
                self.assertAlmostEqual(d[key] / tick, round(d[key] / tick))

    def test_input_missing_and_output_contract(self):
        for b in (None, [], [bar()] * 40):
            self.assertEqual(build_plan(b)["status"], "insufficient")
        for tick in (0, float("nan"), True):
            self.assertEqual(build_plan(bars(), tick=tick)["status"], "invalid")
        self.assertEqual(build_plan(bars(), live_price=float("nan"))["status"], "invalid")
        b = bars()
        original = copy.deepcopy(b)
        d = build_plan(b, small())
        self.assertEqual(b, original)
        for key in ("status", "label", "mode", "setup", "direction", "entry", "stop", "target", "target_basis", "summary",
                    "wait_for", "trigger", "invalidation", "conditions", "structure", "decision_bar_time", "reference"):
            self.assertIn(key, d)
        self.assertEqual(d["decision_bar_time"], b[-2]["dt"])
        self.assertTrue(d["reference"]["start"])
        json.dumps(d, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
