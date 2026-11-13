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
             "SHFE.ss2611", "DCE.m2701", "DCE.jm2701", "CZCE.MA701",
             "CZCE.RM705", "DCE.jd2612", "CZCE.AP612", "CZCE.TA705",
             "DCE.l2705", "DCE.p2612", "DCE.pp2612", "GFEX.lc2612"]


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

    def test_new_products_are_available_offline_with_correct_tick_value(self):
        self.assertEqual(len(symbols.PRESETS), 20)
        self.assertEqual(
            [(row["symbol"], row["name"], row["tick"], row["tick_value"])
             for row in symbols.PRESETS[12:]],
            [("CZCE.RM701", "菜粕701", 1.0, 10.0),
             ("DCE.jd2701", "鸡蛋2701", 1.0, 10.0),
             ("CZCE.AP701", "苹果701", 1.0, 10.0),
             ("CZCE.TA701", "PTA701", 2.0, 10.0),
             ("DCE.l2701", "塑料2701", 1.0, 5.0),
             ("DCE.p2701", "棕榈2701", 1.0, 10.0),
             ("DCE.pp2701", "聚丙烯2701", 1.0, 5.0),
             ("GFEX.lc2611", "碳酸锂2611", 20.0, 20.0)])
        for row, multiplier in zip(symbols.PRESETS[16:], (5, 10, 5, 1)):
            self.assertEqual(row["tick_value"] / row["tick"], multiplier)
            self.assertNotIn("expire_datetime", row, "Calendar defaults must not look SDK-verified.")

    def test_new_products_use_verified_main_and_live_contract_parameters(self):
        api, _, quotes = fake_api()
        for quote, tick, multiplier in zip(quotes[12:16], (1, 1, 1, 2), (10, 10, 10, 5)):
            quote.price_tick, quote.volume_multiple = tick, multiplier
        result = symbols.refresh_from_api(api, TODAY)
        self.assertEqual(api.get_quote_list.call_args_list[0].args[0][12:16],
                         ["KQ.m@CZCE.RM", "KQ.m@DCE.jd", "KQ.m@CZCE.AP", "KQ.m@CZCE.TA"])
        self.assertEqual([p["symbol"] for p in result[12:16]], CONTRACTS[12:16])
        self.assertEqual([p["name"] for p in result[12:16]],
                         ["菜粕705", "鸡蛋2612", "苹果612", "PTA705"])
        for row in result[12:16]:
            with self.subTest(symbol=row["symbol"]):
                cfg = SimpleNamespace(SYMBOL=row["symbol"])
                symbols.apply_symbol_params(cfg)
                self.assertEqual(cfg.TICK_VALUE, 10.0)
                self.assertEqual(cfg.PRICE_TICK, row["tick"])
        self.assertEqual(symbols.rollover_if_expired("DCE.jd2601"), "DCE.jd2612")
        self.assertEqual(symbols.rollover_if_expired("CZCE.TA601"), "CZCE.TA705")

    def test_plastic_and_palm_resolve_actual_main_and_parameters_in_one_atomic_batch(self):
        api, _, quotes = fake_api()
        # Deliberately differ from calendar codes and one offline tick: the SDK
        # owns both contract selection and risk metadata, including future changes.
        quotes[16].price_tick, quotes[16].volume_multiple = 1, 5
        quotes[17].price_tick, quotes[17].volume_multiple = 2, 10
        result = symbols.refresh_from_api(api, TODAY)
        self.assertEqual(api.get_quote_list.call_count, 2)
        self.assertEqual(api.get_quote_list.call_args_list[0].args[0][16:18], ["KQ.m@DCE.l", "KQ.m@DCE.p"])
        self.assertEqual(api.get_quote_list.call_args_list[1].args[0][16:18], CONTRACTS[16:18])
        self.assertEqual([(p["symbol"], p["name"], p["tick"], p["tick_value"]) for p in result[16:18]],
                         [("DCE.l2705", "塑料2705", 1, 5), ("DCE.p2612", "棕榈2612", 2, 20)])
        for old, expected in (("DCE.l2609", CONTRACTS[16]), ("DCE.p2609", CONTRACTS[17])):
            self.assertEqual(symbols.rollover_if_expired(old), expected)
            cfg = SimpleNamespace(SYMBOL=expected)
            symbols.apply_symbol_params(cfg)
            self.assertEqual((cfg.PRICE_TICK, cfg.TICK_VALUE),
                             (symbols.find(expected)["tick"], symbols.find(expected)["tick_value"]))
        saved = json.loads(symbols.contracts_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["presets"], result)
        self.assertEqual(len(saved["presets"]), 20)

    def test_polypropylene_resolves_exact_product_without_colliding_with_palm(self):
        api, _, quotes = fake_api()
        quotes[18].price_tick, quotes[18].volume_multiple = 1, 5
        result = symbols.refresh_from_api(api, TODAY)
        self.assertEqual(api.get_quote_list.call_count, 2)
        self.assertEqual(api.get_quote_list.call_args_list[0].args[0][18], "KQ.m@DCE.pp")
        self.assertEqual(api.get_quote_list.call_args_list[1].args[0][18], "DCE.pp2612")
        self.assertEqual(result[18]["name"], "聚丙烯2612")
        cfg = SimpleNamespace(SYMBOL="DCE.pp2612")
        symbols.apply_symbol_params(cfg)
        self.assertEqual((cfg.PRICE_TICK, cfg.TICK_VALUE), (1, 5))
        self.assertEqual(symbols.rollover_if_expired("DCE.pp2609"), "DCE.pp2612")
        self.assertEqual(symbols.rollover_if_expired("DCE.p2609"), "DCE.p2612")
        self.assertEqual(symbols.refresh_months(TODAY)[18], result[18], "Verified pp contract must survive a cache reload.")

    def test_lithium_uses_sdk_main_contract_and_parameters_instead_of_calendar_placeholder(self):
        self.assertEqual(symbols.PRESETS[-1]["symbol"], "GFEX.lc2611")
        api, _, quotes = fake_api()
        quotes[-1].price_tick, quotes[-1].volume_multiple = 20, 1
        result = symbols.refresh_from_api(api, TODAY)
        self.assertEqual(api.get_quote_list.call_args_list[0].args[0][-1], "KQ.m@GFEX.lc")
        self.assertEqual(api.get_quote_list.call_args_list[1].args[0][-1], "GFEX.lc2612")
        self.assertEqual(result[-1]["name"], "碳酸锂2612")
        cfg = SimpleNamespace(SYMBOL="GFEX.lc2612")
        symbols.apply_symbol_params(cfg)
        self.assertEqual((cfg.PRICE_TICK, cfg.TICK_VALUE), (20, 20))
        self.assertEqual(symbols.rollover_if_expired("GFEX.lc2609"), "GFEX.lc2612")
        self.assertEqual(symbols.rollover_if_expired("DCE.l2609"), "DCE.l2705")
        self.assertEqual(symbols.refresh_months(TODAY)[-1], result[-1])
        # Future contract metadata changes must come from the actual quote.
        api, _, quotes = fake_api()
        quotes[-1].price_tick, quotes[-1].volume_multiple = 10, 2
        result = symbols.refresh_from_api(api, TODAY)
        self.assertEqual((result[-1]["tick"], result[-1]["tick_value"]), (10, 20))

    def test_lithium_monthly_fallback_rolls_year_without_claiming_verified_main(self):
        row = symbols._build_presets(date(2026, 11, 13))[-1]
        self.assertEqual(row["symbol"], "GFEX.lc2612")
        row = symbols._build_presets(date(2026, 12, 27))[-1]
        self.assertEqual(row["symbol"], "GFEX.lc2702")
        self.assertNotIn("expire_datetime", row)

    def test_known_product_caches_survive_expansion_without_rewrite(self):
        payload = self.populate_cache()
        for count in (12, 16, 18, 19):
            with self.subTest(count=count):
                legacy = {**payload, "presets": payload["presets"][:count]}
                symbols.contracts_path().write_text(json.dumps(legacy), encoding="utf-8")
                saved = symbols.contracts_path().read_bytes()
                result = symbols.refresh_months(TODAY)
                self.assertEqual(result[:count], legacy["presets"])
                self.assertEqual(result[count:], symbols._build_presets(TODAY)[count:])
                self.assertEqual(symbols.contracts_path().read_bytes(), saved)
                self.assertEqual(symbols.refresh_months(TODAY + timedelta(days=8)),
                                 symbols._build_presets(TODAY + timedelta(days=8)))

    def test_other_incomplete_or_invalid_legacy_caches_are_rejected(self):
        good = self.populate_cache()
        for count in (11, 13, 15, 17):
            payload = deepcopy(good)
            payload["presets"] = payload["presets"][:count]
            symbols.contracts_path().write_text(json.dumps(payload), encoding="utf-8")
            with self.subTest(count=count):
                self.assertEqual(symbols.refresh_months(TODAY), symbols._build_presets(TODAY))
        payload = deepcopy(good)
        payload["presets"] = payload["presets"][:12]
        payload["presets"][0]["symbol"] = "SHFE.au2612"
        symbols.contracts_path().write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(symbols.refresh_months(TODAY), symbols._build_presets(TODAY))

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
        for index in (-4, -3, -2, -1):
            for target in ("actual", "main"):
                with self.subTest(new_product=CONTRACTS[index], target=target):
                    api, main, actual = fake_api()
                    if target == "actual":
                        actual[index].expired = True
                    else:
                        main[index].underlying_symbol = CONTRACTS[-2 if index == -1 else -1]
                    with self.assertRaises(ValueError):
                        symbols.refresh_from_api(api, TODAY)
                    self.assertEqual(symbols.PRESETS, before)
                    self.assertEqual(symbols.contracts_path().read_bytes(), saved)

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
        self.assertEqual(len(loaded["PRESETS"]), 20)
        self.assertFalse(symbols.contracts_path().exists())


if __name__ == "__main__":
    unittest.main()
