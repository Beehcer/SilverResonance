"""Offline tests of durable structure identity and transactional updates."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from outlook_store import OutlookStore


def candles(count=90):
    start = datetime(2026, 10, 12, 9)
    return [{"dt": (start + timedelta(minutes=30*i)).isoformat(sep=" "),
             "open": 100, "high": 104, "low": 96, "close": 101 if i % 2 else 99,
             "volume": 100, "oi": 0} for i in range(count)]


class OutlookStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "structures.json"
        self.store = OutlookStore(self.path)
        self.args = {"symbol": "SHFE.ag2612", "tick": 1, "period": 1800}

    def analyze(self, bars=None, store=None, **kwargs):
        return (store or self.store).analyze(bars or candles(), **(self.args | kwargs))

    def test_restart_and_live_ticks_preserve_durable_identity_without_write(self):
        first = self.analyze()
        self.assertEqual(first["status"], "ok")
        stored = self.path.read_bytes()
        reopened = OutlookStore(self.path)
        live = candles()
        live[-1].update(close=108, high=109)
        with patch.object(reopened, "_save", side_effect=AssertionError("live-only update wrote state")):
            next_view = self.analyze(live, reopened)
        self.assertEqual(first["structure_tracking"]["active"], next_view["structure_tracking"]["active"])
        self.assertEqual(first["structure_tracking"]["events"], next_view["structure_tracking"]["events"])
        self.assertNotEqual(first["price"], next_view["price"])
        self.assertEqual(self.path.read_bytes(), stored)

    def test_atomic_save_failure_retains_memory_and_file_then_recovers(self):
        self.analyze()
        memory, saved = copy.deepcopy(self.store.data), self.path.read_bytes()
        more = candles(91)
        with patch("outlook_store.os.replace", side_effect=OSError("locked")):
            response = self.analyze(more)
        self.assertEqual(response["status"], "error")
        self.assertEqual(self.store.data, memory)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())
        self.assertEqual(self.analyze(more)["status"], "ok")
        self.assertNotEqual(self.store.data, memory)

    def test_symbol_period_tick_and_returned_objects_are_isolated(self):
        first = self.analyze()
        self.analyze(symbol="SHFE.au2612")
        self.analyze(period=180)
        self.analyze(tick=.5)
        self.assertEqual(len(self.store.data["structures"]), 4)
        first["structure_tracking"]["active"]["low"] = 0
        self.assertGreater(self.analyze()["structure_tracking"]["active"]["low"], 0)

    def test_corruption_is_not_overwritten(self):
        for bad in ("{", json.dumps({"version": 50, "structures": {}})):
            self.path.write_text(bad, encoding="utf-8")
            broken = OutlookStore(self.path)
            self.assertEqual(self.analyze(store=broken)["status"], "error")
            self.assertEqual(self.path.read_text(encoding="utf-8"), bad)

    def test_nested_corruption_is_rejected_on_load_without_resetting_history(self):
        self.analyze()
        original = copy.deepcopy(self.store.data)
        key = next(iter(original["structures"]))
        mutations = {
            "empty_state": lambda d: d["structures"].__setitem__(key, {}),
            "phase_list": lambda d: d["structures"][key]["active"].__setitem__("phase", []),
            "unknown_phase": lambda d: d["structures"][key]["active"].__setitem__("phase", "unknown"),
            "missing_seen": lambda d: d["structures"][key].pop("seen"),
            "invalid_time": lambda d: d["structures"][key]["active"].__setitem__("end_time", "bad"),
            "invalid_price": lambda d: d["structures"][key]["active"].__setitem__("atr", -1),
            "identity_mismatch": lambda d: d["structures"][key].__setitem__("symbol", "OTHER"),
            "malformed_key": lambda d: d["structures"].__setitem__("bad", d["structures"].pop(key)),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                bad = copy.deepcopy(original)
                mutate(bad)
                saved = json.dumps(bad).encode("utf-8")
                self.path.write_bytes(saved)
                reopened = OutlookStore(self.path)
                self.assertIsNotNone(reopened.load_error)
                self.assertEqual(self.analyze(store=reopened)["status"], "error")
                self.assertEqual(self.path.read_bytes(), saved)
                self.assertEqual(reopened.data["structures"], {})

    def test_json_nonfinite_constants_and_overflow_are_rejected(self):
        self.analyze()
        original = copy.deepcopy(self.store.data)
        key = next(iter(original["structures"]))
        original["structures"][key]["active"]["high"] = float("nan")
        for invalid in ("NaN", "Infinity", "-Infinity", "1e400"):
            with self.subTest(number=invalid):
                saved = json.dumps(original).replace("NaN", invalid).encode("utf-8")
                self.path.write_bytes(saved)
                reopened = OutlookStore(self.path)
                self.assertIsNotNone(reopened.load_error)
                self.assertEqual(self.analyze(store=reopened)["status"], "error")
                self.assertEqual(self.path.read_bytes(), saved)

    def test_invalid_core_state_never_commits_or_returns_an_ok_analysis(self):
        self.analyze()
        before, saved = copy.deepcopy(self.store.data), self.path.read_bytes()
        current = next(iter(before["structures"].values()))
        invalid = [None, {}, dict(current, symbol="OTHER")]
        for field, value in (("phase", []), ("high", float("nan")), ("confirmed_at", [])):
            state = copy.deepcopy(current)
            state["active"][field] = value
            invalid.append(state)
        for state in invalid:
            with self.subTest(state=type(state).__name__):
                with patch("outlook_store.outlook.advance_outlook", return_value=({"status": "ok"}, state)):
                    response = self.analyze()
                self.assertEqual(response["status"], "error")
                self.assertEqual(self.store.data, before)
                self.assertEqual(self.path.read_bytes(), saved)

    def test_initial_insufficient_state_can_restart_then_explicitly_rebuild(self):
        self.assertEqual(self.analyze(candles(10))["status"], "insufficient")
        reopened = OutlookStore(self.path)
        self.assertIsNone(reopened.load_error)
        pending = next(iter(reopened.data["structures"].values()))
        self.assertIsNone(pending["active"])
        self.assertTrue(pending["pending_rebuild"])
        response = self.analyze(store=reopened)
        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["structure_tracking"]["rebuild_reason"])

    def test_valid_tracking_version_change_is_left_for_core_to_rebuild(self):
        self.analyze()
        old = copy.deepcopy(self.store.data)
        key = next(iter(old["structures"]))
        old["structures"][key]["version"] += 1
        self.path.write_text(json.dumps(old), encoding="utf-8")
        reopened = OutlookStore(self.path)
        self.assertIsNone(reopened.load_error)
        result = self.analyze(store=reopened)
        self.assertEqual(result["status"], "ok")
        self.assertIn("版本", result["structure_tracking"]["rebuild_reason"])
        self.assertEqual(result["structure_tracking"]["previous"][-1]["status"], "rebuilt")

    def test_candidate_and_replacement_survive_validated_restart(self):
        from test_outlook_tracking import bar, data
        self.analyze(data())
        group = [bar(117, low=115, high=119)] * 6
        candidate = self.analyze(data(group[:4]))
        self.assertIsNotNone(candidate["structure_tracking"]["candidate"])
        reopened = OutlookStore(self.path)
        self.assertIsNone(reopened.load_error)
        replaced = self.analyze(data(group), store=reopened)
        self.assertEqual(replaced["status"], "ok")
        self.assertEqual(replaced["structure_tracking"]["active"]["kind"], "platform")
        again = OutlookStore(self.path)
        self.assertIsNone(again.load_error)
        self.assertEqual(self.analyze(data(group), store=again)["structure_tracking"], replaced["structure_tracking"])


if __name__ == "__main__":
    unittest.main()
