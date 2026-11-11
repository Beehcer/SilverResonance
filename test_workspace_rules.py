import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_rules import (IMPLEMENTED_RULE_VERSION, RULE_COMPATIBILITY_REVIEW,
                             evaluate_plan, rule_manifest, validate_plan)


VERSION = "fixture-rule-v1"


def bar(dt, close, low=None, high=None, closed=True, open_=None, **extra):
    return {"dt": dt, "open": close if open_ is None else open_,
            "high": close + 1 if high is None else high,
            "low": close - 1 if low is None else low, "close": close,
            "closed": closed, **extra}


def fixture():
    plan = {
        "title": "B类反转计划", "enabled": True, "rule_version": VERSION,
        "direction": "long", "mode": "reversal_b", "session_label": "日盘",
        "expires_at": "2026-11-09T11:00:00+08:00",
        "context_confirmed": True,
        "context_note": "已完成日线允许本模式；原趋势线已破坏，局部反向进攻成立，关键前高110尚未突破。",
        "main_level": 100, "main_after": "2026-11-09T09:00:00+08:00",
        "entry_low": 98, "entry_high": 105, "trigger_price": 101,
        "trigger_buffer_ticks": 0, "stop_price": 95, "obstacle_price": 110,
        "objects": [], "notes": "最近障碍的失败条件按人工计划处理",
    }
    market = {
        "symbol": "TEST.future", "price": 103, "tick": 1, "tick_value": 10,
        "source": "live", "fresh": True, "as_of": "2026-11-09T10:10:00+08:00",
        "periods": {
            "day": [bar("2026-11-06T15:00:00+08:00", 100),
                    bar("2026-11-09T09:00:00+08:00", 99, closed=False)],
            "m30": [bar("2026-11-09T09:30:00+08:00", 102, low=97),
                    bar("2026-11-09T10:00:00+08:00", 100, closed=False)],
            "m3": [bar("2026-11-09T09:33:00+08:00", 100, low=98),
                   bar("2026-11-09T09:36:00+08:00", 103, low=100),
                   bar("2026-11-09T10:09:00+08:00", 100, closed=False)],
        },
    }
    risk = {"lots": 2, "fee_per_lot": 4, "slippage_ticks": 0.5,
            "budget": 300, "daily_limit": 1000, "daily_loss": 50}
    return plan, market, risk


def checks(result):
    return {item["key"]: item for item in result["checks"]}


class WorkspaceRuleTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.market, self.risk = fixture()

    def evaluate(self):
        return evaluate_plan(self.plan, self.market, self.risk, VERSION)

    def test_b_ready_without_breaking_key_extreme(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertLess(self.market["price"], self.plan["obstacle_price"])
        self.assertIn("B类不要求", checks(result)["main"]["detail"])
        self.assertIn("人工", checks(result)["context"]["detail"])
        self.assertTrue(result["signal_key"])

    def test_all_modes_keep_manual_premises_and_identical_no_bypass_gates(self):
        for mode in ("continuation", "reversal_a", "reversal_b", "range"):
            with self.subTest(mode=mode):
                self.plan["mode"] = mode
                self.assertEqual(self.evaluate()["status"], "ready")
                self.plan["context_confirmed"] = False
                self.assertEqual(self.evaluate()["status"], "waiting")
                self.plan["context_confirmed"] = True
        self.plan["mode"] = "early"
        self.assertEqual(self.evaluate()["status"], "invalid")

    def test_small_cycle_cannot_authorize_main(self):
        self.market["periods"]["m30"][0]["close"] = 100
        self.market["periods"]["m30"][0]["open"] = 100
        result = self.evaluate()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(checks(result)["main"]["state"], "wait")
        self.assertEqual(checks(result)["retest"]["state"], "wait")

    def test_before_or_same_time_main_reference_is_not_permission(self):
        self.plan["main_after"] = "2026-11-09T09:30:00+08:00"
        self.assertEqual(checks(self.evaluate())["main"]["state"], "wait")

    def test_retest_and_trigger_must_be_strictly_subsequent(self):
        self.market["periods"]["m3"] = [
            bar("2026-11-09T09:27:00+08:00", 100),
            bar("2026-11-09T09:30:00+08:00", 103),
        ]
        result = self.evaluate()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(checks(result)["retest"]["state"], "wait")
        self.market["periods"]["m3"] = [bar("2026-11-09T09:33:00+08:00", 103, low=99)]
        self.assertEqual(self.evaluate()["status"], "waiting")

    def test_bar_start_cannot_be_mistaken_for_its_close_time(self):
        self.market["periods"]["m30"][0]["closed_at"] = "2026-11-09T10:00:00+08:00"
        result = self.evaluate()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(checks(result)["main"]["time"], "2026-11-09T10:00:00+08:00")
        self.market["periods"]["m3"][0]["closed_at"] = "2026-11-09T10:03:00+08:00"
        self.market["periods"]["m3"][1]["end_time"] = "2026-11-09T10:06:00+08:00"
        self.assertEqual(self.evaluate()["status"], "ready")

    def test_trigger_requires_completed_cross_and_within_region(self):
        trigger = self.market["periods"]["m3"][1]
        trigger["closed"] = False
        self.assertEqual(checks(self.evaluate())["trigger"]["state"], "wait")
        trigger["closed"] = True
        trigger["close"] = trigger["open"] = 101
        self.assertEqual(checks(self.evaluate())["trigger"]["state"], "wait")
        trigger["close"] = trigger["open"] = 106
        trigger["high"] = 107
        self.assertEqual(checks(self.evaluate())["trigger"]["state"], "wait")

    def test_prior_permission_persists_when_later_closed_bar_returns_below_main(self):
        original = self.evaluate()
        self.market["periods"]["m30"].insert(1, bar("2026-11-09T10:00:00+08:00", 99, low=98))
        self.market["periods"]["m3"].insert(2, bar("2026-11-09T10:03:00+08:00", 100, low=98))
        result = self.evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["signal_key"], original["signal_key"])
        self.assertEqual(checks(result)["main"]["time"], checks(original)["main"]["time"])

    def test_unfinished_candle_does_not_cancel_completed_signal(self):
        original = self.evaluate()
        self.market["periods"]["m3"][-1] = bar("2026-11-09T10:09:00+08:00", 97, low=96, closed=False)
        self.market["periods"]["m30"][-1] = bar("2026-11-09T10:00:00+08:00", 97, low=96, closed=False)
        result = self.evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["signal_key"], original["signal_key"])

    def test_formed_stop_touch_is_not_undone_by_price_recovery(self):
        self.market["periods"]["m3"][-1] = bar("2026-11-09T10:09:00+08:00", 103, low=94, closed=False)
        result = self.evaluate()
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(checks(result)["trigger"]["state"], "pass")
        self.assertIn("形成中", checks(result)["stop"]["detail"])

    def test_closed_stop_touch_is_not_undone_by_price_recovery(self):
        self.market["periods"]["m3"].insert(2, bar("2026-11-09T10:03:00+08:00", 103, low=94))
        self.assertEqual(checks(self.evaluate())["stop"]["state"], "fail")

    def test_after_trigger_closed_departure_does_not_resurrect_on_return(self):
        self.market["periods"]["m3"].insert(2, bar("2026-11-09T10:00:00+08:00", 106))
        self.market["periods"]["m3"].insert(3, bar("2026-11-09T10:03:00+08:00", 103))
        result = self.evaluate()
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(checks(result)["signal_validity"]["state"], "fail")
        self.assertTrue(result["signal_key"])

    def test_current_price_must_be_in_zone_and_before_stop_obstacle(self):
        for price, failed_key in ((94, "stop"), (106, "entry"), (110, "obstacle")):
            with self.subTest(price=price):
                self.market["price"] = price
                result = self.evaluate()
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(checks(result)[failed_key]["state"], "fail")

    def test_waits_before_first_zone_arrival(self):
        self.market["price"] = 107
        self.market["periods"]["m3"] = [bar("2026-11-09T09:33:00+08:00", 107)]
        self.assertEqual(self.evaluate()["status"], "waiting")

    def test_missing_risk_is_unknown_not_zero(self):
        for key in ("lots", "fee_per_lot", "slippage_ticks", "budget"):
            with self.subTest(key=key):
                saved = self.risk.pop(key)
                result = self.evaluate()
                self.assertEqual(result["status"], "insufficient")
                self.assertEqual(checks(result)["risk"]["state"], "unknown")
                self.assertIsNone(result["risk"][key])
                self.risk[key] = saved

    def test_daily_risk_is_optional_but_requires_both_if_enabled(self):
        self.risk.pop("daily_limit")
        self.assertEqual(self.evaluate()["status"], "insufficient")
        self.risk.pop("daily_loss")
        result = self.evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["risk"]["daily_enabled"])
        self.assertIsNone(result["risk"]["remaining_daily"])
        self.risk.update(daily_limit=None, daily_loss=None)
        self.assertEqual(self.evaluate()["status"], "ready")

    def test_costs_include_two_sides_and_personal_lots(self):
        risk = self.evaluate()["risk"]
        self.assertEqual(risk["price_risk"], 160)
        self.assertEqual(risk["fees"], 8)
        self.assertEqual(risk["slippage"], 20)
        self.assertEqual(risk["total_risk"], 188)
        self.assertEqual(risk["remaining_daily"], 950)
        self.assertEqual(risk["projected_daily_loss"], 238)
        self.assertEqual(risk["net_obstacle_room"], 112)
        self.risk["budget"] = 187
        self.assertEqual(self.evaluate()["status"], "invalid")

    def test_daily_limit_includes_new_trade_risk(self):
        self.risk["daily_limit"] = 237
        self.assertEqual(self.evaluate()["status"], "invalid")
        self.risk["daily_limit"] = 238
        self.assertEqual(self.evaluate()["status"], "ready")

    def test_no_net_space_after_costs_blocks_without_invented_target(self):
        self.plan["obstacle_price"] = 104
        result = self.evaluate()
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["levels"]["obstacle_price"], 104)
        self.assertLess(result["risk"]["net_obstacle_room"], 0)

    def test_missing_daily_or_manual_premise_never_automatically_inferred(self):
        self.market["periods"]["day"][0]["closed"] = False
        self.assertEqual(self.evaluate()["status"], "insufficient")
        self.market["periods"]["day"][0]["closed"] = True
        self.plan.pop("context_confirmed")
        result = self.evaluate()
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(checks(result)["context"]["state"], "unknown")
        self.plan["context_confirmed"] = False
        self.assertEqual(checks(self.evaluate())["context"]["state"], "wait")

    def test_freshness_expiry_and_version_revoke_but_keep_evidence(self):
        for field, value in (("fresh", False), ("as_of", "2026-11-09T11:00:00+08:00")):
            with self.subTest(field=field):
                original = self.market[field]
                self.market[field] = value
                result = self.evaluate()
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(checks(result)["trigger"]["state"], "pass")
                self.market[field] = original
        self.plan["rule_version"] = "old"
        self.assertEqual(self.evaluate()["status"], "invalid")
        self.assertEqual(evaluate_plan(self.plan, self.market, self.risk, "")["status"], "insufficient")

    def test_mock_can_never_ready(self):
        self.market["source"] = "mock"
        result = self.evaluate()
        self.assertEqual(result["status"], "waiting")
        self.assertIn("模拟", result["label"])
        self.assertEqual(checks(result)["trigger"]["state"], "pass")
        self.market["fresh"] = False
        self.assertEqual(self.evaluate()["status"], "invalid")

    def test_directional_zone_does_not_veto_opposite_direction(self):
        self.plan["objects"] = [{"id": "danger", "kind": "zone", "zone_type": "danger",
            "direction": "short", "period": "m30", "effect": "block",
            "points": [{"time": 1794186000000, "price": 98}, {"time": 1794196800000, "price": 106}]}]
        self.assertEqual(self.evaluate()["status"], "ready")
        self.plan["objects"][0]["direction"] = "long"
        result = self.evaluate()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(checks(result)["zone:danger"]["state"], "fail")
        self.plan["objects"][0]["effect"] = "warn"
        self.assertEqual(self.evaluate()["status"], "ready")
        self.plan["objects"][0].update(zone_type="invalid", effect="observe")
        self.assertEqual(self.evaluate()["status"], "ready")

    def test_short_is_symmetric(self):
        self.plan["direction"] = "short"
        for key in ("main_level", "trigger_price", "stop_price", "obstacle_price"):
            self.plan[key] = 200 - self.plan[key]
        self.plan["entry_low"], self.plan["entry_high"] = 200 - self.plan["entry_high"], 200 - self.plan["entry_low"]
        self.market["price"] = 200 - self.market["price"]
        for series in self.market["periods"].values():
            for item in series:
                item["open"], item["close"] = 200 - item["open"], 200 - item["close"]
                item["low"], item["high"] = 200 - item["high"], 200 - item["low"]
        self.assertEqual(self.evaluate()["status"], "ready")
        self.assertEqual(self.evaluate()["risk"]["total_risk"], 188)

    def test_corresponding_defensive_low_is_manual_and_break_alone_is_not_entry(self):
        self.plan.update(direction="short", mode="continuation", main_level=100,
                         entry_low=95, entry_high=102, trigger_price=99,
                         stop_price=105, obstacle_price=90,
                         context_note="人工核对：30分钟反弹从防守低点100恢复上攻至104；100在破位前已确认，"
                                      "原空头延续允许，反弹恢复原推进结构的失效边界为105，当前尚未恢复。")
        self.market["price"] = 97
        self.market["periods"]["m30"] = [bar("2026-11-09T09:30:00+08:00", 98, high=103)]
        self.market["periods"]["m3"] = [bar("2026-11-09T09:33:00+08:00", 97)]
        self.assertEqual(self.evaluate()["status"], "waiting")
        self.assertEqual(checks(self.evaluate())["main"]["state"], "pass")
        # Only the independent later rebound and later closed weakness complete the chain.
        self.market["periods"]["m3"] = [bar("2026-11-09T09:33:00+08:00", 100, high=102),
                                         bar("2026-11-09T09:36:00+08:00", 97, high=100)]
        self.assertEqual(self.evaluate()["status"], "ready")
        self.plan["context_confirmed"] = False
        self.assertEqual(self.evaluate()["status"], "waiting")
        self.plan["context_confirmed"] = True
        # A 3m defensive-low breach cannot manufacture the still-missing 30m permission.
        self.market["periods"]["m30"] = [bar("2026-11-09T09:30:00+08:00", 102)]
        self.assertEqual(self.evaluate()["status"], "waiting")

    def test_compatibility_review_records_manual_scope_and_reviewed_version(self):
        review = RULE_COMPATIBILITY_REVIEW
        self.assertEqual(review["reviewed_version"], IMPLEMENTED_RULE_VERSION)
        self.assertNotEqual(review["previous_version"], review["reviewed_version"])
        self.assertIn("八4", review["sections"])
        self.assertIn("人工", review["reason"])
        self.assertTrue(any("对应关系" in item for item in review["not_automated"]))
        self.assertTrue(any("context_confirmed" in item["fields"] for item in review["mapping"]))

    def test_time_zones_normalize_to_shanghai(self):
        self.plan["main_after"] = "2026-11-09T01:00:00Z"
        self.market["as_of"] = "2026-11-09T10:10:00"
        self.market["periods"]["m30"][0]["dt"] = "2026-11-09T01:30:00+00:00"
        self.assertEqual(self.evaluate()["status"], "ready")

    def test_incomplete_or_bad_market_is_insufficient_never_false(self):
        self.market["periods"]["m3"][0].pop("closed")
        result = self.evaluate()
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(checks(result)["execution_data"]["state"], "unknown")

    def test_future_or_duplicate_closed_bars_are_not_accepted(self):
        self.market["periods"]["m3"][1]["dt"] = "2026-11-09T10:15:00+08:00"
        self.assertEqual(self.evaluate()["status"], "insufficient")
        self.market["periods"]["m3"][1]["dt"] = self.market["periods"]["m3"][0]["dt"]
        self.assertEqual(self.evaluate()["status"], "insufficient")

    def test_finite_numeric_risk_cannot_accept_booleans_or_nan(self):
        for value in (True, float("nan"), float("inf"), "0", 10 ** 400):
            with self.subTest(value=value):
                self.risk["fee_per_lot"] = value
                self.assertEqual(self.evaluate()["status"], "insufficient")

    def test_validator_handles_partial_drafts_and_rejects_unknown_bad_fields(self):
        self.assertIsNone(validate_plan({})["context_confirmed"])
        self.assertEqual(evaluate_plan({}, {}, {}, VERSION)["status"], "insufficient")
        for bad in ({"mode": []}, {"mode": "early"}, {"main_level": True}, {"main_level": float("nan")},
                    {"surprise": 1}, {"main_after": "bad"}, {"entry_low": 5, "entry_high": 4},
                    {"context_confirmed": "yes"}, {"trigger_buffer_ticks": -1}, {1: "bad"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_plan(bad)
        with self.assertRaises(ValueError):
            validate_plan({"main_level": 10 ** 400})

    def test_overflowing_calculations_do_not_leak_nonfinite_json(self):
        self.risk["fee_per_lot"] = 1e308
        result = self.evaluate()
        self.assertEqual(result["status"], "insufficient")
        json.dumps(result, allow_nan=False)
        self.risk["fee_per_lot"] = 4
        self.plan["main_buffer_ticks"] = 1e308
        self.market["tick"] = 1e308
        result = self.evaluate()
        self.assertEqual(result["status"], "insufficient")
        json.dumps(result, allow_nan=False)

    def test_bad_zone_value_is_validation_error_not_type_error(self):
        self.plan["objects"] = [{"kind": "zone", "zone_type": [],
            "direction": "long", "period": "m30", "effect": "block",
            "points": [{"time": 1794186000000, "price": 98}, {"time": 1794196800000, "price": 106}]}]
        with self.assertRaises(ValueError):
            validate_plan(self.plan)

    def test_evaluation_does_not_mutate_inputs(self):
        before = copy.deepcopy((self.plan, self.market, self.risk))
        self.evaluate()
        self.assertEqual((self.plan, self.market, self.risk), before)

    def test_plan_activation_keeps_main_permission_but_requires_fresh_execution(self):
        self.plan["activation_at"] = "2026-11-09T09:40:00+08:00"
        result = self.evaluate()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(checks(result)["main"]["state"], "pass")
        self.assertEqual(checks(result)["retest"]["state"], "wait")
        self.market["periods"]["m3"].insert(2, bar("2026-11-09T10:03:00+08:00", 100, low=98))
        self.market["periods"]["m3"].insert(3, bar("2026-11-09T10:06:00+08:00", 103, low=100))
        result = self.evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(checks(result)["trigger"]["time"], "2026-11-09T10:06:00+08:00")

    def test_server_metadata_and_empty_context_note_validate_safely(self):
        self.plan.update(updated_by={"id": "owner", "name": "用户"}, activation_at=None,
                         activated_snapshot={}, updated_at="2026-11-09T01:00:00Z")
        self.assertEqual(self.evaluate()["status"], "ready")
        self.plan["context_note"] = None
        self.assertEqual(self.evaluate()["status"], "insufficient")

    def test_rule_manifest_missing_file_and_content_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.md"
            missing = rule_manifest(path)
            self.assertFalse(missing["available"])
            self.assertFalse(missing["compatible"])
            self.assertIsNone(missing["version"])
            data = "# 测试规则\n\n规则正文。".encode("utf-8")
            path.write_bytes(data)
            manifest = rule_manifest(path)
            self.assertEqual(manifest["title"], "测试规则")
            self.assertEqual(manifest["version"], hashlib.sha256(data).hexdigest())
            self.assertFalse(manifest["compatible"])
            self.assertEqual(manifest["compatibility_review"]["reviewed_version"], IMPLEMENTED_RULE_VERSION)
            manifest["compatibility_review"]["mapping"].clear()
            self.assertTrue(rule_manifest(path)["compatibility_review"]["mapping"])
            with patch.dict(os.environ, {"SILVER_RULES_PATH": str(path)}):
                self.assertEqual(rule_manifest()["version"], manifest["version"])


if __name__ == "__main__":
    unittest.main()
