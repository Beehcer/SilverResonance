"""Automatic warmup uses the existing SDK, bounded turns, and honest archives."""
import asyncio
import copy
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import app
from workspace_history import WorkspaceHistoryArchive
from workspace_market import WorkspaceMarketCache, PERIODS, iso, timestamp, reference_id
from workspace_warmup import CatalogueWarmup, ArchiveWarmer, source_history_length
import test_workspace_production_feed as production_tests
FakeAPI = production_tests.FakeAPI

DAY = [["09:00:00", "10:15:00"], ["10:30:00", "11:30:00"], ["13:30:00", "15:00:00"]]


class WarmupSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.now, self.symbols = timestamp("2026-11-15T12:00:00+08:00"), ["A", "B", "C"]
        self.warmup = CatalogueWarmup(lambda: self.symbols, clock=lambda: self.now)
        self.missing = set(self.symbols)

    def choose(self, priorities=()):
        return self.warmup.select(priorities, lambda symbol: symbol in self.missing)

    def ready(self, symbol):
        self.missing.discard(symbol)
        return self.warmup.observe(symbol, {p: {"count": 5, "error": None} for p in PERIODS})

    def test_no_viewer_starts_catalogue_then_rotates_after_ready(self):
        self.assertEqual(self.choose(), ["A"])
        self.assertEqual(self.choose(), [])
        self.assertTrue(self.ready("A"))
        self.assertFalse(self.ready("A"))
        self.assertEqual(self.choose(), ["B"])
        self.ready("B")
        self.assertEqual(self.choose(), ["C"])
        self.ready("C")
        self.assertEqual(self.choose(), [])
        self.assertEqual(self.warmup.status()["ready"], 3)

    def test_current_view_takes_precedence_and_background_wait_is_bounded(self):
        self.assertEqual(self.choose(["C"]), ["C"])
        self.missing.remove("C")
        self.assertEqual(self.choose(["C"]), [])
        self.now += 31
        self.assertEqual(self.choose(["C"]), ["A"])
        self.missing.remove("A")
        self.now += 31
        self.assertEqual(self.choose(), ["B"])
        self.assertIn("超时", self.warmup.status()["symbols"][0]["error"])

    def test_failure_retries_with_backoff_and_does_not_stall_other_contracts(self):
        self.choose()
        self.warmup.subscription_failed("A", ConnectionError("private credentials must not be exposed"))
        self.assertEqual(self.choose(), ["B"])
        self.ready("B")
        self.assertEqual(self.choose(), ["C"])
        self.ready("C")
        self.assertEqual(self.choose(), [])
        self.now += 2
        self.assertEqual(self.choose(), ["A"])
        for _ in range(100):
            self.warmup.subscription_failed("A", ValueError())
        state = self.warmup.status()["symbols"][0]
        self.assertEqual(state["next_retry_at"], self.now + 60)
        self.assertNotIn("private", str(self.warmup.status()))

    def test_partial_periods_never_claim_ready_and_catalogue_changes_are_seen(self):
        self.choose()
        self.warmup.observe("A", {"m3": {"count": 100}})
        self.assertFalse(self.warmup.status()["symbols"][0]["ready"])
        self.symbols[:] = ["B", "C", "D"]
        self.missing.add("D")
        self.assertNotIn("A", self.warmup.symbols())
        visited = []
        for _ in range(3):
            chosen = self.choose()[0]
            visited.append(chosen)
            self.ready(chosen)
        self.assertEqual(set(visited), {"B", "C", "D"})

    def test_source_budget_uses_day_and_night_sessions_and_retains_margin(self):
        now = timestamp("2026-11-15T12:00:00+08:00")
        day = source_history_length({"day": DAY, "night": []}, now)
        short = source_history_length({"day": DAY, "night": [["21:00:00", "23:00:00"]]}, now)
        late = source_history_length({"day": DAY, "night": [["21:00:00", "26:30:00"]]}, now)
        self.assertTrue(2100 <= day <= 2304)
        self.assertTrue(3200 <= short <= 3456)
        self.assertTrue(5100 <= late <= 5504)
        self.assertEqual(late, source_history_length({"day": DAY, "night": [["21:00:00", "02:30:00"]]}, now))
        self.assertTrue(all(value % 128 == 0 for value in (day, short, late)))
        with self.assertRaises(ValueError):
            source_history_length(None, now)

    def test_background_preview_and_observation_do_not_promote_foreground_priority(self):
        cache = WorkspaceMarketCache(clock=lambda: self.now)
        cache.snapshot("A", {}, {}, preview=True, request_data=False)
        cache.snapshot("B", {}, {}, request_data=False)
        cache.coverage_status("C")
        self.assertEqual(cache.requested(), [])
        cache.snapshot("A", {}, {})
        self.now += 1
        cache.snapshot("B", {}, {})
        self.assertEqual(cache.prioritized_requests(), ["B", "A"])
        self.now += 60
        self.assertEqual(cache.prioritized_requests(), ["B"])
        self.now += 1
        self.assertEqual(cache.prioritized_requests(), [])
        self.assertEqual(set(cache.requested()), {"A", "B"})


class ArchiveWarmerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = timestamp("2026-11-15T12:00:00+08:00")
        self.archive = WorkspaceHistoryArchive(Path(self.directory.name)/"history", clock=lambda: self.now)
        self.symbols = ["A", "B"]
        self.cache = WorkspaceMarketCache(clock=lambda: self.now)
        self.reads = []
        self.worker = ArchiveWarmer(lambda: self.symbols, self.snapshot, self.archive, clock=lambda: self.now)

    def snapshot(self, symbol):
        self.reads.append(symbol)
        return self.cache.snapshot(symbol, {}, {"connected": True}, request_data=False)

    def seed(self, symbol):
        for period in PERIODS.values():
            self.cache.cache(symbol, period, [{"dt": iso(self.now - (5-i)*period), "open": 100,
                                              "high": 104, "low": 98, "close": 101} for i in range(6)])

    def test_first_complete_history_archives_all_symbols_without_viewer_or_oss(self):
        for symbol in self.symbols:
            self.seed(symbol)
        self.assertTrue(self.worker.step())
        self.assertEqual(self.reads, ["A"])
        self.assertTrue(self.worker.step())
        self.assertEqual(self.reads, self.symbols)
        self.assertFalse(self.worker.step())
        self.assertEqual(self.cache.requested(), [])
        for symbol in self.symbols:
            recovered = self.archive.restore(symbol)
            self.assertIsNone(recovered["market"]["history_token"])
            self.assertFalse(recovered["market"]["fresh"])

    def test_saved_age_is_actual_disk_age_even_when_checks_repeat(self):
        self.seed("A")
        self.worker.step()
        original = self.archive.metadata("A")["saved_at"]
        self.now += 901
        self.worker.notify_ready("A")
        self.worker.step()
        state = next(item for item in self.worker.status()["symbols"] if item["symbol"] == "A")
        self.assertEqual(state["saved_at"], original)
        self.assertEqual(state["checked_at"], self.now)
        self.assertGreater(state["checked_at"], state["saved_at"])

    def test_none_empty_and_many_pending_symbols_are_fair_without_false_ready(self):
        self.symbols[:] = [str(i) for i in range(32)]
        def pending(symbol):
            self.reads.append(symbol)
            return None if int(symbol) % 2 else {}
        self.worker.snapshot_provider = pending
        for _ in range(64):
            self.worker.step()
            self.now += .25
        self.assertEqual(set(self.reads[:32]), set(self.symbols))
        self.assertEqual(set(self.reads[32:]), set(self.symbols))
        self.assertTrue(all(item["status"] == "waiting_source" for item in self.worker.status()["symbols"]))

    def test_initial_ready_notification_bypasses_waiting_source_retry_and_is_bounded(self):
        self.worker.step()
        self.seed("A")
        for _ in range(100):
            self.worker.notify_ready("A")
        self.assertEqual(self.worker.urgent, ["A"])
        self.worker.step()
        self.assertEqual(self.reads, ["A", "A"])
        self.assertIsNotNone(self.archive.restore("A"))

    def test_archive_error_retries_and_other_contract_does_not_wait(self):
        for symbol in self.symbols:
            self.seed(symbol)
        with patch.object(self.archive, "bootstrap", side_effect=OSError("secret path")):
            self.worker.step()
        self.worker.step()
        state = self.worker.status()["symbols"]
        self.assertEqual(state[0]["status"], "retrying")
        self.assertEqual(state[0]["error"], "OSError")
        self.assertEqual(state[1]["status"], "archived")
        self.now += 5
        self.worker.step()
        self.assertEqual(self.worker.status()["symbols"][0]["status"], "archived")

    def test_worker_start_is_singleton_and_stop_joins_without_sdk_access(self):
        self.worker.start()
        first = self.worker.thread
        self.worker.start()
        self.assertIs(first, self.worker.thread)
        self.assertTrue(self.worker.stop())


