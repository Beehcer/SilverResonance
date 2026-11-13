"""Isolated, loopback-only UI prototype. No live feeds, accounts, or state writes."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit


DIRECTORY = Path(__file__).resolve().parent
PROJECT = DIRECTORY.parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

# This module only reads the public contract catalogue and its existing cache.
# It does not call load_settings(), refresh_from_api(), or any saving function.
import symbols  # noqa: E402


BEIJING = timezone(timedelta(hours=8))
DEFAULT_PORT = 8864
PERIODS = {"day": 86400, "m30": 1800, "m3": 180}
NOTICE = "本地样板 · 模拟行情；价格与交易时段均为演示，不连接实盘。"
PUBLIC_FILES = {
    "/": (DIRECTORY / "index.html", "text/html; charset=utf-8"),
    "/index.html": (DIRECTORY / "index.html", "text/html; charset=utf-8"),
    "/styles.css": (DIRECTORY / "styles.css", "text/css; charset=utf-8"),
    "/app.js": (DIRECTORY / "app.js", "text/javascript; charset=utf-8"),
    "/model.js": (DIRECTORY / "model.js", "text/javascript; charset=utf-8"),
    **{
        "/assets/" + name: (PROJECT / "static" / name, "text/javascript; charset=utf-8")
        for name in (
            "workspace-chart.js", "workspace-symbols.js",
            "workspace-shortcuts.js", "plan-board-model.js",
        )
    },
}
# Deliberately illustrative levels. They are never presented as live quotes.
BASES = {
    "ag": 7300, "au": 600, "rb": 3400, "sa": 1500, "fg": 1300,
    "bu": 3600, "cf": 14500, "sp": 5600, "ss": 13200, "m": 2900,
    "jm": 1400, "ma": 2600, "rm": 2400, "jd": 3200, "ap": 7400,
    "ta": 5000, "l": 7600, "p": 8000, "pp": 7200, "lc": 80000,
}


def catalogue() -> list[dict]:
    return [
        {key: item[key] for key in ("symbol", "name", "tick", "tick_value")}
        for item in symbols.PRESETS
    ]


def _product(symbol: str) -> str:
    return "".join(ch for ch in symbol.split(".")[-1] if ch.isalpha()).lower()


def _snap(value: float, tick: float) -> float:
    return round(max(tick, round(value / tick) * tick), 8)


def _demo_times(now: datetime, days: int = 100) -> list[datetime]:
    """Simplified weekday/day-session demo, not an exchange trading calendar."""
    dates = []
    current = now.date()
    while len(dates) < days:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    times = []
    for day in reversed(dates):
        for start, end in ((time(9), time(11, 30)), (time(13, 30), time(15))):
            cursor = datetime.combine(day, start, BEIJING)
            stop = datetime.combine(day, end, BEIJING)
            while cursor < stop and cursor <= now:
                times.append(cursor)
                cursor += timedelta(minutes=3)
    return times


def _series(item: dict, now: datetime) -> list[dict]:
    base = BASES.get(_product(item["symbol"]), 5000)
    tick = float(item["tick"])
    phase = int(hashlib.sha256(item["symbol"].encode()).hexdigest()[:8], 16) % 629 / 100
    rows = []
    for index, stamp in enumerate(_demo_times(now)):
        # Timestamp-derived terms keep existing bars stable when the date changes.
        slot = int(stamp.timestamp() // 180)
        slow = math.sin(slot / 370 + phase) * .024
        medium = math.sin(slot / 47 + phase) * .009
        short = math.sin(slot / 3.7 + phase) * .0018
        close = _snap(base * (1 + slow + medium + short), tick)
        open_price = rows[-1]["close"] if rows else _snap(close - base * .001, tick)
        spread = max(tick, base * (.0007 + abs(math.sin(slot * 1.3 + phase)) * .001))
        high = _snap(max(open_price, close) + spread, tick)
        low = _snap(min(open_price, close) - spread * .8, tick)
        rows.append({
            "dt": stamp.isoformat(), "time": int(stamp.timestamp() * 1000),
            "open": open_price, "high": high, "low": low, "close": close,
            "volume": int(300 + abs(math.sin(slot * .7 + phase)) * 1800),
            "oi": int(180000 + math.sin(slot / 81 + phase) * 12000),
            "source_trade_day": stamp.date().isoformat(),
        })
    return rows


def _aggregate(rows: list[dict], period: str) -> list[dict]:
    buckets = OrderedDict()
    for row in rows:
        stamp = datetime.fromisoformat(row["dt"])
        if period == "day":
            start = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = stamp.replace(minute=stamp.minute // 30 * 30, second=0, microsecond=0)
        key = start.isoformat()
        if key not in buckets:
            buckets[key] = {**row, "dt": key, "time": int(start.timestamp() * 1000)}
        else:
            bar = buckets[key]
            bar.update(high=max(bar["high"], row["high"]), low=min(bar["low"], row["low"]),
                       close=row["close"], volume=bar["volume"] + row["volume"], oi=row["oi"])
    return list(buckets.values())


@lru_cache(maxsize=24)
def _market_bars(symbol: str, tick: float, bucket: int) -> dict:
    now = datetime.fromtimestamp(bucket * 180, BEIJING)
    rows = _series({"symbol": symbol, "tick": tick}, now)
    return {"day": _aggregate(rows, "day")[-100:],
            "m30": _aggregate(rows, "m30")[-360:], "m3": rows[-480:]}


def mock_market(item: dict, now: datetime | None = None) -> dict:
    now = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    stamp = now.timestamp()
    periods = _market_bars(item["symbol"], float(item["tick"]), int(stamp // 180))
    return {
        **item, "price": periods["m3"][-1]["close"], "source": "mock", "mock": True,
        "fresh": False, "feed_status": "mock", "unavailable": False, "partial": False,
        "notice": NOTICE, "generated_at": now.isoformat(), "updated_at": stamp,
        "history_token": "prototype-v1:" + item["symbol"], "periods": periods,
        "freshness": {period: {"checked_at": stamp, "last_bar_at": bars[-1]["dt"]}
                      for period, bars in periods.items()},
    }


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "DualSymbolPreview/1"

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head=True)

    def do_POST(self):
        self._json({"error": "样板服务只提供只读接口"}, 405)

    do_PUT = do_PATCH = do_DELETE = do_POST

    def _send(self, payload: bytes, content_type: str, status: int = 200, head: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(payload)

    def _json(self, data: dict, status: int = 200, head: bool = False):
        self._send(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8", status, head)

    def _serve(self, head: bool = False):
        url = urlsplit(self.path)
        path = unquote(url.path)
        if path == "/api/symbols":
            return self._json({"symbols": catalogue(), "source": "mock", "notice": NOTICE}, head=head)
        if path == "/api/market":
            requested = parse_qs(url.query).get("symbol", [])
            if len(requested) != 1:
                return self._json({"error": "请选择一个可用品种"}, 400, head)
            item = next((item for item in catalogue() if item["symbol"] == requested[0]), None)
            if item is None:
                return self._json({"error": "该品种不在样板目录中"}, 404, head)
            return self._json({"market": mock_market(item)}, head=head)
        resource = PUBLIC_FILES.get(path)
        if resource is None:
            return self._json({"error": "未找到样板资源"}, 404, head)
        file, kind = resource
        try:
            content = file.read_bytes()
        except OSError:
            return self._json({"error": "样板资源尚未准备好"}, 404, head)
        self._send(content, kind, head=head)

    def log_message(self, format: str, *args):
        pass


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), PreviewHandler)


def main():
    parser = argparse.ArgumentParser(description="本地双品种交互样板（仅模拟行情）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"本地样板 http://127.0.0.1:{server.server_port}/", flush=True)
    print(NOTICE, flush=True)
    print("仅当前电脑可访问；Ctrl+C 停止，不影响原工作台。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
