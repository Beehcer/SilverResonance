"""Planned price lines never substitute for the confirmed execution trigger."""
import copy
import unittest
from unittest.mock import patch

from test_workspace_rules import VERSION, fixture
from workspace_rules import estimate_planned_risk, evaluate_plan, validate_plan
from workspace_store import WorkspaceStore


class PlannedEntryTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.market, self.risk = fixture()
        self.plan["planned_entry_price"] = 100

    def evaluate(self):
        return evaluate_plan(self.plan, self.market, self.risk, VERSION)

    def test_planned_price_and_live_price_have_independent_risk(self):
        value = self.evaluate()
        self.assertEqual(value["status"], "ready")
        self.assertEqual(value["planned_risk"]["total_risk"], 128)
        self.assertEqual(value["risk"]["total_risk"], 188)
        self.assertFalse(value["planned_risk"]["permission"])
        self.market["price"] = 104
        self.assertEqual(self.evaluate()["planned_risk"]["total_risk"], 128)
        self.assertEqual(self.evaluate()["risk"]["total_risk"], 208)

    def test_line_cannot_create_missing_trigger_or_authorize_stale_data(self):
        self.market["periods"]["m3"] = self.market["periods"]["m3"][:1]
        self.assertNotEqual(self.evaluate()["status"], "ready")
        self.assertTrue(self.evaluate()["planned_risk"]["qualified"])
        self.market["fresh"] = False
        self.assertNotEqual(self.evaluate()["status"], "ready")

    def test_no_line_retains_legacy_plan_behavior(self):
        del self.plan["planned_entry_price"]
        result = self.evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["planned_risk"]["complete"])

    def test_draft_can_switch_direction_before_repositioning_stop(self):
        self.plan.update(enabled=False, direction="short", context_confirmed=False)
        self.assertEqual(validate_plan(self.plan)["direction"], "short")
        result = self.evaluate()
        self.assertFalse(result["planned_risk"]["valid"])
        self.assertIn("高于", result["planned_risk"]["reason"])
        self.plan["enabled"] = True
        with self.assertRaises(ValueError):
            validate_plan(self.plan)

    def test_short_stop_risk_mirrors_long_without_using_quote_price(self):
        short = dict(self.plan, direction="short", planned_entry_price=100, stop_price=105, obstacle_price=90)
        estimate = estimate_planned_risk(short, self.market, self.risk)
        self.assertEqual(estimate["total_risk"], 128)
        self.assertEqual(estimate["net_obstacle_room"], 172)
        self.assertTrue(estimate["qualified"])

    def test_invalid_price_or_wrong_tick_never_looks_qualified(self):
        for price in (0, -1, float("inf"), float("nan"), True):
            with self.subTest(price=price), self.assertRaises(ValueError):
                validate_plan(dict(self.plan, planned_entry_price=price))
        self.plan["planned_entry_price"] = 100.1
        self.assertEqual(self.evaluate()["status"], "invalid")
        self.assertFalse(self.evaluate()["planned_risk"]["valid"])

    def test_plan_line_outside_permission_zone_blocks_ready(self):
        self.plan["planned_entry_price"] = 106
        result = self.evaluate()
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any(item["key"] == "planned_entry" and item["state"] == "fail" for item in result["checks"]))

    def test_estimate_can_show_stop_amount_without_claiming_obstacle_or_signal(self):
        draft = {"direction": "long", "planned_entry_price": 100, "stop_price": 95}
        estimate = estimate_planned_risk(draft, self.market, self.risk)
        self.assertTrue(estimate["complete"])
        self.assertIsNone(estimate["net_obstacle_room"])
        self.assertFalse(estimate["permission"])
        self.assertNotEqual(evaluate_plan(draft, self.market, self.risk, VERSION)["status"], "ready")

    def test_daily_limit_and_private_fees_are_not_assumed(self):
        for missing in ("lots", "fee_per_lot", "slippage_ticks", "budget", "daily_loss"):
            risk = {key: val for key, val in self.risk.items() if key != missing}
            self.assertFalse(estimate_planned_risk(self.plan, self.market, risk)["complete"])
        risk = dict(self.risk, daily_loss=900)
        self.assertFalse(estimate_planned_risk(self.plan, self.market, risk)["qualified"])

    def test_shared_line_patch_resets_activation_and_preserves_trigger(self):
        store = WorkspaceStore()
        token = store.anonymous_session()
        user = store.authenticate(token)
        with patch("workspace_store._now", return_value="2026-11-09T09:30:00+00:00"):
            original = store.save_plan("TEST.future", 0, self.plan, user["id"], VERSION)
        with patch("workspace_store._now", return_value="2026-11-09T09:35:00+00:00"):
            changed = store.patch_plan("TEST.future", original["revision"], {"planned_entry_price": 102}, [], [], user["id"], VERSION)
        self.assertEqual(changed["trigger_price"], original["trigger_price"])
        self.assertNotEqual(changed["activation_at"], original["activation_at"])
        self.assertEqual(changed["activated_snapshot"]["planned_entry_price"], 102)
        original_snapshot = copy.deepcopy(original["activated_snapshot"])
        self.assertEqual(original_snapshot["planned_entry_price"], 100)


if __name__ == "__main__":
    unittest.main()
