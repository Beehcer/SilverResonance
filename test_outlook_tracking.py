"""Frozen structure lifecycle: causal, incremental and JSON-persistable."""
import copy
from datetime import datetime, timedelta
import json
import unittest

from outlook import advance_outlook


def bar(close=105, low=None, high=None):
    return {"open": close, "close": close, "low": close - .5 if low is None else low,
            "high": close + .5 if high is None else high, "volume": 100, "oi": 1}


def data(extra=(), direction="up", initial=None):
    history = [bar(c) for c in [100.5, 103, 107, 109.5, 107, 103] * 10]
    observed = initial if initial is not None else [bar() for _ in range(7)] + [bar(113)]
    result = [copy.deepcopy(b) for b in history + list(observed) + list(extra) + [bar(113)]]
    if direction == "down":
        result = [{**b, "open": 210 - b["open"], "close": 210 - b["close"],
                   "high": 210 - b["low"], "low": 210 - b["high"]} for b in result]
    for i, b in enumerate(result):
        b["dt"] = (datetime(2026, 10, 1, 9) + timedelta(minutes=30 * i)).isoformat(" ")
    return result


def advance(bars, previous=None, **kwargs):
    return advance_outlook(bars, previous, symbol="TEST", tick=1, period=1800, **kwargs)


