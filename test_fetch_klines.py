"""K 线下载器的离线回归测试：假 SDK、临时目录，不读取凭据或联网。"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import pandas as pd

import fetch_klines as fk


PRESET = {"symbol": "SHFE.ag2612", "name": "沪银2612", "tick": 1, "tick_value": 15}


def frame(period=3600):
    start = pd.Timestamp("2026-10-01 01:00:00", tz="UTC").value
    return pd.DataFrame({
        "datetime": [start + i * period * 10**9 for i in range(100)],
        "open": [100.0] * 100, "high": [102.0] * 100,
        "low": [99.0] * 100, "close": [101.0] * 100,
        "volume": [1000] * 100, "open_oi": [2000] * 100, "close_oi": [2010] * 100,
    })


class FakeApi:
    def __init__(self, delay=False, timeout=False, fail_seconds=(), fail_symbols=()):
        self.delay = delay
        self.timeout = timeout
        self.fail_seconds = set(fail_seconds)
        self.fail_symbols = set(fail_symbols)
        self.calls = []
        self.waits = []
        self.closed = False

    def get_kline_serial(self, symbol, sec, data_length):
        self.calls.append((symbol, sec, data_length))
        if sec in self.fail_seconds or symbol in self.fail_symbols:
            raise RuntimeError("SDK error may contain a secret: NEVER_PRINT_THIS")
        self.ready = not (self.delay or self.timeout)
        return frame(sec)

    def is_serial_ready(self, serial):
        return self.ready

    def wait_update(self, deadline):
        self.waits.append(deadline)
        self.ready = not self.timeout
        return self.ready

    def close(self):
        self.closed = True


class FetchKlinesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = self.tmp.name
        self.sleeper = mock.patch.object(fk.time, "sleep")
        self.sleeper.start()
        self.addCleanup(self.sleeper.stop)

    def run_main(self, api, args=(), refresh=None):
        output = io.StringIO()
        with mock.patch.object(fk, "create_api", return_value=api) as create, \
             mock.patch.object(fk.symbols_mod, "PRESETS", [dict(PRESET)]), \
             mock.patch.object(fk.symbols_mod, "refresh_from_api", side_effect=refresh,
                               create=True) as update, contextlib.redirect_stdout(output):
            code = fk.main(["--symbols", "ag", "--out", self.out, "--sleep", "0", *args])
        return code, output.getvalue(), create, update

    def write_json(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def read_json(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_hourly_fetch_waits_until_serial_ready(self):
        api = FakeApi(delay=True)
        bars, source = fk.fetch_period(api, PRESET, 3600, 9, 250, [])
        self.assertEqual(len(api.waits), 1)
        self.assertEqual(api.calls, [("KQ.m@SHFE.ag", 3600, 2380)])
        self.assertEqual(source, "主连 KQ.m@SHFE.ag")
        self.assertEqual(len(bars), 100)
        self.assertEqual(bars[0]["dt"], "2026-10-01 09:00:00")
        self.assertEqual(bars[0]["oi"], 10)

    def test_fallback_records_actual_contract_without_exception_secrets(self):
        api = FakeApi(fail_symbols={"KQ.m@SHFE.ag"})
        notes = []
        bars, source = fk.fetch_period(api, PRESET, 30, 1050, 250, notes)
        self.assertEqual(source, "具体合约 SHFE.ag2612")
        self.assertEqual(len(bars), 100)
        self.assertEqual(api.calls[-1][2], fk.FREE_KLINE_CAP)
        self.assertEqual(len(notes), 1)
        self.assertNotIn("NEVER_PRINT_THIS", str(notes))

    def test_initialization_timeout_never_serializes_placeholder_data(self):
        api = FakeApi(timeout=True)
        notes = []
        with mock.patch.object(fk, "to_bars") as convert:
            self.assertEqual(fk.fetch_period(api, PRESET, 3600, 9, 250, notes), (None, None))
        convert.assert_not_called()
        self.assertEqual(len(api.waits), 2)
        self.assertEqual(len(notes), 2)

    def test_invalid_price_rows_are_excluded_from_json(self):
        df = frame()
        df.loc[0, "open"] = float("nan")
        df.loc[1, "high"] = float("inf")
        bars = fk.to_bars(df, 3600)
        self.assertEqual(len(bars), 98)
        json.dumps(bars, allow_nan=False)

    def test_missing_hourly_cache_downloads_and_updates_both_consumers(self):
        api = FakeApi()
        code, output, create, update = self.run_main(api, ["--periods", "1h"])
        self.assertEqual(code, 0)
        create.assert_called_once()
        update.assert_called_once_with(api)
        self.assertTrue(api.closed)
        self.assertIn("成功 1，跳过 0，失败 0", output)
        modern = self.read_json(fk.kline_path(self.out, PRESET["symbol"], "1h"))
        legacy = self.read_json(fk.legacy_1h_path(self.out, PRESET))
        self.assertEqual(modern["bars"], legacy["bars"])
        self.assertEqual(modern["source"], "主连 KQ.m@SHFE.ag")
        self.assertEqual(legacy["days"], 250)
        self.assertEqual(datetime.fromisoformat(modern["fetched_at"]).utcoffset(), timedelta(hours=8))

    def test_force_updates_all_five_periods_even_with_existing_hourly_cache(self):
        for label in fk._PERIOD_BY_LABEL:
            self.write_json(fk.kline_path(self.out, PRESET["symbol"], label), {"stale": True})
        self.write_json(fk.legacy_1h_path(self.out, PRESET), {"bars": [{"dt": "2000-01-01 00:00"}]})
        api = FakeApi()
        code, output, _, _ = self.run_main(api, ["--force"])
        self.assertEqual(code, 0)
        self.assertEqual({sec for _, sec, _ in api.calls}, {86400, 3600, 900, 180, 30})
        self.assertEqual(len(api.calls), 5)
        self.assertIn("成功 5，跳过 0，失败 0", output)
        for label in fk._PERIOD_BY_LABEL:
            self.assertEqual(len(self.read_json(fk.kline_path(self.out, PRESET["symbol"], label))["bars"]), 100)

    def test_legacy_copy_preserves_source_and_does_not_invent_fetch_time(self):
        old = {"symbol": PRESET["symbol"], "source": "主连 KQ.m@SHFE.ag", "days": 250,
               "bars": [{"dt": "2026-10-01 09:00", "close": 100}]}
        self.write_json(fk.legacy_1h_path(self.out, PRESET), old)
        code, _, create, update = self.run_main(FakeApi(), ["--periods", "1h"])
        self.assertEqual(code, 0)
        create.assert_not_called()
        update.assert_not_called()
        copied = self.read_json(fk.kline_path(self.out, PRESET["symbol"], "1h"))
        self.assertEqual(copied["source"], old["source"])
        self.assertIsNone(copied["fetched_at"])
        self.assertIn("cached_at", copied)
        self.assertEqual(copied["bars"][0]["dt"], "2026-10-01 09:00:00")

    def test_existing_output_is_counted_as_skip_without_connecting(self):
        self.write_json(fk.kline_path(self.out, PRESET["symbol"], "1h"), {"kept": True})
        code, output, create, _ = self.run_main(FakeApi(), ["--periods", "1h"])
        self.assertEqual(code, 0)
        create.assert_not_called()
        self.assertIn("成功 0，跳过 1，失败 0", output)

    def test_partial_download_failure_returns_nonzero_and_preserves_old_file(self):
        path = fk.kline_path(self.out, PRESET["symbol"], "15m")
        self.write_json(path, {"kept": True})
        api = FakeApi(fail_seconds={900})
        code, output, _, _ = self.run_main(api, ["--periods", "1h,15m", "--force"])
        self.assertEqual(code, 1)
        self.assertIn("成功 1，跳过 0，失败 1", output)
        self.assertEqual(self.read_json(path), {"kept": True})
        self.assertNotIn("NEVER_PRINT_THIS", output)
        self.assertTrue(api.closed)

    def test_main_contract_refresh_remaps_selected_product_before_download(self):
        new = {**PRESET, "symbol": "SHFE.ag2706", "name": "沪银2706"}

        def refresh(api):
            fk.symbols_mod.PRESETS = [new]

        api = FakeApi(fail_symbols={"KQ.m@SHFE.ag"})
        code, _, _, update = self.run_main(api, ["--symbols", "SHFE.ag2608",
                                                "--periods", "1h", "--force"], refresh)
        self.assertEqual(code, 0)
        update.assert_called_once()
        self.assertEqual(api.calls[-1][0], "SHFE.ag2706")
        self.assertTrue(os.path.exists(fk.kline_path(self.out, new["symbol"], "1h")))
        self.assertFalse(os.path.exists(fk.kline_path(self.out, PRESET["symbol"], "1h")))

    def test_refresh_failure_cannot_download_under_estimated_contract(self):
        api = FakeApi()
        code, output, _, _ = self.run_main(api, ["--periods", "1h,15m"], RuntimeError("secret"))
        self.assertEqual(code, 1)
        self.assertEqual(api.calls, [])
        self.assertIn("成功 0，跳过 0，失败 2", output)
        self.assertNotIn("secret", output)
        self.assertTrue(api.closed)

    def test_failed_atomic_write_preserves_old_json_and_cleans_tempfile(self):
        path = os.path.join(self.out, "original.json")
        self.write_json(path, {"kept": True})
        with self.assertRaises(ValueError):
            fk.atomic_write_json(path, {"bad": float("nan")})
        self.assertEqual(self.read_json(path), {"kept": True})
        self.assertEqual(os.listdir(self.out), ["original.json"])


if __name__ == "__main__":
    unittest.main()
