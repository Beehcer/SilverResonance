"""Offline audit-backed repairs, using anonymous fixtures and no application feed."""
import copy
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.repair_forecast_anchors import build_repairs


def stamp(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


BASE = stamp("2026-11-10T14:30:00+08:00")
STEP = 1800000


def fixture():
    point = {"id": "node-a", "label": "1", "time": BASE + 2.25 * STEP, "price": 101.25}
    wave = {"id": "wave-a", "kind": "wave", "period": "m30", "source_period": "m30", "points": [point]}
    plans = [{"symbol": "TEST.contract", "revision": 1, "objects": []},
             {"symbol": "TEST.contract", "revision": 2, "objects": [wave]}]
    third = copy.deepcopy(plans[-1]); third["revision"] = 3
    third["objects"][0].update(color="#123456", note="style edit")
    third["objects"][0]["points"][0]["price"] = 104.75
    plans.append(third)
    history = [{"revision": plan["revision"], "at": at, "plan": plan} for plan, at in zip(plans, [
        "2026-11-10T14:40:00+08:00", "2026-11-10T15:10:00+08:00", "2026-11-10T22:10:00+08:00"])]
    market = {"symbol": "TEST.contract", "incremental": False, "as_of": "2026-11-11T10:00:00+08:00",
              "periods": {"m30": [{"time": BASE - STEP}, {"time": BASE},
                                     {"time": stamp("2026-11-10T21:00:00+08:00")}]}}
    return copy.deepcopy(plans[-1]), history, market


class ForecastAnchorRepairTests(unittest.TestCase):
    def test_uses_last_time_change_not_later_style_or_price_and_never_mutates(self):
        plan, history, market = fixture(); before = copy.deepcopy((plan, history, market))
        report = build_repairs(plan, list(reversed(history)), market)
        repair = report["repairs"][0]
        self.assertEqual(repair["evidence"]["revision"], 2)
        self.assertEqual(repair["node_update"]["changes"], {"time": BASE + 2.25 * STEP,
                         "chart_anchor": {"period": "m30", "base_time": BASE, "offset": 2.25}})
        self.assertEqual(repair["expected_point"], plan["objects"][0]["points"][0])
        self.assertEqual(set(repair["node_update"]["changes"]), {"time", "chart_anchor"})
        self.assertEqual((plan, history, market), before)

    def test_latest_horizontal_adjustment_supersedes_original_creation(self):
        plan, history, market = fixture()
        for target in (plan, history[-1]["plan"]):
            target["objects"][0]["points"][0]["time"] = stamp("2026-11-10T22:30:00+08:00")
        repair = build_repairs(plan, history, market)["repairs"][0]
        self.assertEqual(repair["evidence"]["revision"], 3)
        self.assertEqual(repair["evidence"]["base_time"], stamp("2026-11-10T21:00:00+08:00"))
        self.assertEqual(repair["evidence"]["offset"], 3)

    def test_same_timestamp_reappearing_uses_latest_change(self):
        plan, history, market = fixture()
        history[1]["plan"]["objects"][0]["points"][0]["time"] -= STEP
        self.assertEqual(build_repairs(plan, history, market)["skipped"][0]["reason"], "historical_point")

    def test_explicit_null_and_existing_anchor_are_not_repaired(self):
        for anchor, reason in [(None, "explicit_absolute_time"), ({"period": "m30", "base_time": BASE, "offset": 2}, "already_anchored")]:
            plan, history, market = fixture()
            for target in (plan, history[-1]["plan"]):
                target["objects"][0]["points"][0]["chart_anchor"] = anchor
            report = build_repairs(plan, history, market)
            self.assertEqual(report["repairs"], [])
            self.assertEqual(report["skipped"][0]["reason"], reason)

    def test_prior_explicit_anchor_removal_is_not_inferred_back(self):
        plan, history, market = fixture()
        history[1]["plan"]["objects"][0]["points"][0]["chart_anchor"] = None
        self.assertEqual(build_repairs(plan, history, market)["skipped"][0]["reason"], "prior_explicit_coordinate")

    def test_truncated_first_snapshot_and_revision_gaps_are_not_evidence(self):
        plan, history, market = fixture()
        self.assertEqual(build_repairs(plan, history[1:], market)["skipped"][0]["reason"], "no_proven_time_change")
        self.assertEqual(build_repairs(plan, history[::2], market)["skipped"][0]["reason"], "audit_gap")

    def test_period_change_requires_manual_review(self):
        plan, history, market = fixture()
        history[1]["plan"]["objects"][0]["source_period"] = "m3"
        self.assertEqual(build_repairs(plan, history, market)["skipped"][0]["reason"], "period_or_kind_changed")

    def test_stale_plan_scope_and_incomplete_market_fail_closed(self):
        for mutation in ("plan", "scope", "partial", "incremental"):
            plan, history, market = fixture()
            if mutation == "plan": plan["revision"] += 1
            elif mutation == "scope": history[0]["plan"]["board_id"] = "other-board"
            else: market[mutation] = True
            with self.assertRaises(ValueError): build_repairs(plan, history, market)

    def test_historical_or_excessively_distant_points_are_skipped(self):
        for delta, reason in [(0, "historical_point"), (10001, "offset_exceeds_limit")]:
            plan, history, market = fixture()
            for target in (plan, history[1]["plan"], history[2]["plan"]): target["objects"][0]["points"][0]["time"] = BASE + delta * STEP
            self.assertEqual(build_repairs(plan, history, market)["skipped"][0]["reason"], reason)

    def test_missing_or_incomplete_source_history_is_not_reconstructed(self):
        for mode in ("missing", "counts", "naive"):
            plan, history, market = fixture()
            if mode == "missing": market["periods"]["m30"] = market["periods"]["m30"][-1:]
            elif mode == "counts": market["period_counts"] = {"m30": 100}
            else: history[1]["at"] = "2026-11-10T15:10:00"
            self.assertEqual(build_repairs(plan, history, market)["repairs"], [])

    def test_inserted_node_is_proven_by_adjacent_existing_object_snapshots(self):
        plan, history, market = fixture()
        history[0]["plan"]["objects"] = [{**copy.deepcopy(plan["objects"][0]), "points": []}]
        self.assertEqual(build_repairs(plan, history, market)["repairs"][0]["evidence"]["change"], "inserted")

    def test_daily_trading_date_requires_actual_intraday_start_evidence(self):
        plan, history, market = fixture()
        day = stamp("2026-11-11T00:00:00+08:00")
        for target in (plan, history[1]["plan"], history[2]["plan"]):
            target["objects"][0].update(period="day", source_period="day")
            target["objects"][0]["points"][0]["time"] = day + 86400000 * 1.5
        history[1]["at"] = "2026-11-10T21:10:00+08:00"
        market["periods"]["day"] = [{"time": day, "session_boundary": "2026-11-10T18:00:00+08:00"}]
        report = build_repairs(plan, history, market)
        self.assertEqual(report["repairs"][0]["evidence"]["base_time"], day)
        market["periods"]["m30"] = []
        self.assertEqual(build_repairs(plan, history, market)["skipped"][0]["reason"], "daily_start_not_proven")

    def test_cli_creates_only_new_local_review_artifact(self):
        plan, history, market = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); snapshot = path / "snapshot.json"; audit = path / "audit.json"; output = path / "review.json"
            snapshot.write_text(json.dumps({"plan": plan, "market": market}), encoding="utf8")
            audit.write_text(json.dumps(history), encoding="utf8")
            cmd = [sys.executable, "scripts/repair_forecast_anchors.py", "--snapshot", str(snapshot), "--history", str(audit), "--output", str(output)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["repairs"], 1)
            self.assertNotIn("price", result.stdout)
            before = output.read_bytes()
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