class OutlookTrackingTests(unittest.TestCase):
    def test_initial_contract_stable_id_and_idempotence(self):
        original = data()
        a, state = advance(original)
        again, repeated = advance(copy.deepcopy(original), json.loads(json.dumps(state)))
        self.assertEqual((a, state), (again, repeated))
        self.assertEqual((a["range"]["low"], a["range"]["high"]), (100, 110))
        self.assertEqual(a["state"]["key"], "up_break")
        tracking = a["structure_tracking"]
        for field in ("active", "previous", "candidate", "events", "changed_at", "change_reason", "update_rule"):
            self.assertIn(field, tracking)
        self.assertEqual(tracking["active"]["id"], state["active"]["id"])
        self.assertTrue(all(p["time"] <= p["confirmed_time"] for p in a["pivots"]))
        json.dumps([a, state], allow_nan=False)

    def test_window_roll_does_not_absorb_breakout_or_reset_anchor(self):
        first, state = advance(data())
        sid = state["active"]["id"]
        extra = []
        for i in range(85):
            extra.append(bar(115 + i * 2))  # 无重叠的趋势，不形成整理平台。
            result, state = advance(data(extra)[-50:], state)
        self.assertEqual(state["active"]["id"], sid)
        self.assertEqual(result["state"]["key"], "up_extension")
        self.assertEqual((result["range"]["low"], result["range"]["high"]), (100, 110))
        self.assertEqual(result["range"]["start_time"], first["range"]["start_time"])
        self.assertEqual((result["range"]["start_index"], result["range"]["end_index"]), (-1, -1))
        self.assertIsNone(result["structure_tracking"]["candidate"])

    def test_unfinished_bar_only_changes_price_and_path_start(self):
        original = data()
        a, state = advance(original)
        saved = copy.deepcopy(state)
        changed = copy.deepcopy(original)
        changed[-1].update(open=200, close=200, high=1000, low=1)
        b, next_state = advance(changed, state)
        self.assertEqual(state, saved)
        self.assertEqual(next_state, state)
        self.assertEqual(a["structure_tracking"], b["structure_tracking"])
        for key in ("state", "levels", "range", "pivots", "events", "atr", "buffer"):
            self.assertEqual(a[key], b[key])
        for x, y in zip(a["scenarios"], b["scenarios"]):
            self.assertEqual(x["path"][1:], y["path"][1:])
            self.assertEqual(y["path"][0], 200)

    def test_long_short_hold_extension_and_failed_return(self):
        for direction, prefix in (("up", "up"), ("down", "down")):
            with self.subTest(direction=direction):
                _, state = advance(data(direction=direction))
                hold, state = advance(data([bar(112, low=110)], direction), state)
                self.assertEqual(hold["state"]["key"], prefix + "_hold")
                extend, state = advance(data([bar(112, low=110), bar(123)], direction), state)
                self.assertEqual(extend["state"]["key"], prefix + "_extension")
                failed, state = advance(data([bar(112, low=110), bar(123), bar(107)], direction), state)
                self.assertEqual(failed["state"]["key"], prefix + "_failed")
                self.assertEqual(failed["state"]["bias"], "neutral")
                self.assertEqual(failed["scenarios"][0]["key"], "failed_rebalance")
                self.assertEqual((state["active"]["low"], state["active"]["high"]), (100, 110))

    def test_platform_requires_four_forming_and_two_later_confirming_bars(self):
        group = [bar(117, low=115, high=119)] * 6
        for direction, bounds in (("up", (115, 119)), ("down", (91, 95))):
            with self.subTest(direction=direction):
                _, state = advance(data(direction=direction))
                old_id = state["active"]["id"]
                for n in range(1, 7):
                    result, state = advance(data(group[:n], direction), state)
                    if n < 4:
                        self.assertIsNone(state["candidate"])
                    elif n < 6:
                        self.assertEqual(state["candidate"]["bars"], n)
                        self.assertEqual(state["active"]["id"], old_id)
                self.assertNotEqual(state["active"]["id"], old_id)
                self.assertEqual((state["active"]["low"], state["active"]["high"]), bounds)
                self.assertEqual(result["state"]["key"], "inside")
                self.assertEqual(state["previous"][-1]["id"], old_id)
                self.assertEqual(state["previous"][-1]["status"], "replaced")
                self.assertIsNone(state["candidate"])

    def test_candidate_failure_preserves_old_structure_and_resets_confirmation(self):
        group = [bar(117, low=115, high=119)] * 5
        _, state = advance(data())
        original_id = state["active"]["id"]
        _, state = advance(data(group), state)
        self.assertEqual(state["candidate"]["confirmations"], 1)
        result, state = advance(data(group + [bar(124)]), state)
        self.assertIsNone(state["candidate"])
        self.assertEqual(state["active"]["id"], original_id)
        self.assertEqual(state["previous"], [])
        self.assertTrue(any(e["kind"] == "candidate_cancelled" for e in result["structure_tracking"]["events"]))

    def test_trend_platform_candidate_preserves_separate_failure_path(self):
        group = [bar(117, low=115, high=119)] * 4
        for direction in ("up", "down"):
            with self.subTest(direction=direction):
                _, state = advance(data(direction=direction))
                result, state = advance(data(group, direction), state)
                self.assertIsNotNone(state["candidate"])
                self.assertEqual([p["key"] for p in result["scenarios"]],
                                 [direction + "_shallow", "new_platform", direction + "_failure_return"])

    def test_failed_platform_candidate_preserves_both_directional_branches(self):
        group = [bar(107)] + [bar(105, low=103, high=107)] * 4
        for direction in ("up", "down"):
            with self.subTest(direction=direction):
                _, state = advance(data(direction=direction))
                result, state = advance(data(group, direction), state)
                self.assertIsNotNone(state["candidate"])
                self.assertEqual(result["state"]["key"], direction + "_failed")
                self.assertEqual([p["key"] for p in result["scenarios"]],
                                 ["new_platform", "up_retest", "down_retest"])

    def test_shadow_beyond_platform_buffer_does_not_confirm(self):
        group = [bar(117, low=115, high=119)] * 4
        _, state = advance(data())
        _, state = advance(data(group), state)
        _, state = advance(data(group + [bar(117, low=110, high=125)]), state)
        self.assertIsNone(state["candidate"])
        self.assertEqual(state["active"]["high"], 110)

    def test_platform_after_failed_breakout_is_nested_and_independent(self):
        _, state = advance(data())
        group = [bar(107)] + [bar(105, low=103, high=107)] * 6
        result, state = advance(data(group), state)
        self.assertEqual((state["active"]["low"], state["active"]["high"]), (103, 107))
        self.assertEqual(result["state"]["key"], "inside")
        self.assertEqual(state["previous"][-1]["phase"], "up_failed")

    def test_batch_and_one_bar_at_a_time_have_same_causal_result(self):
        _, initial = advance(data())
        extra = [bar(117, low=115, high=119)] * 6 + [bar(123), bar(126)]
        batch_result, batch_state = advance(data(extra), initial)
        state = initial
        for n in range(1, len(extra) + 1):
            result, state = advance(data(extra[:n]), state)
        self.assertEqual(state, batch_state)
        self.assertEqual(result, batch_result)
        self.assertIsNone(initial["candidate"])

    def test_reference_touch_is_fact_and_does_not_infer_intrabar_order(self):
        _, state = advance(data())
        result, state = advance(data([bar(105, low=85, high=125)]), state)
        touched = [e for e in result["structure_tracking"]["events"] if e["kind"] == "reference_touch"]
        self.assertEqual(len(touched), 2)
        self.assertEqual({e["price"] for e in touched}, {90, 120})
        self.assertTrue(all("仅记录触及" in e["detail"] and "不推断同棒先后" in e["detail"] for e in touched))
        self.assertTrue(all("参考位触及" in e["label"] for e in touched))
        self.assertEqual(result["state"]["key"], "up_failed")

    def test_history_keeps_four_generations_without_mutating_previous_state(self):
        _, state = advance(data())
        extra = []
        for generation in range(5):
            high = state["active"]["high"]
            if generation:
                extra.append(bar(high + 4))
            extra += [bar(high + 7, low=high + 5, high=high + 9)] * 6
            old = copy.deepcopy(state)
            _, next_state = advance(data(extra), state)
            self.assertEqual(state, old)
            state = next_state
        self.assertGreaterEqual(len(state["previous"]), 4)
        self.assertGreaterEqual(len(state["events"]), 8)
        self.assertEqual(len({h["id"] for h in state["previous"]}), len(state["previous"]))

    def test_revision_rebuild_changes_id_even_when_reference_bounds_unchanged(self):
        original = data()
        _, state = advance(original)
        revised = copy.deepcopy(original)
        revised[10]["close"] += .1
        result, next_state = advance(revised, state)
        self.assertIn("修订", next_state["rebuild_reason"])
        self.assertEqual((state["active"]["low"], state["active"]["high"]),
                         (next_state["active"]["low"], next_state["active"]["high"]))
        self.assertNotEqual(state["active"]["id"], next_state["active"]["id"])
        self.assertEqual(advance(revised, next_state)[1], next_state)
        self.assertEqual(result["structure_tracking"]["previous"][-1]["status"], "rebuilt")

    def test_rollback_gap_and_parameter_change_rebuild_explicitly(self):
        _, state = advance(data([bar(115), bar(118)]))
        rollback, rebuilt = advance(data(), state)
        self.assertIn("回退", rebuilt["rebuild_reason"])
        ats = [e["at"] for e in rollback["structure_tracking"]["events"]]
        self.assertEqual(ats, sorted(ats))
        missing = data([bar(115), bar(118), bar(119)])
        del missing[-3]
        self.assertIn("断档", advance(missing, state)[1]["rebuild_reason"])
        changed = advance_outlook(data(), state, symbol="OTHER", tick=1, period=1800)[1]
        self.assertIn("品种", changed["rebuild_reason"])
        self.assertEqual(changed["symbol"], "OTHER")

    def test_historical_insert_is_a_revision_but_older_window_prefix_is_allowed(self):
        full = data()
        missing = copy.deepcopy(full)
        del missing[40]
        _, state = advance(missing)
        _, restored = advance(full, state)
        self.assertIn("补入", restored["rebuild_reason"])
        self.assertNotEqual(restored["active"]["id"], state["active"]["id"])
        # 更早的可见前缀不会改写已冻结结构，也不被当成区间内补入。
        _, short = advance(full[-50:])
        result, wider = advance(full, short)
        self.assertIsNone(wider["rebuild_reason"])
        self.assertEqual(wider["active"]["id"], short["active"]["id"])

    def test_no_shared_anchor_tick_and_version_change_rebuild(self):
        _, state = advance(data())
        later = data([bar(115 + 2 * i) for i in range(80)])[-50:]
        self.assertIn("共同锚点", advance(later, state)[1]["rebuild_reason"])
        tick_state = advance_outlook(data(), state, symbol="TEST", tick=.5, period=1800)[1]
        self.assertIn("最小跳动", tick_state["rebuild_reason"])
        old_version = copy.deepcopy(state)
        old_version["version"] = 0
        self.assertIn("版本", advance(data(), old_version)[1]["rebuild_reason"])

    def test_out_of_order_or_duplicate_time_pauses_until_rebuilt(self):
        _, state = advance(data())
        bad = data()
        bad[10], bad[11] = bad[11], bad[10]
        result, paused = advance(bad, state)
        self.assertEqual(result["status"], "insufficient")
        self.assertIn("乱序", paused["pending_rebuild"])
        result, resumed = advance(data(), paused)
        self.assertEqual(result["status"], "ok")
        self.assertIn("恢复后重建", resumed["rebuild_reason"])
        bad = data()
        bad[10]["dt"] = bad[9]["dt"]
        self.assertEqual(advance(bad, state)[0]["status"], "insufficient")

    def test_missing_timestamp_does_not_invent_incremental_identity(self):
        b = data()
        del b[10]["dt"]
        result, state = advance(b)
        self.assertEqual(result["status"], "insufficient")
        self.assertIn("dt", state["pending_rebuild"])
        for item in result["scenarios"]:
            self.assertNotIn("probability", item)


if __name__ == "__main__":
    unittest.main()
