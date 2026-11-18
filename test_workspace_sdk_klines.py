"""Offline source-adapter regressions; never instantiate TqApi or log in."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import numpy as np

from workspace_sdk_klines import (FIELDS, SDKKlineSnapshotError, UnsupportedSDKKlineSource,
                                  WorkspaceSDKKlines)


SYMBOL = "SHFE.ag2702"
DURATION = "180000000000"
BASE = 1795009140000000000


def candle(row_id, **changes):
    row = dict(zip(FIELDS, [BASE + row_id * int(DURATION), 10959, 10979, 10952, 10971, 6638, 10000, 10005]))
    row.update(changes)
    return row


class AttrDict(dict):
    __getattr__ = dict.__getitem__


class Source:
    def __init__(self, width=4, last_id=None):
        last_id = width - 1 if last_id is None else last_id
        self.root = {"_path": ["klines", SYMBOL, DURATION], "last_id": last_id,
                     "data": {str(i): candle(i) for i in range(last_id + 1)}}
        self.default = dict(zip(FIELDS, [0, float("nan"), float("nan"), float("nan"), float("nan"), 0, 0, 0]))
        self.array = np.zeros((width, 1 + len(FIELDS)))
        self.frame = {"id": self.array[:, 1]}
        self.frame["datetime"] = self.array[:, 0]
        self.frame.update({field: self.array[:, index + 1] for index, field in enumerate(FIELDS[1:], 1)})
        self.meta = {"root": [self.root], "array": self.array, "width": width, "default": self.default,
                     "adj_type": None, "init": True, "chart": {"ready": True, "ins_list": SYMBOL,
                     "duration": int(DURATION)}, "extra_array": {}, "df": self.frame}
        self.api = SimpleNamespace(_serials={id(self.frame): self.meta}, _diffs=[], _sync_diffs=[],
                                   _data=AttrDict(quotes={}, klines={SYMBOL: {DURATION: self.root}}))
        self.sync_frame()
        self.adapter = WorkspaceSDKKlines(self.api, sdk_version="3.10.1")

    def sync_frame(self, tail=None):
        ids = range(self.root["last_id"] - self.meta["width"] + 1, self.root["last_id"] + 1)
        rows = [[(self.root["data"].get(str(i)) if i >= 0 else self.default)["datetime"], i] +
                [(self.root["data"].get(str(i)) if i >= 0 else self.default)[key] for key in FIELDS[1:]] for i in ids]
        if tail is None:
            self.array[:] = rows
        else:
            self.array[-tail:] = rows[-tail:]

    def update(self, row_id, **changes):
        self.root["data"][str(row_id)].update(changes)
        self.pump({str(row_id): changes})

    def pump(self, data):
        self.api._diffs = [{"klines": {SYMBOL: {DURATION: {"data": data}}}}]
        self.api._sync_diffs = self.api._diffs
        return self.adapter.observe()

    def snapshot(self):
        return self.adapter.snapshot(SYMBOL, 180, self.frame)


class SDKKlineSnapshots(unittest.TestCase):
    def test_initial_snapshot_uses_received_root_even_without_diff(self):
        source = Source()
        source.root["data"]["1"].update(high=10977, close=10970)
        result = source.snapshot()
        self.assertEqual(result["high"][1], 10977)
        self.assertEqual(source.frame["high"][1], 10979)
        self.assertEqual(source.adapter.diagnostics(SYMBOL, 180)["frame_mismatch_rows"], 1)

    def test_multiple_pumps_accumulate_closed_corrections_before_refresh(self):
        source = Source()
        before = source.snapshot()
        source.update(0, high=10977, close=10970)
        source.update(1, close=10966, volume=5511)
        source.api._diffs = source.api._sync_diffs = []
        source.adapter.observe()
        after = source.snapshot()
        self.assertEqual(list(after["close"][:2]), [10970, 10966])
        self.assertEqual(list(before["close"][:2]), [10971, 10971])
        self.assertEqual(after["volume"][1], 5511)
        self.assertFalse(source.adapter.has_pending(SYMBOL, 180))
        self.assertEqual(source.adapter.diagnostics(SYMBOL, 180)["late_revision_rows"], 2)

    def test_unchanged_snapshot_shares_readonly_columns_and_checks_only_tail(self):
        source = Source(width=1000)
        first = source.snapshot()
        before = source.adapter.diagnostics(SYMBOL, 180, limit=0)["root_rows_read"]
        second = source.snapshot()
        self.assertIs(first, second)
        self.assertEqual(source.adapter.diagnostics(SYMBOL, 180, limit=0)["root_rows_read"] - before, 2)
        with self.assertRaises(ValueError):
            first["high"][0] = 10980

    def test_only_changed_rows_and_tail_are_read_after_initialization(self):
        source = Source(width=1000)
        source.snapshot()
        before = source.adapter.diagnostics(SYMBOL, 180, limit=0)["root_rows_read"]
        source.update(10, high=10977)
        source.snapshot()
        self.assertEqual(source.adapter.diagnostics(SYMBOL, 180, limit=0)["root_rows_read"] - before, 3)

    def test_rolling_window_preserves_corrections_by_id_not_row_position(self):
        source = Source()
        source.snapshot()
        source.update(2, high=10977)
        old = source.snapshot()
        source.root["last_id"] = 4
        source.root["data"]["4"] = candle(4, high=10984)
        source.sync_frame()  # SDK rolling itself can still leave old corrected rows stale.
        source.frame["high"][1] = 10979
        source.pump({"4": source.root["data"]["4"]})
        new = source.snapshot()
        self.assertEqual(list(new["high"]), [10979, 10977, 10979, 10984])
        self.assertEqual(list(old["high"]), [10979, 10979, 10977, 10979])

    def test_roll_and_late_revision_in_same_pump(self):
        source = Source()
        source.snapshot()
        source.root["last_id"] = 4
        source.root["data"]["4"] = candle(4)
        source.sync_frame()
        source.root["data"]["1"]["close"] = 10966
        source.pump({"1": {"close": 10966}, "4": source.root["data"]["4"]})
        self.assertEqual(source.snapshot()["close"][0], 10966)

    def test_dropped_old_id_correction_cannot_touch_reused_array_slot(self):
        source = Source()
        source.snapshot()
        source.update(0, high=10977)
        source.root["last_id"] = 4
        source.root["data"]["4"] = candle(4)
        source.sync_frame()
        self.assertEqual(list(source.snapshot()["high"]), [10979] * 4)

    def test_newest_tail_reads_current_root_even_without_observe(self):
        source = Source()
        source.snapshot()
        source.root["data"]["3"]["close"] = 10966
        self.assertEqual(source.snapshot()["close"][-1], 10966)

    def test_same_bar_high_downward_correction_is_not_clamped(self):
        source = Source()
        source.snapshot()
        source.update(3, high=10977)
        self.assertEqual(source.snapshot()["high"][-1], 10977)

    def test_partial_diff_is_not_used_as_full_row(self):
        source = Source()
        source.snapshot()
        source.root["data"]["0"].update(high=10977, close=10970)
        source.pump({"0": {"high": 10978}})  # Superseded diff; root already newer.
        result = source.snapshot()
        self.assertEqual((result["high"][0], result["close"][0]), (10977, 10970))

    def test_deleted_required_row_fails_retains_pending_and_recovers(self):
        source = Source()
        before = source.snapshot()
        original = source.root["data"].pop("0")
        source.pump({"0": None})
        with self.assertRaisesRegex(SDKKlineSnapshotError, "root_row_missing"):
            source.snapshot()
        self.assertTrue(source.adapter.has_pending(SYMBOL, 180))
        self.assertEqual(before["close"][0], 10971)
        source.root["data"]["0"] = {**original, "close": 10970}
        after = source.snapshot()
        self.assertEqual(after["close"][0], 10970)
        self.assertIsNone(source.adapter.diagnostics(SYMBOL, 180)["error"])

    def test_parent_data_delete_requires_full_rebuild(self):
        source = Source()
        source.snapshot()
        source.api._diffs = [{"klines": {SYMBOL: {DURATION: {"data": None}}}}]
        source.api._sync_diffs = []
        source.adapter.observe()
        source.root["data"] = {}
        with self.assertRaisesRegex(SDKKlineSnapshotError, "root_row_missing"):
            source.snapshot()

    def test_parent_symbol_reset_rebuilds_all_retained_rows(self):
        source = Source()
        source.snapshot()
        source.root["data"]["0"]["high"] = 10977
        source.api._diffs = [{"klines": {SYMBOL: None}}]
        source.api._sync_diffs = []
        self.assertIn((SYMBOL, 180), source.adapter.observe())
        self.assertEqual(source.snapshot()["high"][0], 10977)

    def test_parent_global_reset_rebuilds_without_falling_back_to_frame(self):
        source = Source()
        source.snapshot()
        source.root["data"]["0"]["high"] = 10977
        source.api._diffs = [{"klines": None}]
        source.api._sync_diffs = []
        source.adapter.observe()
        self.assertEqual(source.snapshot()["high"][0], 10977)

    def test_invalid_root_values_fail_without_publishing_partial_changes(self):
        for changes in ({"high": None}, {"high": float("nan")}, {"high": 10950},
                        {"volume": -1}, {"volume": 1.5}, {"close": "10970"}):
            with self.subTest(changes=changes):
                source = Source()
                before = source.snapshot()
                source.update(0, **changes)
                with self.assertRaises(SDKKlineSnapshotError):
                    source.snapshot()
                self.assertEqual(before["high"][0], 10979)
                self.assertTrue(source.adapter.has_pending(SYMBOL, 180))

    def test_root_frame_identity_guards(self):
        mutators = [lambda s: s.root.update(last_id=2),
                    lambda s: s.root.update(_path=["klines", "DCE.p2701", DURATION]),
                    lambda s: s.meta["chart"].update(duration=900000000000),
                    lambda s: s.meta.update(adj_type="F"),
                    lambda s: s.meta.update(init=False),
                    lambda s: s.root["data"]["0"].update(datetime=BASE + 1),
                    lambda s: s.frame["id"].__setitem__(1, 2)]
        for mutate in mutators:
            with self.subTest(mutate=mutate):
                source = Source()
                mutate(source)
                with self.assertRaises(SDKKlineSnapshotError):
                    source.snapshot()

    def test_timestamp_rewrite_for_same_id_fails_even_if_both_sources_changed(self):
        source = Source()
        source.snapshot()
        source.root["data"]["0"]["datetime"] += int(DURATION) // 2
        source.frame["datetime"][0] += int(DURATION) // 2
        source.pump({"0": {"datetime": source.root["data"]["0"]["datetime"]}})
        with self.assertRaisesRegex(SDKKlineSnapshotError, "datetime_changed"):
            source.snapshot()

    def test_detached_old_root_cannot_override_current_sdk_tree(self):
        source = Source()
        source.snapshot()
        source.api._data["klines"][SYMBOL][DURATION] = deepcopy(source.root)
        source.pump({"0": {"close": 10970}})
        with self.assertRaisesRegex(SDKKlineSnapshotError, "detached"):
            source.snapshot()

    def test_non_chronological_root_rows_cannot_publish(self):
        source = Source()
        source.root["data"]["1"]["datetime"] = source.root["data"]["0"]["datetime"]
        source.sync_frame()
        with self.assertRaisesRegex(SDKKlineSnapshotError, "timestamps_invalid"):
            source.snapshot()

    def test_provider_id_rewind_reloads_all_overlapping_rows(self):
        source = Source(width=4, last_id=8)
        source.snapshot()
        source.root["last_id"] = 7
        source.sync_frame()
        source.root["data"]["5"].update(high=10977, close=10970)
        source.api._diffs = [{"klines": {SYMBOL: {DURATION: {"last_id": 7}}}}]
        source.api._sync_diffs = []
        source.adapter.observe()
        result = source.snapshot()
        self.assertEqual((result["high"][1], result["close"][1]), (10977, 10970))

    def test_normal_bar_close_on_roll_is_not_counted_as_late_revision(self):
        source = Source()
        source.snapshot()
        source.root["data"]["3"]["close"] = 10970
        source.root["last_id"] = 4
        source.root["data"]["4"] = candle(4)
        source.sync_frame()
        source.pump({"3": {"close": 10970}, "4": source.root["data"]["4"]})
        self.assertEqual(source.snapshot()["close"][-2], 10970)
        self.assertEqual(source.adapter.diagnostics(SYMBOL, 180, limit=0)["late_revision_rows"], 0)

    def test_prehistory_nan_rows_stay_empty(self):
        source = Source(width=4, last_id=1)
        result = source.snapshot()
        self.assertTrue(np.all(np.isnan(result["close"][:2])))
        self.assertEqual(list(result["close"][-2:]), [10971, 10971])

    def test_duplicate_diff_reads_count_once_and_persistent_mismatch_not_recounted(self):
        source = Source()
        source.snapshot()
        source.update(0, high=10977)
        source.adapter.observe()
        source.snapshot()
        stats = source.adapter.diagnostics(SYMBOL, 180, limit=0)
        source.adapter.observe()
        source.snapshot()
        after = source.adapter.diagnostics(SYMBOL, 180, limit=0)
        self.assertEqual((stats["late_revision_rows"], stats["frame_mismatch_rows"]), (1, 1))
        self.assertEqual((after["late_revision_rows"], after["frame_mismatch_rows"]), (1, 1))

    def test_diagnostics_bounded_and_include_raw_frame_and_snapshot(self):
        source = Source(width=8)
        source.snapshot()
        source.update(0, high=10977)
        source.snapshot()
        result = source.adapter.diagnostics(SYMBOL, 180, limit=999)
        self.assertLessEqual(len(result["rows"]), 5)
        row = next(row for row in result["rows"] if row["id"] == 0)
        self.assertEqual((row["raw"]["high"], row["frame"]["high"], row["snapshot"]["high"]), (10977, 10979, 10977))
        self.assertEqual(set(row["raw"]), set(FIELDS))

    def test_other_symbol_period_diffs_do_not_modify_current_rows(self):
        source = Source()
        first = source.snapshot()
        source.api._diffs = [{"klines": {"DCE.p2701": {DURATION: {"data": {"0": {"high": 9000}}}},
                                        SYMBOL: {"900000000000": {"data": {"0": {"high": 8000}}}}}}]
        source.api._sync_diffs = []
        changed = source.adapter.observe()
        self.assertNotIn((SYMBOL, 180), changed)
        self.assertIs(source.snapshot(), first)

    def test_unsupported_sdk_and_private_shape_are_explicit(self):
        source = Source()
        with self.assertRaisesRegex(UnsupportedSDKKlineSource, "version"):
            WorkspaceSDKKlines(source.api, sdk_version="3.10.2")
        with self.assertRaisesRegex(UnsupportedSDKKlineSource, "registry"):
            WorkspaceSDKKlines(SimpleNamespace(), sdk_version="3.10.1")

    def test_installed_sdk_closed_bar_update_blind_spot_is_repaired_without_mutation(self):
        try:
            from test_workspace_sdk_objects import import_sdk_objects
            TqApi = import_sdk_objects()[0]
            from importlib.metadata import version
        except ImportError:
            self.skipTest("TQSDK not installed; adapter fake-source tests still run")
        if version("tqsdk") != "3.10.1":
            self.skipTest("Pinned 3.10.1 implementation reproduction")
        source = Source(width=3)
        source.snapshot()
        source.root["data"]["1"].update(open=10971, high=10978, close=10965, volume=5502)
        source.sync_frame()
        source.root["data"]["0"].update(high=10977, close=10970)
        source.root["data"]["1"].update(close=10966, volume=5511)
        # Invoke the installed implementation on an in-memory receiver only.
        # No constructor, TqAuth, sockets, event loop or SDK login is involved.
        TqApi._update_serial_single(source.api, source.meta)
        self.assertEqual(source.meta["update_row"], 2)
        self.assertEqual(list(source.frame["high"][:2]), [10979, 10978])
        self.assertEqual(list(source.frame["close"][:2]), [10971, 10965])
        root_before, frame_before = deepcopy(source.root), source.array.copy()
        source.pump({"0": {"high": 10977, "close": 10970}, "1": {"close": 10966, "volume": 5511}})
        result = source.snapshot()
        self.assertEqual(list(result["high"][:2]), [10977, 10978])
        self.assertEqual(list(result["close"][:2]), [10970, 10966])
        self.assertEqual(list(result["volume"][:2]), [6638, 5511])
        np.testing.assert_array_equal(frame_before, source.array)
        self.assertEqual(root_before, source.root)


if __name__ == "__main__":
    unittest.main()
