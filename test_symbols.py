"""离线主力映射与缓存回归：python -m unittest test_symbols -v。"""

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import symbols


TODAY = date(2026, 10, 19)
CONTRACTS = ["SHFE.ag2611", "SHFE.au2612", "SHFE.rb2701", "CZCE.SA701",
             "CZCE.FG701", "SHFE.bu2612", "CZCE.CF701", "SHFE.sp2701",
             "SHFE.ss2611", "DCE.m2701", "DCE.jm2701", "CZCE.MA701"]


def fake_api():
    main = [SimpleNamespace(underlying_symbol=s) for s in CONTRACTS]
    actual = [SimpleNamespace(instrument_id=s, expired=False, price_tick=0.5,
                              volume_multiple=15,
                              expire_datetime=datetime(2027, 1, 20, tzinfo=symbols._BEIJING).timestamp())
              for s in CONTRACTS]
    return Mock(get_quote_list=Mock(side_effect=[main, actual])), main, actual


class SymbolsTests(unittest.TestCase):
    def setUp(self):
        self.old_presets = deepcopy(symbols.PRESETS)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = patch.object(symbols.user_settings, "app_dir", return_value=self.temp.name)
        self.directory.start()
        self.addCleanup(self.directory.stop)
        self.addCleanup(lambda: symbols.PRESETS.__setitem__(slice(None), self.old_presets))
        symbols.refresh_months(TODAY)

    def populate_cache(self):
        api, _, _ = fake_api()
        symbols.refresh_from_api(api, TODAY)
        return json.loads(symbols.contracts_path().read_text(encoding="utf-8"))

    def test_no_cache_calendar_fallback_uses_given_date(self):
        self.assertEqual(symbols.PRESETS[0]["symbol"], "SHFE.ag2612")
        self.assertEqual(symbols.PRESETS[3]["symbol"], "CZCE.SA701")
        symbols.refresh_months(date(2026, 5, 27))
        self.assertEqual(symbols.PRESETS[0]["symbol"], "SHFE.ag2612")
        symbols.refresh_months(date(2026, 5, 26))
        self.assertEqual(symbols.PRESETS[0]["symbol"], "SHFE.ag2606")
        self.assertFalse(symbols.contracts_path().exists())

    def test_api_mapping_is_batched_and_parameters_come_from_quote(self):
        api, _, quotes = fake_api()
        quotes[1].price_tick, quotes[1].volume_multiple = 0.02, 1000
        old_reference = symbols.PRESETS
        result = symbols.refresh_from_api(api, TODAY)
        self.assertIs(result, old_reference)
        self.assertEqual(api.get_quote_list.call_args_list[0].args[0],
                         [f"KQ.m@{t[1]}.{t[2]}" for t in symbols._TEMPLATES])
        self.assertEqual(api.get_quote_list.call_args_list[1].args[0], CONTRACTS)
        self.assertEqual(api.get_quote_list.call_count, 2)
        self.assertEqual([p["symbol"] for p in result], CONTRACTS)
        self.assertEqual(symbols.name_of(CONTRACTS[0]), "沪银2611")
        self.assertEqual(symbols.find(CONTRACTS[0])["tick_value"], 7.5)
        self.assertAlmostEqual(result[1]["tick_value"], 20)
        self.assertIsNone(symbols.find("SHFE.nonexistent"))
        cfg = SimpleNamespace(SYMBOL=CONTRACTS[1])
        symbols.apply_symbol_params(cfg)
        self.assertEqual((cfg.PRICE_TICK, cfg.TICK_VALUE), (0.02, 20))
        self.assertEqual(symbols.rollover_if_expired("SHFE.ag2606"), CONTRACTS[0])
        saved = json.loads(symbols.contracts_path().read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"schema_version", "source", "fetched_at", "presets"})
        self.assertEqual(saved["source"], "tqsdk")
        self.assertEqual(saved["fetched_at"], "2026-10-19T00:00:00+08:00")
        self.assertEqual(saved["presets"], result)
        self.assertEqual(list(symbols.contracts_path().parent.glob("*.tmp")), [])

    def test_cache_valid_for_seven_days_but_not_future_or_stale(self):
        self.populate_cache()
        for day in (TODAY, TODAY + timedelta(days=7)):
            self.assertEqual(symbols.refresh_months(day)[0]["symbol"], CONTRACTS[0])
        for day in (TODAY - timedelta(days=1), TODAY + timedelta(days=8)):
            self.assertEqual(symbols.refresh_months(day), symbols._build_presets(day))

    def test_invalid_cache_falls_back_without_network(self):
        good = self.populate_cache()
        cases = []
        for field, value in (("source", "unknown"), ("schema_version", 2), ("schema_version", True),
                             ("fetched_at", "2026-10-19T00:00:00"),
                             ("fetched_at", "not-a-date"), ("presets", [])):
            candidate = deepcopy(good)
            candidate[field] = value
            cases.append(candidate)
        for field, value in (("symbol", "SHFE.au2611"), ("symbol", "SHFE.ag2512"),
                             ("tick", float("nan")), ("tick_value", -1),
                             ("tick", True), ("expire_datetime", 1)):
            candidate = deepcopy(good)
            candidate["presets"][0][field] = value
            cases.append(candidate)
        cases.extend([[], None, {"source": "tqsdk", "schema_version": 1}])
        for candidate in cases:
            with self.subTest(candidate=candidate):
                symbols.contracts_path().write_text(json.dumps(candidate), encoding="utf-8")
                self.assertEqual(symbols.refresh_months(TODAY), symbols._build_presets(TODAY))
        symbols.contracts_path().write_text("{broken", encoding="utf-8")
        self.assertEqual(symbols.refresh_months(TODAY), symbols._build_presets(TODAY))

    def test_cache_expiry_and_datetime_clock_are_respected(self):
        payload = self.populate_cache()
        noon = datetime(2026, 10, 19, 12, tzinfo=symbols._BEIJING)
        payload["presets"][0]["expire_datetime"] = noon.timestamp()
        symbols.contracts_path().write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(symbols.refresh_months(TODAY)[0]["symbol"], CONTRACTS[0])
        self.assertEqual(symbols.refresh_months(noon), symbols._build_presets(TODAY))

    def test_bad_api_item_never_partially_updates_cache_or_presets(self):
        self.populate_cache()
        before = deepcopy(symbols.PRESETS)
        saved = symbols.contracts_path().read_bytes()
        cases = [("main", "underlying_symbol", "DCE.ag2611"),
                 ("main", "underlying_symbol", "SHFE.au2611"),
                 ("main", "underlying_symbol", ""),
                 ("main", "underlying_symbol", "SHFE.ag9912"),
                 ("actual", "expired", True), ("actual", "instrument_id", "SHFE.au2611"),
                 ("actual", "price_tick", float("nan")), ("actual", "price_tick", 0),
                 ("actual", "volume_multiple", float("inf")),
                 ("actual", "volume_multiple", -1), ("actual", "expire_datetime", 1),
                 ("actual", "expire_datetime", float("nan"))]
        for target, field, value in cases:
            with self.subTest(target=target, field=field, value=value):
                api, main, actual = fake_api()
                setattr((main if target == "main" else actual)[0], field, value)
                with self.assertRaises(ValueError):
                    symbols.refresh_from_api(api, TODAY)
                self.assertEqual(symbols.PRESETS, before)
                self.assertEqual(symbols.contracts_path().read_bytes(), saved)
        api, _, actual = fake_api()
        actual[-1].expired = True  # 前十一项有效也不能先写入。
        with self.assertRaises(ValueError):
            symbols.refresh_from_api(api, TODAY)
        self.assertEqual(symbols.PRESETS, before)

    def test_incomplete_batch_and_api_failure_leave_presets_unchanged(self):
        before = deepcopy(symbols.PRESETS)
        for responses in ([[]], [fake_api()[1], []]):
            api = Mock(get_quote_list=Mock(side_effect=responses))
            with self.assertRaises(ValueError):
                symbols.refresh_from_api(api, TODAY)
            self.assertEqual(symbols.PRESETS, before)
        api = Mock(get_quote_list=Mock(side_effect=RuntimeError("offline")))
        with self.assertRaises(RuntimeError):
            symbols.refresh_from_api(api, TODAY)
        self.assertEqual(symbols.PRESETS, before)
        self.assertFalse(symbols.contracts_path().exists())

    def test_failed_atomic_replace_preserves_previous_file_and_memory(self):
        self.populate_cache()
        before = deepcopy(symbols.PRESETS)
        saved = symbols.contracts_path().read_bytes()
        api, _, quotes = fake_api()
        quotes[0].price_tick = 5
        with patch.object(symbols.os, "replace", side_effect=PermissionError("read-only")):
            with self.assertRaises(PermissionError):
                symbols.refresh_from_api(api, TODAY)
        self.assertEqual(symbols.PRESETS, before)
        self.assertEqual(symbols.contracts_path().read_bytes(), saved)
        self.assertEqual(list(symbols.contracts_path().parent.glob("*.tmp")), [])

    def test_cache_path_uses_program_directory_when_frozen(self):
        self.directory.stop()
        with patch.object(symbols.user_settings.sys, "frozen", True, create=True), \
                patch.object(symbols.user_settings.sys, "executable", str(Path(self.temp.name) / "SilverResonance.exe")):
            self.assertEqual(symbols.contracts_path(), Path(self.temp.name) / "state" / "contracts.json")

    def test_import_never_opens_network_or_reads_credentials(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch.object(symbols.user_settings, "load_settings", side_effect=AssertionError("credentials forbidden")):
            loaded = runpy.run_path(symbols.__file__)
        self.assertEqual(len(loaded["PRESETS"]), 12)
        self.assertFalse(symbols.contracts_path().exists())


if __name__ == "__main__":
    unittest.main()
