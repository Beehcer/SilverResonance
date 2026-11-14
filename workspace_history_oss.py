"""Bounded, asynchronous publication of public candle blocks, never user plans.

Credentials live outside the application image in history-oss.json. Publication
failure leaves the authenticated same-origin history route fully operational.
"""
from collections import OrderedDict
import gzip
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import threading
import time
import urllib.request
from datetime import datetime

LOGGER = logging.getLogger("silver.history")
BUCKET = "silver-resonance"
ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
BASE = "https://silver-resonance.oss-cn-hangzhou.aliyuncs.com/"
PREFIX = "history/v1/blocks/"
DIGEST = re.compile(r"[a-f0-9]{64}")
CONTRACT = re.compile(r"(?:SHFE|DCE|CZCE|CFFEX|INE|GFEX)\.[A-Za-z]{1,4}[0-9]{3,4}")
PRICE_FIELDS = {"open", "high", "low", "close"}
TIME_FIELDS = {"dt", "end_time", "closed_at", "session_boundary", "source_start", "source_last_start"}
BOOL_FIELDS = {"closed", "complete", "truncated_start"}
COUNT_FIELDS = {"expected_slots", "observed_slots", "missing_slots", "pending_slots", "unobserved_prior_groups", "trading_minutes"}
ROW_FIELDS = (PRICE_FIELDS | TIME_FIELDS | BOOL_FIELDS | COUNT_FIELDS |
              {"volume", "oi", "source_trade_day", "quality", "candle_basis", "source_period_seconds", "missing_times", "calendar_quality"})


def _valid_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _validate_public_candles(value):
    """Export only price data, not arbitrary nested content inside a bars list."""
    if (not isinstance(value, dict)
            or set(value) != {"schema", "symbol", "period", "candle_basis", "anchor_reference_id", "bars"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["symbol"], str) or not CONTRACT.fullmatch(value["symbol"])
            or value["period"] not in ("day", "m30", "m3")
            or value["candle_basis"] not in ("native_v1", "trading_minutes_v1")
            or (value["anchor_reference_id"] is not None
                and (not isinstance(value["anchor_reference_id"], str) or not DIGEST.fullmatch(value["anchor_reference_id"])))
            or not isinstance(value["bars"], list) or not 1 <= len(value["bars"]) <= 10000):
        raise ValueError("Invalid history object")
    for row in value["bars"]:
        if not isinstance(row, dict) or not {"dt", *PRICE_FIELDS}.issubset(row) or set(row) - ROW_FIELDS:
            raise ValueError("Unexpected history candle fields")
        for key, item in row.items():
            if key in PRICE_FIELDS | {"volume", "oi"}:
                if type(item) not in (int, float) or not math.isfinite(item) or (key in PRICE_FIELDS and item <= 0) or (key == "volume" and item < 0):
                    raise ValueError("Invalid price field")
            elif key in TIME_FIELDS:
                if not _valid_timestamp(item):
                    raise ValueError("Invalid candle time")
            elif key in BOOL_FIELDS:
                if type(item) is not bool:
                    raise ValueError("Invalid candle state")
            elif key in COUNT_FIELDS:
                if type(item) is not int or item < 0:
                    raise ValueError("Invalid candle count")
            elif key == "source_period_seconds" and item not in (180, 900):
                raise ValueError("Invalid candle source period")
            elif key == "source_trade_day":
                if not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item) or not _valid_timestamp(item):
                    raise ValueError("Invalid trading day")
            elif key == "quality" and item not in ("incomplete", "complete", "live"):
                raise ValueError("Invalid candle quality")
            elif key == "calendar_quality" and item not in ("provided", "weekday_assumed"):
                raise ValueError("Invalid candle calendar")
            elif key == "candle_basis" and item != value["candle_basis"]:
                raise ValueError("Invalid candle basis")
            elif key == "missing_times" and (not isinstance(item, list) or len(item) > 10 or not all(_valid_timestamp(stamp) for stamp in item)):
                raise ValueError("Invalid missing candle times")
        if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise ValueError("Invalid candle price range")


