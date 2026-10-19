"""The visual evidence layer must not grant a trade or alter frozen state."""
import copy
import unittest
from unittest.mock import patch

import outlook
import trade_plan
from test_outlook_tracking import data
from test_trade_plan import small


class MarketStructureIntegrationTests(unittest.TestCase):
    def test_visual_layer_uses_same_completed_snapshot_and_frozen_scale(self):
        bars = data()
        with patch.object(outlook, "build_market_structure", return_value={"status": "fixture"}) as build:
            analysis, state = outlook.advance_outlook(bars, symbol="TEST", tick=1, period=1800)
        self.assertEqual(build.call_args.args[0], bars)
        self.assertEqual(build.call_args.kwargs["period"], 1800)
        reference = build.call_args.kwargs["reference"]
        self.assertEqual(reference["buffer"], analysis["buffer"])
        self.assertEqual(reference["low"], state["active"]["low"])
        self.assertNotIn("market_structure", state)

    def test_visual_scenario_cannot_override_trade_plan_or_structure_state(self):
        bars = data()
        analysis, state = outlook.advance_outlook(bars, symbol="TEST", tick=1, period=1800)
        normal = trade_plan.build_plan(bars, small(), tick=1, analysis=analysis)
        changed = copy.deepcopy(analysis)
        changed["market_structure"] = {"status": "ok", "phase": {"direction": "down"},
                                        "scenarios": [{"target": 1, "condition": "visual only"}]}
        self.assertEqual(trade_plan.build_plan(bars, small(), tick=1, analysis=changed), normal)
        _, repeated = outlook.advance_outlook(bars, state, symbol="TEST", tick=1, period=1800)
        self.assertEqual(state, repeated)

    def test_unfinished_bar_changes_neither_frozen_state_nor_confirmed_visual_events(self):
        bars = data()
        analysis, state = outlook.advance_outlook(bars, symbol="TEST", tick=1, period=1800)
        changed = copy.deepcopy(bars)
        changed[-1].update(open=100, high=300, low=1, close=250)
        after, repeated = outlook.advance_outlook(changed, state, symbol="TEST", tick=1, period=1800)
        self.assertEqual(state, repeated)
        for key in ("phase", "events", "trend_lines", "candidate", "decision_bar_time"):
            self.assertEqual(analysis["market_structure"][key], after["market_structure"][key], key)


if __name__ == "__main__":
    unittest.main()
