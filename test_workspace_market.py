import unittest
from workspace_market import WorkspaceMarketCache, data_length_for_period, iso, timestamp, trading_day


class WorkspaceMarketTests(unittest.TestCase):
    def setUp(self):
        self.now = timestamp("2026-11-09T10:35:00+08:00")
        self.cache = WorkspaceMarketCache(lambda: self.now)
        self.preset = {"tick": 1, "tick_value": 10, "name": "测试"}

    def bars(self, period, price=100):
        start = int(self.now // period) * period
        return [{"dt": iso(start - (3-i)*period), "open": price, "high": price+2,
                 "low": price-2, "close": price+1} for i in range(4)]

    def populate(self, symbol, price=100):
        for period in (86400, 1800, 180):
            self.cache.cache(symbol, period, self.bars(period, price))

    def test_independent_symbols_and_closed_event_time(self):
        self.populate("ONE", 100)
        self.populate("TWO", 200)
        one = self.cache.snapshot("ONE", self.preset, {"connected": True})
        two = self.cache.snapshot("TWO", self.preset, {"connected": True})
        self.assertEqual((one["price"], two["price"]), (101, 201))
        self.assertTrue(one["fresh"])
        self.assertEqual(one["feed_status"], "live")
        bars = one["periods"]["m30"]
        self.assertEqual(timestamp(bars[-2]["closed_at"]), timestamp(bars[-2]["dt"])+1800)
        self.assertFalse(bars[-1]["closed"])
        self.assertNotIn("closed_at", bars[-1])

    def test_refresh_without_new_data_does_not_refresh_quote(self):
        self.populate("ONE")
        fixed = {p: self.bars(p) for p in (86400, 1800, 180)}
        self.cache.request("ONE")
        self.now += 25
        self.cache.refresh(lambda s, p: fixed[p])
        value = self.cache.snapshot("ONE", self.preset, {"connected": True})
        self.assertFalse(value["fresh"])
        self.assertTrue(value["freshness"]["m30"]["checked_recent"])
        self.assertEqual(value["feed_status"], "idle")
        self.assertIn("编写计划", value["message"])
        self.assertEqual(value["quote_at"], iso(self.now - 25))

    def test_failed_read_is_not_reported_as_idle(self):
        self.populate("ONE")
        self.cache.request("ONE")
        self.now += 25
        def failing_read(symbol, period):
            raise ConnectionError("unavailable")
        self.cache.refresh(failing_read)
        value = self.cache.snapshot("ONE", self.preset, {"connected": True})
        self.assertEqual(value["feed_status"], "stale")
        self.assertFalse(value["fresh"])
        self.assertTrue(value["periods"]["m3"])

    def test_missing_daily_and_disconnection_block_ready(self):
        self.cache.cache("ONE", 180, self.bars(180))
        missing = self.cache.snapshot("ONE", self.preset, {"connected": True})
        self.assertFalse(missing["fresh"])
        self.assertEqual(missing["feed_status"], "loading")
        self.populate("ONE")
        disconnected = self.cache.snapshot("ONE", self.preset, {"connected": False})
        self.assertFalse(disconnected["fresh"])
        self.assertEqual(disconnected["feed_status"], "disconnected")

    def test_invalid_prices_and_viewed_requests_stay_warm(self):
        self.cache.request("ONE")
        self.now += 181
        self.assertEqual(self.cache.requested(), ["ONE"])
        broken = self.bars(180)
        broken[-1]["low"] = 1000
        with self.assertRaises(ValueError):
            self.cache.cache("ONE", 180, broken)

    def test_recent_reads_of_old_main_period_cannot_enable_current_trigger(self):
        self.populate("ONE")
        old = self.bars(1800)
        for row in old:
            row["dt"] = iso(timestamp(row["dt"]) - 10 * 86400)
        self.cache.cache("ONE", 1800, old)
        result = self.cache.snapshot("ONE", self.preset, {"connected": True})
        self.assertTrue(result["freshness"]["m30"]["checked_recent"])
        self.assertFalse(result["freshness"]["m30"]["covers_trigger"])
        self.assertFalse(result["fresh"])
        self.assertEqual(result["feed_status"], "aligning")

    def test_history_correction_does_not_refresh_current_quote(self):
        self.populate("ONE")
        rows = self.bars(180)
        self.now += 25
        rows[0]["high"] += 5
        self.cache.cache("ONE", 180, rows)
        result = self.cache.snapshot("ONE", self.preset, {"connected": True})
        self.assertFalse(result["freshness"]["m3"]["changed_recent"])

    def test_old_daily_label_cannot_pass_with_live_intraday_bars(self):
        self.populate("ONE")
        rows = self.bars(86400)
        for row in rows:
            row["dt"] = iso(timestamp(row["dt"]) - 10 * 86400)
        self.cache.cache("ONE", 86400, rows)
        result = self.cache.snapshot("ONE", self.preset, {"connected": True})
        self.assertFalse(result["freshness"]["day"]["matches_trading_day"])
        self.assertFalse(result["fresh"])

    def test_friday_night_future_monday_label_does_not_make_completed_daily_future(self):
        for clock in ("2026-11-06T21:35:00+08:00", "2026-11-07T01:35:00+08:00", "2026-11-09T21:35:00+08:00"):
            self.now = timestamp(clock)
            self.populate("ONE")
            day = trading_day(self.now).isoformat()
            previous = "2026-11-06" if day == "2026-11-09" else "2026-11-09"
            rows = [{"dt": date+"T00:00:00+08:00", "open": 100, "high": 103, "low": 99, "close": 101}
                    for date in (previous, day)]
            self.cache.cache("ONE", 86400, rows)
            result = self.cache.snapshot("ONE", self.preset, {"connected": True})
            self.assertTrue(result["fresh"], clock)
            self.assertLessEqual(timestamp(result["periods"]["day"][0]["closed_at"]), self.now)
            self.assertFalse(result["periods"]["day"][-1]["closed"])

    def test_retention_is_180_daily_and_expanded_minute_samples(self):
        self.assertEqual(data_length_for_period(86400), 180)
        self.assertEqual(data_length_for_period(1800), 6000)
        self.assertEqual(data_length_for_period(180), 10000)
        daily = [{"dt": iso(self.now - (250-i)*86400), "open": 100, "high": 102,
                  "low": 98, "close": 101} for i in range(250)]
        self.cache.cache("ONE", 86400, daily)
        result = self.cache.snapshot("ONE", self.preset, {"connected": False})
        self.assertEqual(len(result["periods"]["day"]), 180)
        hourly = [{"dt": iso(self.now - (45*24-i)*3600), "open": 100, "high": 102,
                   "low": 98, "close": 101} for i in range(45*24)]
        self.cache.cache("ONE", 180, hourly)
        result = self.cache.snapshot("ONE", self.preset, {"connected": False})
        minutes = result["periods"]["m3"]
        self.assertEqual(len(minutes), len(hourly))
        self.assertEqual(minutes[0]["dt"], hourly[0]["dt"])
        oversized = [{"dt": iso(self.now - (10050-i)*180), "open": 100, "high": 102,
                      "low": 98, "close": 101} for i in range(10050)]
        self.cache.cache("ONE", 180, oversized)
        result = self.cache.snapshot("ONE", self.preset, {"connected": False})
        self.assertEqual(len(result["periods"]["m3"]), 10000)


if __name__ == "__main__":
    unittest.main()