class HistoryPublisher:
    def __init__(self, archive, bucket, clock=time.time, verify=None):
        self.archive, self.bucket, self.clock = archive, bucket, clock
        self.verify = verify or self._verify_public
        self.lock = threading.RLock()
        self.pending = OrderedDict()
        self.published = {}
        self.failed_until = {}
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.snapshot_provider = None
        self.path = archive.root / "oss-publications.json"
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self.published = {key: item for key, item in value.items()
                              if DIGEST.fullmatch(key) and isinstance(item, dict)
                              and isinstance(item.get("last"), str)
                              and isinstance(item.get("verified_at"), (int, float))}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    @staticmethod
    def _verify_public(digest):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(BASE + PREFIX + digest + ".json", headers={"Accept-Encoding": "gzip"})
        with opener.open(request, timeout=10) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("History object too large")
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("History object checksum mismatch")

    def public_url(self, digest):
        with self.lock:
            item = self.published.get(digest)
            return BASE + PREFIX + digest + ".json" if item else None

    def enqueue(self, descriptors):
        # Called from request threads: only enqueue metadata, never do network IO.
        with self.lock:
            for item in descriptors:
                if not isinstance(item, dict):
                    continue
                digest = item.get("sha256", "")
                if (not isinstance(digest, str) or not DIGEST.fullmatch(digest) or not _valid_timestamp(item.get("last"))
                        or not isinstance(item.get("path"), (str, Path)) or digest in self.published
                        or self.failed_until.get(digest, 0) > self.clock()):
                    continue
                path = Path(item["path"])
                expected = self.archive.root / "blocks" / (digest + ".json")
                if path != expected or path.is_symlink():
                    continue
                self.pending[digest] = {"path": path, "last": item["last"]}
                self.pending.move_to_end(digest)
                while len(self.pending) > 512:
                    self.pending.popitem(last=False)
        self.wake.set()

    def _save(self):
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.published, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def publish_one(self):
        with self.lock:
            if not self.pending:
                return False
            digest, item = self.pending.popitem(last=False)
        try:
            raw = self.archive.read_block(digest)
            value = json.loads(raw)
            _validate_public_candles(value)
            # A compromised descriptor cannot redirect credentials or select a key.
            key = PREFIX + digest + ".json"
            headers = {"Content-Type": "application/json; charset=utf-8", "Content-Encoding": "gzip",
                       "Cache-Control": "public,max-age=2592000,immutable", "x-oss-object-acl": "public-read",
                       "x-oss-meta-sha256": digest, "x-oss-forbid-overwrite": "true"}
            try:
                self.bucket.put_object(key, gzip.compress(raw, mtime=0), headers=headers)
            except Exception as error:
                if getattr(error, "code", "") != "FileAlreadyExists":
                    raise
            self.verify(digest)
            with self.lock:
                self.published[digest] = {"last": item["last"], "verified_at": self.clock()}
                self.failed_until.pop(digest, None)
                self._save()
        except Exception as error:
            with self.lock:
                self.failed_until[digest] = self.clock() + 60
                # Errors may contain authenticated request headers; log class only.
                if len(self.failed_until) > 512:
                    self.failed_until.pop(next(iter(self.failed_until)))
            LOGGER.warning("History publication deferred (%s); origin fallback retained", type(error).__name__)
        return True

    def collect_garbage(self):
        # Archive GC is authoritative for lifetime/obsolete versions. Only keys
        # successfully published by this worker may be removed from our prefix.
        self.archive.collect_garbage()
        live = self.archive.live_hashes()
        with self.lock:
            removed = [digest for digest, item in self.published.items()
                       if self.clock() - item["verified_at"] > 86400
                       and digest not in live
                       and not (self.archive.root / "blocks" / (digest + ".json")).is_file()]
        for digest in removed:
            # A bootstrap may have restored this exact immutable version while
            # collection was checking other contracts. Never delete that copy.
            if (self.archive.root / "blocks" / (digest + ".json")).is_file() or digest in self.archive.live_hashes():
                continue
            try:
                self.bucket.delete_object(PREFIX + digest + ".json")
            except Exception as error:
                LOGGER.warning("History expiry deferred (%s)", type(error).__name__)
                continue
            with self.lock:
                self.published.pop(digest, None)
                self._save()

    def start(self, snapshot_provider=None):
        self.snapshot_provider = snapshot_provider
        if self.thread:
            return
        def work():
            next_snapshot, next_gc = 0, self.clock() + 60
            while not self.stopped.is_set():
                if self.snapshot_provider and self.clock() >= next_snapshot:
                    next_snapshot = self.clock() + 60
                    try:
                        for market in self.snapshot_provider():
                            if self.stopped.is_set():
                                return
                            if market and not market.get("partial") and all(market.get("periods", {}).get(p) for p in ("day", "m30", "m3")):
                                self.archive.bootstrap(market)
                    except Exception as error:
                        LOGGER.warning("History snapshot deferred (%s)", type(error).__name__)
                if self.clock() >= next_gc:
                    next_gc = self.clock() + 3600
                    try:
                        self.collect_garbage()
                    except Exception as error:
                        LOGGER.warning("History cleanup deferred (%s)", type(error).__name__)
                if not self.publish_one():
                    self.wake.wait(2)
                    self.wake.clear()
        self.thread = threading.Thread(target=work, name="workspace-history-oss", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)


def configure_history_publication(archive, config_path, snapshot_provider=None):
    path = Path(config_path)
    if not path.is_file():
        return None
    try:
        import oss2
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("bucket") != BUCKET or config.get("endpoint") != ENDPOINT:
            raise ValueError("Unsupported history bucket")
        auth = oss2.Auth(config["access_key_id"], config["access_key_secret"])
        bucket = oss2.Bucket(auth, ENDPOINT, BUCKET, connect_timeout=8)
        publisher = HistoryPublisher(archive, bucket)
        archive.publication_hook = publisher.enqueue
        archive.public_url_provider = publisher.public_url
        publisher.start(snapshot_provider)
        return publisher
    except Exception as error:
        LOGGER.warning("History OSS unavailable (%s); origin cache enabled", type(error).__name__)
        return None