class ProductionCatalogueTests(unittest.TestCase):
    setUp = production_tests.ProductionFeedTests.setUp
    run_session = production_tests.ProductionFeedTests.run_session

    def test_preview_priority_is_disabled_only_for_live_production_auto_warmup(self):
        symbol = self.presets[0]["symbol"]
        no_archive = types.SimpleNamespace(preview=lambda *args, **kwargs: None)
        with patch.object(app, "_workspace_history", no_archive), patch.object(app.config, "MOCK_MODE", False), \
                patch.dict(app.app.config, {"WORKSPACE_PREWARM": True}):
            app._workspace_market_provider(symbol, preview=True)
            self.assertEqual(self.cache.requested(), [])
            app._workspace_market_provider(symbol)
            self.assertEqual(self.cache.prioritized_requests(), [symbol])
            self.cache.requests.clear()
            with patch.dict(app.app.config, {"WORKSPACE_PREWARM": False}):
                app._workspace_market_provider(symbol, preview=True)
                self.assertEqual(self.cache.requested(), [symbol])
            self.cache.requests.clear()
            with patch.object(app.config, "MOCK_MODE", True):
                app._workspace_market_provider(symbol, preview=True)
                self.assertEqual(self.cache.requested(), [symbol])

    def test_all_twenty_contracts_automatically_warm_in_same_sdk_loop_without_viewer(self):
        self.presets.extend({"symbol": "EX.p" + str(index), "tick": 1, "tick_value": 10} for index in range(16, 20))
        api = FakeAPI(self, [{"at": index + 1, "received": True} for index in range(24)])
        owner, original = threading.get_ident(), api.get_kline_serial
        def subscribe(*args):
            self.assertIs(asyncio.get_running_loop(), api.loop)
            self.assertEqual(threading.get_ident(), owner)
            return original(*args)
        with patch.dict(app.app.config, {"WORKSPACE_PREWARM": True}), patch.object(api, "get_kline_serial", side_effect=subscribe):
            self.run_session(api)
        self.assertEqual(len(api.calls), 20 * 3)
        self.assertEqual(len(set(api.calls)), len(api.calls))
        self.assertEqual(self.cache.requested(), [])
        self.assertEqual(app._workspace_warmup.status()["ready"], 20)
        self.assertEqual(len(api.quote_calls), 1)

    def test_unready_or_broken_first_background_contract_does_not_starve_second(self):
        codes = [item["symbol"] for item in self.presets[:2]]
        warmup = CatalogueWarmup(lambda: codes, clock=lambda: self.now, timeout=1)
        api = FakeAPI(self, [])
        reader = app._WorkspaceSerialReader(api, self.cache, warmup=warmup, quotes=api.quotes)
        reader.subscribe_requested(codes)
        first_frames = {id(serial) for key, serial in api.frames.items() if key[0] == codes[0]}
        with patch.object(api, "is_serial_ready", side_effect=lambda serial: id(serial) not in first_frames):
            reader.refresh(True)
        self.assertTrue(all(item["count"] for item in self.cache.coverage_status(codes[1]).values()))
        # Conversion failure also releases the next turn: rotate among cold
        # background symbols instead of reselecting the broken first one.
        other_cache = WorkspaceMarketCache(clock=lambda: self.now)
        reader = app._WorkspaceSerialReader(api, other_cache, warmup=warmup, quotes=api.quotes)
        reader.subscribe_requested(codes)
        for key, serial in api.frames.items():
            if key[0] == codes[0]:
                serial["low"][-1] = 200
        reader.refresh(True)
        reader.refresh(True)
        self.assertTrue(all(item["count"] for item in other_cache.coverage_status(codes[1]).values()))

    def test_fixed_reference_skips_native_subscription_and_preserves_original_mapping(self):
        symbol = self.presets[0]["symbol"]
        reference = {"basis": "native_v1", "target_basis": "trading_minutes_v1",
                     "source_times": [1000, 2000, 3000], "target_base_time": 3000}
        self.cache.reference_loader = lambda code: copy.deepcopy(reference)
        warmup = CatalogueWarmup(lambda: [symbol], clock=lambda: self.now)
        api = FakeAPI(self, [])
        api.quotes[symbol].trading_time = {"day": DAY, "night": [["21:00:00", "23:00:00"]]}
        reader = app._WorkspaceSerialReader(api, self.cache, quotes=api.quotes, trading_minutes=True, warmup=warmup)
        reader.schedule_requested()
        api.loop.run_until_complete(asyncio.sleep(0))
        self.assertEqual([period for _, period, _ in api.calls], [86400, 180, 900])
        self.assertTrue(3200 <= api.calls[-1][2] <= 3456)
        self.assertEqual(self.cache.references[symbol], reference)
        self.assertEqual(self.cache.reference_ids[symbol], reference_id(reference))
        pending = self.cache.snapshot(symbol, {}, {"connected": True})
        self.assertEqual(pending["anchor_reference"]["m30"], reference)
        self.assertEqual(pending["anchor_reference_id"], reference_id(reference))
        # A restored fixed reference makes the native serial unnecessary for
        # actual m30 publication and coverage, not merely for subscriptions.
        rows = [{"dt": iso(self.now - (2-i)*1800), "open": 100, "high": 104,
                 "low": 98, "close": 101} for i in range(3)]
        for period in (86400, 180):
            self.cache.cache(symbol, period, rows)
        self.assertTrue(self.cache.publish_trading_m30(symbol, rows, {"complete": True}))
        self.assertNotIn((symbol, 1800), self.cache.data)
        self.assertTrue(warmup.observe(symbol, self.cache.coverage_status(symbol)))
        complete = self.cache.snapshot(symbol, {}, {"connected": True})
        self.assertEqual(complete["anchor_reference"]["m30"], reference)
        self.assertEqual(complete["anchor_reference_id"], reference_id(reference))
        self.assertEqual(complete["period_counts"]["m30"], 3)

    def test_new_reference_keeps_original_native_window_until_mapping_exists(self):
        symbol = self.presets[0]["symbol"]
        warmup = CatalogueWarmup(lambda: [symbol], clock=lambda: self.now)
        api = FakeAPI(self, [])
        api.quotes[symbol].trading_time = {"day": DAY, "night": []}
        reader = app._WorkspaceSerialReader(api, self.cache, quotes=api.quotes, trading_minutes=True, warmup=warmup)
        reader.schedule_requested()
        api.loop.run_until_complete(asyncio.sleep(0))
        self.assertIn((symbol, 1800, 6000), api.calls)
        self.assertTrue(2100 <= next(n for s, p, n in api.calls if p == 900) <= 2304)

    def test_catalogue_addition_gets_quote_on_existing_sdk_without_new_login(self):
        codes = [self.presets[0]["symbol"]]
        warmup = CatalogueWarmup(lambda: codes, clock=lambda: self.now)
        api = FakeAPI(self, [])
        quotes = {codes[0]: api.quotes[codes[0]]}
        reader = app._WorkspaceSerialReader(api, self.cache, quotes=quotes, warmup=warmup)
        reader.schedule_requested()
        api.loop.run_until_complete(asyncio.sleep(0))
        reader.refresh(True)
        added = self.presets[1]["symbol"]
        codes[:] = [added]
        with patch.object(api, "get_quote", wraps=api.get_quote) as get:
            reader.schedule_requested()
            api.loop.run_until_complete(asyncio.sleep(0))
            get.assert_called_once_with(added)
        reader.refresh(True)
        self.assertEqual(warmup.status()["ready"], 1)
        self.assertIn(added, quotes)

    def test_local_archival_worker_starts_even_when_oss_is_unconfigured(self):
        fake_thread = types.SimpleNamespace(start=lambda: None, is_alive=lambda: True)
        with patch.object(app, "_data_thread", None), patch.object(app, "_workspace_archive_warmer", None), \
                patch.object(app, "_workspace_history_publisher", None), patch.object(app.config, "MOCK_MODE", False), \
                patch.object(app.threading, "Thread", return_value=fake_thread), \
                patch("workspace_history_oss.configure_history_publication", return_value=None) as publisher, \
                patch("workspace_warmup.ArchiveWarmer") as worker, patch.object(app.fundamentals, "start"):
            app.start_data_thread()
            publisher.assert_called_once()
            worker.assert_called_once()
            worker.return_value.start.assert_called_once()
            # A second caller cannot create a second producer or archive loop.
            app.start_data_thread()
            worker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
