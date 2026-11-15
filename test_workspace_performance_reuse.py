"""Content reuse must never reuse current permission or hide corrections."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from workspace_api import register_workspace
from workspace_history import WorkspaceHistoryArchive
from workspace_rules import _bars, _cached_bars
from workspace_store import WorkspaceStore


def sample():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = [{"dt": (start + timedelta(minutes=i * 3)).isoformat(), "open": 100,
             "high": 105, "low": 95, "close": 101, "closed": i < 5} for i in range(6)]
    return {"symbol": "TEST.a", "source": "live", "history_token": "same-token", "incremental": False,
            "as_of": "2026-11-15T00:00:00+00:00", "fresh": True,
            "periods": {p: copy.deepcopy(rows) for p in ("day", "m30", "m3")},
            "period_counts": {p: 6 for p in ("day", "m30", "m3")}}


class ReuseTests(unittest.TestCase):
    def test_history_reuses_prefix_but_keeps_tail_and_repairs_disk(self):
        with tempfile.TemporaryDirectory() as root:
            archive = WorkspaceHistoryArchive(root)
            market = sample()
            first = archive.bootstrap(market)
            market["periods"]["m3"][-1]["close"] = 102
            market["fresh"] = False
            second = archive.bootstrap(market)
            self.assertEqual(first["history"], second["history"])
            self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertFalse(second["market"]["fresh"])
            block = first["history"]["m3"][0]["sha256"]
            Path(root, "blocks", block + ".json").write_text("broken")
            archive.bootstrap(market)
            self.assertIn(b'"bars"', archive.read_block(block))
            market["periods"]["m3"][0]["close"] = 103
            third = archive.bootstrap(market)
            self.assertNotEqual(block, third["history"]["m3"][0]["sha256"])

    def test_validation_matches_uncached_for_corrections_tails_clock_and_bad_order(self):
        rows = sample()["periods"]["m3"]
        future = datetime(2026, 11, 15, tzinfo=timezone.utc)
        key = ("test", "live", "m3")
        for change in (lambda r: None, lambda r: r[-1].update(low=0),
                       lambda r: r[0].update(close=200), lambda r: r.reverse(),
                       lambda r: r[-2].update(dt=r[0]["dt"])):
            value = copy.deepcopy(rows)
            change(value)
            for when in (future, datetime(2026, 10, 1, tzinfo=timezone.utc), None):
                self.assertEqual(_cached_bars(value, when, key), _bars(value, when))
                self.assertEqual(_cached_bars(copy.deepcopy(value), when, key), _bars(value, when))

    def test_preview_content_revision_and_exact_reference_omission(self):
        with tempfile.TemporaryDirectory() as root:
            value = {**sample(), "partial": True, "fresh": False,
                     "anchor_reference_id": "fixed", "anchor_reference": {"m30": {"fixture": True}}}
            app = Flask(__name__)
            register_workspace(app, WorkspaceStore(Path(root) / "state.json"), lambda _: value,
                               lambda: [{"symbol": "TEST.a"}], lambda: {}, lambda *args: {},
                               market_preview_provider=lambda _: value)
            client = app.test_client()
            client.get("/api/workspace/session")
            url = "/api/workspace/market?symbol=TEST.a&preview=240"
            first = client.get(url).json["market"]
            revision = first["preview_revision"]
            value["as_of"] = "2026-11-15T01:00:00+00:00"
            same = client.get(url + "&preview_revision=" + revision)
            self.assertEqual(same.json["status"], "preview_unchanged")
            self.assertNotIn("market", same.json)
            self.assertIn("no-store", same.headers["Cache-Control"])
            reduced = client.get(url + "&anchor_reference=fixed").json["market"]
            self.assertNotIn("anchor_reference", reduced)
            self.assertIn("anchor_reference", client.get(url + "&anchor_reference=wrong").json["market"])
            value["periods"]["m3"][-1]["close"] = 102
            self.assertNotEqual(client.get(url + "&preview_revision=" + revision).json["market"]["preview_revision"], revision)


if __name__ == "__main__":
    unittest.main()
