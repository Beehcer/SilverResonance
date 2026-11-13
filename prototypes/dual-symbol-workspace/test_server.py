import importlib.util
import json
from datetime import datetime
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


spec = importlib.util.spec_from_file_location("dual_symbol_preview_server", Path(__file__).with_name("server.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = preview.make_server(0)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)

    def test_catalogue_contains_lithium_without_reading_account_settings(self):
        with patch.object(preview.symbols.user_settings, "load_settings", side_effect=AssertionError("credentials forbidden")):
            result = preview.catalogue()
        self.assertEqual(len(result), len(preview.symbols.PRESETS))
        self.assertTrue(any(row["symbol"].startswith("GFEX.lc") for row in result))
        self.assertTrue(all(set(row) == {"symbol", "name", "tick", "tick_value"} for row in result))

    def test_simulation_is_timestamped_coherent_and_respects_price_ticks(self):
        now = datetime(2026, 11, 13, 10, 23, tzinfo=preview.BEIJING)
        for product in ("CZCE.MA", "GFEX.lc", "SHFE.au"):
            item = next(row for row in preview.catalogue() if row["symbol"].startswith(product))
            result = preview.mock_market(item, now)
            self.assertEqual(result["source"], "mock")
            self.assertTrue(result["mock"])
            self.assertFalse(result["fresh"])
            self.assertTrue(result["generated_at"].endswith("+08:00"))
            for bars in result["periods"].values():
                self.assertGreater(len(bars), 65)
                self.assertEqual(bars[-1]["close"], result["price"])
                times = [row["time"] for row in bars]
                self.assertEqual(times, sorted(set(times)))
                self.assertLessEqual(times[-1], now.timestamp() * 1000)
                for row in bars:
                    self.assertLessEqual(row["low"], min(row["open"], row["close"]))
                    self.assertGreaterEqual(row["high"], max(row["open"], row["close"]))
                    self.assertGreater(row["low"], 0)
                    self.assertGreaterEqual(row["volume"], 0)
                    for key in ("open", "high", "low", "close"):
                        self.assertAlmostEqual(row[key] / item["tick"], round(row[key] / item["tick"]), places=6)

    def test_routes_are_read_only_and_only_expose_whitelisted_assets(self):
        with urlopen(self.url + "/api/symbols") as response:
            payload = json.load(response)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        symbol = next(row["symbol"] for row in payload["symbols"] if row["symbol"].startswith("GFEX.lc"))
        with urlopen(self.url + "/api/market?symbol=" + symbol) as response:
            self.assertEqual(json.load(response)["market"]["symbol"], symbol)
        with urlopen(self.url + "/assets/workspace-chart.js") as response:
            self.assertIn(b"WorkspaceChart", response.read())
        for path in ("/server.py", "/README.md", "/test_server.py", "/assets/../user_config.json",
                     "/%2e%2e/state/contracts.json", "/assets/workspace.js",
                     "/api/market?symbol=UNKNOWN"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.url + path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + "/api/market", data=b"{}", method="POST"))
        self.assertEqual(error.exception.code, 405)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/api/market")
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
