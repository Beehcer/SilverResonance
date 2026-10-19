"""走势推演纯函数测试：python -m unittest test_outlook。"""

import copy
import json
import unittest

from outlook import build_outlook


def bar(close=105, low=None, high=None):
    return {"open": close, "close": close, "low": close - 0.5 if low is None else low,
            "high": close + 0.5 if high is None else high, "volume": 100, "oi": 1}


def history():
    # 重复的触边结构，固定前段上下边界为 100 / 110。
    return [bar(c) for c in [100.5, 103, 107, 109.5, 107, 103] * 10]


def sample(observed=None, live=None):
    observed = observed or [bar() for _ in range(8)]
    return history() + observed + [live or bar()]


class OutlookTests(unittest.TestCase):
    def test_inside_is_neutral_not_short(self):
        d = build_outlook(sample(), tick=0.5)
        self.assertEqual((d["status"], d["state"]["key"], d["state"]["bias"]), ("ok", "inside", "neutral"))
        self.assertEqual(d["range"]["kind"], "range")
        self.assertEqual(d["levels"]["support"], 100)
        self.assertEqual(d["levels"]["resistance"], 110)

    def test_up_break_needs_later_retest(self):
        observed = [bar() for _ in range(7)] + [bar(113, low=109.8, high=113.5)]
        d = build_outlook(sample(observed), tick=0.5)
        self.assertEqual(d["state"]["key"], "up_break")
        self.assertEqual(d["state"]["bias"], "long")

    def test_false_up_break_returns_neutral(self):
        observed = [bar() for _ in range(6)] + [bar(113), bar(107)]
        d = build_outlook(sample(observed), tick=0.5)
        self.assertEqual(d["state"]["key"], "up_failed")
        self.assertEqual(d["state"]["bias"], "neutral")
        self.assertIn("并不自动构成做空", d["state"]["summary"])

    def test_false_down_break_returns_neutral(self):
        observed = [bar() for _ in range(6)] + [bar(97), bar(103)]
        d = build_outlook(sample(observed), tick=0.5)
        self.assertEqual(d["state"]["key"], "down_failed")
        self.assertEqual(d["state"]["bias"], "neutral")

    def test_retest_hold(self):
        observed = [bar() for _ in range(6)] + [bar(113), bar(112, low=110, high=112.5)]
        d = build_outlook(sample(observed), tick=0.5)
        self.assertEqual(d["state"]["key"], "up_hold")

    def test_failed_rebound(self):
        observed = [bar() for _ in range(6)] + [bar(97), bar(98, low=97.5, high=100)]
        d = build_outlook(sample(observed), tick=0.5)
        self.assertEqual(d["state"]["key"], "down_hold")
        self.assertEqual(d["state"]["label"], "反抽受阻")

    def test_extension(self):
        observed = [bar() for _ in range(6)] + [bar(120), bar(124)]
        d = build_outlook(sample(observed), tick=0.5)
        self.assertEqual(d["state"]["key"], "up_extension")
        self.assertGreater(d["levels"]["upper_target"], 124)

    def test_later_observation_volatility_cannot_erase_earlier_breakout(self):
        for first_close, extreme_high, extreme_low, kind in ((112, 150, 60, "up"), (98, 150, 60, "down")):
            with self.subTest(kind=kind):
                observed = [bar(first_close)] + [bar() for _ in range(7)]
                a = build_outlook(sample(observed), tick=1)
                observed[-1] = bar(105, low=extreme_low, high=extreme_high)
                b = build_outlook(sample(observed), tick=1)
                self.assertEqual(a["atr"], b["atr"])
                self.assertEqual(a["buffer"], b["buffer"])
                earlier_a = [e for e in a["events"] if e["index"] == 60]
                earlier_b = [e for e in b["events"] if e["index"] == 60]
                self.assertEqual(len(earlier_a), 1)
                self.assertEqual(earlier_a[0]["kind"], kind)
                self.assertEqual(earlier_a, earlier_b)

    def test_extensions_downgrade_when_latest_close_returns_to_boundary(self):
        for closes, expected in (((120, 124, 110), "up_break"), ((90, 86, 100), "down_break")):
            with self.subTest(closes=closes):
                observed = [bar() for _ in range(5)] + [bar(c) for c in closes]
                d = build_outlook(sample(observed), tick=1)
                self.assertEqual(d["state"]["key"], expected)
                self.assertEqual(d["state"]["bias"], "neutral")
                self.assertNotIn("连续收盘", d["state"]["summary"])
                self.assertIn(f"最新收盘 {closes[-1]}", d["state"]["summary"])

    def test_extensions_require_current_distance_not_only_old_extension(self):
        for closes, expected in (((120, 124, 113), "up_break"), ((90, 86, 97), "down_break")):
            with self.subTest(closes=closes):
                observed = [bar() for _ in range(5)] + [bar(c) for c in closes]
                d = build_outlook(sample(observed), tick=1)
                self.assertEqual(d["state"]["key"], expected)
                self.assertNotIn("已远离边界", d["state"]["summary"])

    def test_holds_downgrade_when_latest_close_reenters_confirmation_band(self):
        cases = [([bar(113), bar(112, low=110), bar(110)], "up_break"),
                 ([bar(97), bar(98, high=100), bar(100)], "down_break")]
        for tail, expected in cases:
            with self.subTest(expected=expected):
                d = build_outlook(sample([bar() for _ in range(5)] + tail), tick=1)
                self.assertEqual(d["state"]["key"], expected)
                self.assertEqual(d["state"]["bias"], "neutral")
                self.assertIn("待确认", d["state"]["label"])

    def test_observation_does_not_rewrite_anchors(self):
        a = build_outlook(sample(), tick=0.5)
        observed = [bar() for _ in range(6)] + [bar(120), bar(124)]
        b = build_outlook(sample(observed), tick=0.5)
        for key in ("low", "high", "start_index", "end_index"):
            self.assertEqual(a["range"][key], b["range"][key])
        self.assertEqual(b["range"]["end_index"], 59)

    def test_last_unfinished_bar_cannot_change_state(self):
        bars = sample([bar() for _ in range(6)] + [bar(113), bar(112, low=110)])
        a = build_outlook(bars, tick=0.5)
        bars[-1] = bar(2000, low=0.01, high=3000)
        b = build_outlook(bars, tick=0.5)
        for key in ("state", "atr", "buffer", "range", "pivots", "events", "levels"):
            self.assertEqual(a[key], b[key], key)
        self.assertNotEqual(a["price"], b["price"])
        self.assertEqual(b["scenarios"][0]["path"][0], 2000)

    def test_pivots_require_two_completed_right_bars(self):
        bars = sample()
        # 倒数第三根是候选，右侧只有一根完成K线；最后未完成K线不算确认。
        bars[-3] = bar(105, high=115)
        d = build_outlook(bars, tick=0.5)
        self.assertNotIn(len(bars) - 3, [p["index"] for p in d["pivots"] if p["kind"] == "high"])
        bars.append(bar())  # 原末根成为完成K线，候选右侧满两根。
        d = build_outlook(bars, tick=0.5)
        self.assertIn(len(bars) - 4, [p["index"] for p in d["pivots"] if p["kind"] == "high"])
        self.assertTrue(all(p["index"] <= len(bars) - 4 for p in d["pivots"]))

    def test_tick_is_per_call_and_does_not_leak(self):
        bars = sample([bar() for _ in range(7)] + [bar(112)])
        small = build_outlook(bars, tick=0.5)
        large = build_outlook(bars, tick=5)
        again = build_outlook(bars, tick=0.5)
        self.assertEqual(small, again)
        self.assertEqual(small["state"]["key"], "up_break")
        self.assertEqual(large["state"]["key"], "inside")
        self.assertGreater(large["buffer"], small["buffer"])

    def test_missing_invalid_and_insufficient_data(self):
        for bars in (None, [], [bar()] * 40, [{}] * 50):
            self.assertEqual(build_outlook(bars)["status"], "insufficient")
        bars = sample()
        bars[20]["close"] = float("nan")
        self.assertEqual(build_outlook(bars)["status"], "insufficient")
        bars = sample()
        del bars[20]["open"]
        self.assertEqual(build_outlook(bars)["status"], "insufficient")

    def test_forty_completed_bars_are_enough(self):
        bars = sample()[-41:]
        d = build_outlook(bars)
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["range"]["end_index"], 31)

    def test_targets_prefer_earlier_confirmed_swings(self):
        prefix = [bar() for _ in range(40)]
        prefix[20] = bar(105, high=130)
        prefix[25] = bar(105, low=80)
        d = build_outlook(prefix + sample(), tick=0.5)
        self.assertEqual(d["levels"]["resistance"], 110)
        self.assertEqual(d["levels"]["upper_target"], 130)
        self.assertEqual(d["levels"]["lower_target"], 80)
        self.assertIn("此前已确认摆动高点", d["scenarios"][0]["condition"])

    def test_indices_stay_relative_to_full_input_when_window_is_trimmed(self):
        bars = history() * 3 + sample()
        d = build_outlook(bars, tick=0.5)
        self.assertEqual(d["range"]["end_index"], len(bars) - 10)
        self.assertEqual(d["range"]["start_index"], len(bars) - 69)
        self.assertTrue(all(len(bars) - 121 <= p["index"] <= len(bars) - 4
                            for p in d["pivots"]))

    def test_readonly_serializable_contract_and_conditional_scenarios(self):
        bars = sample()
        before = copy.deepcopy(bars)
        d = build_outlook(bars, tick=0.5, period=900)
        self.assertEqual(bars, before)
        json.dumps(d, allow_nan=False)
        self.assertLessEqual(len(d["events"]), 3)
        self.assertTrue(all(e["index"] < len(bars) - 1 for e in d["events"]))
        self.assertEqual([s["key"] for s in d["scenarios"]], ["up", "down", "range"])
        for scenario in d["scenarios"]:
            self.assertRegex(scenario["condition"], r"\d")
            self.assertRegex(scenario["invalidation"], r"\d")
            self.assertGreater(len(scenario["path"]), 2)
        self.assertIn("参考延伸", d["scenarios"][0]["condition"])


if __name__ == "__main__":
    unittest.main()
