"""Offline checks against real SDK objects, without constructing a TqApi.

The receiver only closes serial initialization coroutines. It cannot log in,
open sockets, pump updates, or subscribe to a market-data service.
"""
from importlib.metadata import PackageNotFoundError, version
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import logging

import numpy as np

from workspace_sdk_klines import FIELDS, SUPPORTED_VERSION, WorkspaceSDKKlines


SYMBOL = "SHFE.ag2702"
DURATION = "180000000000"
BASE = 1795009140000000000


def import_sdk_objects():
    # shinny_structlog replaces Python's process-global logger manager on
    # import. Keep real SDK class checks isolated from unrelated test logging.
    with patch.object(logging, "getLogger", logging.getLogger), \
            patch.object(logging, "_loggerClass", logging.getLoggerClass()), \
            patch.object(logging.Logger, "root", logging.Logger.root), \
            patch.object(logging.Logger, "manager", logging.Logger.manager), \
            patch.dict(logging._levelToName), patch.dict(logging._nameToLevel):
        from tqsdk.api import TqApi
        from tqsdk.entity import Entity
        from tqsdk.objs import Kline
        from tqsdk.objs_not_entity import TqDataFrame
    return TqApi, Entity, Kline, TqDataFrame


class RealSDKObjectSnapshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            installed = version("tqsdk")
        except PackageNotFoundError:
            raise unittest.SkipTest("TQSDK is not installed") from None
        if installed != SUPPORTED_VERSION:
            raise unittest.SkipTest("Real-object checks require pinned TQSDK " + SUPPORTED_VERSION)
        # Import classes only. Never invoke TqApi(), TqAuth(), or SDK I/O.
        cls.TqApi, cls.Entity, cls.Kline, cls.TqDataFrame = import_sdk_objects()

    def make_source(self, width=4, last_id=3):
        api = SimpleNamespace(_serials={}, _diffs=[], _sync_diffs=[], closed_coroutines=0)

        def close_coroutine(coroutine, **_kwargs):
            coroutine.close()
            api.closed_coroutines += 1
            return None

        # No event loop, connection, authentication, or wait_update exists.
        api.create_task = close_coroutine

        def entity(path, **values):
            value = self.Entity()
            value._instance_entity(path)
            value.update(values)
            return value

        root_path = ["klines", SYMBOL, DURATION]
        data = entity(root_path + ["data"])
        for row_id in range(last_id + 1):
            row = self.Kline(api)
            row._instance_entity(root_path + ["data", str(row_id)])
            row.update(dict(zip(FIELDS, [BASE + row_id * int(DURATION),
                                        10959, 10979, 10952, 10971, 6638, 10000, 10005])))
            data[str(row_id)] = row
        root = entity(root_path, last_id=last_id, data=data)
        symbol_root = entity(["klines", SYMBOL], **{DURATION: root})
        api._data = entity([], quotes=entity(["quotes"]),
                           klines=entity(["klines"], **{SYMBOL: symbol_root}))
        # Exercise the installed initializer and its real TqDataFrame manager.
        meta = self.TqApi._init_serial(api, [root], width, self.Kline(api), None)
        meta["chart"] = entity(["charts", "offline"], ready=True, ins_list=SYMBOL,
                               duration=int(DURATION))
        api._serials[id(meta["df"])] = meta
        self.TqApi._update_serial_single(api, meta)
        adapter = WorkspaceSDKKlines(api)
        return api, root, meta, adapter

    def test_real_serial_initialization_is_supported_and_readonly(self):
        api, root, meta, adapter = self.make_source()
        columns = adapter.snapshot(SYMBOL, 180, meta["df"])
        self.assertIsInstance(meta["df"], self.TqDataFrame)
        self.assertIsInstance(root, self.Entity)
        self.assertIsInstance(meta["default"], self.Kline)
        self.assertIsInstance(root["data"]["0"], self.Kline)
        self.assertIs(api._serials[id(meta["df"])], meta)
        self.assertIs(meta["init"], True)
        self.assertIsNone(meta["adj_type"])
        self.assertEqual(api.closed_coroutines, 1)
        self.assertEqual(set(columns), set(FIELDS))
        for field in FIELDS:
            np.testing.assert_array_equal(columns[field], meta["df"][field].to_numpy())
            self.assertFalse(columns[field].flags.writeable)

    def test_late_closed_revision_uses_real_root_without_mutating_real_frame(self):
        api, root, meta, adapter = self.make_source()
        before = adapter.snapshot(SYMBOL, 180, meta["df"])
        frame_before = meta["df"].copy(deep=True)
        root["data"]["0"].update(high=10977, close=10970)
        root["data"]["1"].update(close=10966, volume=5511)
        # The pinned implementation refreshes only the last row here.
        self.TqApi._update_serial_single(api, meta)
        self.assertEqual(meta["update_row"], 3)
        self.assertEqual(meta["df"]["high"].iloc[0], 10979)
        api._sync_diffs = [{"klines": {SYMBOL: {DURATION: {"data": {
            "0": {"high": 10977, "close": 10970}, "1": {"close": 10966, "volume": 5511}
        }}}}}]
        self.assertIn((SYMBOL, 180), adapter.observe())
        after = adapter.snapshot(SYMBOL, 180, meta["df"])
        self.assertEqual(list(after["close"][:2]), [10970, 10966])
        self.assertEqual(after["high"][0], 10977)
        self.assertEqual(after["volume"][1], 5511)
        self.assertEqual(before["high"][0], 10979)
        self.assertTrue(meta["df"].equals(frame_before))
        self.assertEqual(root["data"]["0"]["high"], 10977)
        self.assertFalse(adapter.has_pending(SYMBOL, 180))

    def test_real_default_preserves_prehistory_empty_rows(self):
        api, root, meta, adapter = self.make_source(width=6, last_id=1)
        before = adapter.snapshot(SYMBOL, 180, meta["df"])
        self.assertEqual(list(meta["df"]["id"]), [-4, -3, -2, -1, 0, 1])
        self.assertTrue(np.all(np.isnan(before["close"][:4])))
        self.assertTrue(np.all(before["datetime"][:4] == 0))
        root["data"]["0"].update(high=10977)
        api._sync_diffs = [{"klines": {SYMBOL: {DURATION: {"data": {"0": {"high": 10977}}}}}}]
        adapter.observe()
        after = adapter.snapshot(SYMBOL, 180, meta["df"])
        self.assertTrue(np.all(np.isnan(after["high"][:4])))
        self.assertTrue(np.all(after["datetime"][:4] == 0))
        self.assertEqual(list(after["high"][-2:]), [10977, 10979])
        self.assertEqual(list(meta["df"]["high"][-2:]), [10979, 10979])


if __name__ == "__main__":
    unittest.main()
