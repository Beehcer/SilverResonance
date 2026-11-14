"""Content-addressed market history, separate from private plans and live evidence.

Only a full, internally consistent market snapshot may enter ``bootstrap``.
Historical blocks are immutable: a provider correction gets a different hash.
The final two candles remain in the response, including the recently closed one.
Publication hooks enqueue work; network I/O does not belong on this request path.
"""
import calendar
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone


TZ = timezone(timedelta(hours=8))
PERIODS = ("day", "m30", "m3")
HASH = re.compile(r"^[0-9a-f]{64}$")
SCHEMA = 1
MAX_BLOCK_BYTES = 8 * 1024 * 1024
GC_GRACE_SECONDS = 24 * 3600


def six_month_cutoff(now):
    """Subtract six calendar months, clamping month ends, preserving local time."""
    value = datetime.fromtimestamp(now, TZ)
    month = value.year * 12 + value.month - 1 - 6
    year, month = divmod(month, 12)
    return value.replace(year=year, month=month + 1,
                         day=min(value.day, calendar.monthrange(year, month + 1)[1])).timestamp()


def _stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(value).hexdigest()


class WorkspaceHistoryArchive:
    """Atomic local blocks plus a replaceable, per-contract latest manifest.

    ``publication_hook(items)`` receives sha256, path, relative_key and last.
    It must only enqueue work. ``public_url_provider(sha256)`` returns a URL only
    after a successful upload. Hook failures cannot make chart loading fail.
    """

    def __init__(self, root, *, clock=time.time, publication_hook=None,
                 public_url_provider=None):
        self.root = Path(root).resolve()
        self.clock = clock
        self.publication_hook = publication_hook
        self.public_url_provider = public_url_provider
        self.lock = threading.RLock()
        self._verified = {}
        self.last_publication_error = None

    def _atomic_write(self, path, content):
        if self.root not in path.parent.resolve().parents:
            raise ValueError("历史缓存目录无效")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Both temp and destination are inside the same fixed archive directory.
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _block_path(self, digest):
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError("历史块编号无效")
        return self.root / "blocks" / (digest + ".json")

    def _manifest_path(self, symbol, source):
        return self.root / "snapshots" / (_digest((symbol + "\0" + source).encode()) + ".json")

    def _write_block(self, digest, content):
        path = self._block_path(digest)
        if path.exists():
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            if self._verified.get(digest) == signature:
                return path
            if stat.st_size == len(content) and _digest(path.read_bytes()) == digest:
                self._verified[digest] = signature
                return path
        self._atomic_write(path, content)
        stat = path.stat()
        self._verified[digest] = (stat.st_size, stat.st_mtime_ns)
        return path

    def read_block(self, digest):
        """Return exact hashed UTF-8 bytes; never serve an unchecked local file."""
        path = self._block_path(digest)
        if path.is_symlink() or self.root not in path.resolve().parents:
            raise ValueError("历史块路径无效")
        if path.stat().st_size > MAX_BLOCK_BYTES:
            raise ValueError("历史块过大")
        content = path.read_bytes()
        if _digest(content) != digest:
            raise ValueError("历史块校验失败")
        return content

    def bootstrap(self, market):
        if (not isinstance(market, dict) or market.get("incremental") is not False
                or market.get("partial") or not market.get("history_token")
                or not isinstance(market.get("symbol"), str)):
            raise ValueError("历史初始化需要完整行情快照")
        symbol = market["symbol"]
        source = market.get("source", "live")
        periods = market.get("periods", {})
        if set(periods) != set(PERIODS):
            raise ValueError("历史行情周期不完整")
        history, tails, counts, publications = {}, {}, {}, []
        basis = market.get("candle_basis", {})
        anchor_id = market.get("anchor_reference_id")
        with self.lock:
            for period in PERIODS:
                rows = periods[period]
                if not isinstance(rows, list) or market.get("period_counts", {}).get(period) != len(rows):
                    raise ValueError("历史行情数量不一致")
                prior = None
                for row in rows:
                    stamp = _stamp(row["dt"])
                    if prior is not None and stamp <= prior:
                        raise ValueError("历史行情时间不连续")
                    prior = stamp
                prefix, tails[period] = rows[:-2], rows[-2:]
                counts[period] = len(prefix)
                groups = {}
                for row in prefix:
                    # Monthly minute blocks plus one small daily block keep cold
                    # bootstrap bounded to <= 15 requests within six months.
                    # Fixed calendar boundaries isolate historical corrections.
                    # This is a storage boundary, not an aggregation boundary:
                    # preserve source_trade_day on the row, including night bars.
                    day = _stamp(row["dt"]).date()
                    bucket = "all" if period == "day" else day.strftime("%Y-%m")
                    groups.setdefault(bucket, []).append(row)
                history[period] = []
                for group in groups.values():
                    value = {"schema": SCHEMA, "symbol": symbol, "period": period,
                             "candle_basis": basis.get(period, "native_v1"),
                             "anchor_reference_id": anchor_id, "bars": group}
                    content = _bytes(value)
                    if len(content) > MAX_BLOCK_BYTES:
                        raise ValueError("历史块过大")
                    digest = _digest(content)
                    path = self._write_block(digest, content)
                    item = {"sha256": digest,
                            "url": "/api/workspace/market/history/" + digest + ".json",
                            "count": len(group), "first": group[0]["dt"], "last": group[-1]["dt"]}
                    history[period].append(item)
                    publications.append({"sha256": digest, "path": str(path),
                                         "relative_key": "history/v1/blocks/" + digest + ".json",
                                         "last": item["last"]})
            # Exclude transport URLs and current read/connection/token metadata.
            # This identity is repeatable across process restarts for equal data.
            identity = {"schema": SCHEMA, "symbol": symbol, "source": source,
                        "basis": basis, "anchor_reference_id": anchor_id,
                        "history": {key: [row["sha256"] for row in history[key]] for key in PERIODS},
                        "tails": tails}
            result = {"schema": SCHEMA, "snapshot_id": _digest(_bytes(identity)),
                      "market": {**market, "periods": tails},
                      "history": history, "history_counts": counts}
            # Publish the manifest only after every referenced local block exists.
            # Partial cold starts must not overwrite the last complete snapshot.
            if all(periods[key] for key in PERIODS):
                persisted = {"saved_at": self.clock(), "bootstrap": result}
                manifest_path = self._manifest_path(symbol, source)
                if manifest_path.exists():
                    try:
                        old = json.loads(manifest_path.read_bytes())["bootstrap"]
                        old_hashes = {item["sha256"] for items in old["history"].values() for item in items}
                        if any(not isinstance(digest, str) or not HASH.fullmatch(digest) for digest in old_hashes):
                            raise ValueError("历史清单编号无效")
                    except (OSError, ValueError, TypeError, KeyError):
                        # A corrupt cache must heal from the new authoritative
                        # full snapshot. Conservatively extend every local old
                        # block's grace since the lost manifest cannot identify
                        # which in-flight readers may still be using it.
                        old_hashes = {path.stem for path in (self.root / "blocks").glob("*.json")
                                      if HASH.fullmatch(path.stem) and not path.is_symlink()}
                    new_hashes = {item["sha256"] for items in history.values() for item in items}
                    # Start the grace period when a block loses its reference,
                    # not when it was originally written weeks ago.
                    retired_at = self.clock()
                    for digest in old_hashes - new_hashes:
                        path = self._block_path(digest)
                        if path.exists():
                            os.utime(path, (retired_at, retired_at))
                self._atomic_write(manifest_path, _bytes(persisted))
        if self.publication_hook and publications:
            try:
                self.publication_hook(publications)
                self.last_publication_error = None
            except Exception as exc:
                self.last_publication_error = type(exc).__name__
        if self.public_url_provider:
            for items in history.values():
                for item in items:
                    try:
                        url = self.public_url_provider(item["sha256"])
                        if isinstance(url, str) and url.startswith("https://"):
                            item["oss_url"] = url
                    except Exception:
                        pass
        return result

    def preview(self, symbol, *, source="live", expected_reference_id=None):
        """Read a persisted picture, never restore a cursor or live qualification."""
        try:
            with self.lock:
                persisted = json.loads(self._manifest_path(symbol, source).read_bytes())
                saved = persisted["bootstrap"]
                market = saved["market"]
                if (saved["schema"] != SCHEMA or market["symbol"] != symbol
                        or market.get("source") != source
                        or (expected_reference_id is not None
                            and market.get("anchor_reference_id") != expected_reference_id)):
                    return None
                cutoff = six_month_cutoff(self.clock())
                periods, counts = {}, {}
                for period in PERIODS:
                    rows = []
                    for item in saved["history"][period]:
                        if _stamp(item["last"]).timestamp() < cutoff:
                            continue
                        value = json.loads(self.read_block(item["sha256"]))
                        if (value["symbol"] != symbol or value["period"] != period
                                or value["anchor_reference_id"] != market.get("anchor_reference_id")
                                or value["candle_basis"] != market.get("candle_basis", {}).get(period, "native_v1")):
                            return None
                        rows.extend(value["bars"])
                    rows.extend(market["periods"][period])
                    rows = [row for row in rows if _stamp(row["dt"]).timestamp() >= cutoff]
                    counts[period], periods[period] = len(rows), rows[-240:]
                if not all(periods.values()):
                    return None
                return {**market, "periods": periods, "period_counts": counts,
                        "fresh": False, "connected": False, "partial": True,
                        "freshness": {key: {**item, "checked_recent": False, "changed_recent": False}
                                      for key, item in market.get("freshness", {}).items()},
                        "persisted_cached": True, "preview_saved_at": persisted["saved_at"] * 1000,
                        "history_token": None, "incremental": False, "feed_status": "preview",
                        "message": "已缓存历史 · 正在连接实时行情"}
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def live_hashes(self):
        """Current manifest references inside retention, including idle contracts.

        Corrupt manifests raise instead of returning an unsafe empty live set.
        An external OSS collector must also stop collection on that failure.
        """
        cutoff, result = six_month_cutoff(self.clock()), set()
        with self.lock:
            folder = self.root / "snapshots"
            if not folder.exists():
                return result
            if self.root not in folder.resolve().parents:
                raise ValueError("历史清单目录无效")
            for path in folder.glob("*.json"):
                if path.is_symlink() or not HASH.fullmatch(path.stem):
                    continue
                value = json.loads(path.read_bytes())["bootstrap"]
                if value["schema"] != SCHEMA or set(value["history"]) != set(PERIODS):
                    raise ValueError("历史清单无效，暂停回收")
                for items in value["history"].values():
                    for item in items:
                        if not HASH.fullmatch(item["sha256"]):
                            raise ValueError("历史块编号无效，暂停回收")
                        if _stamp(item["last"]).timestamp() >= cutoff:
                            result.add(item["sha256"])
        return result

    def collect_garbage(self, *, grace_seconds=GC_GRACE_SECONDS):
        """Collect expired/retired blocks after a grace, never private plan data."""
        now, removed = self.clock(), 0
        cutoff = six_month_cutoff(now)
        with self.lock:
            live = self.live_hashes()
            folder = self.root / "blocks"
            if not folder.exists():
                return removed
            if self.root not in folder.resolve().parents:
                raise ValueError("历史块目录无效")
            for path in folder.iterdir():
                if (path.is_symlink() or not path.is_file() or not HASH.fullmatch(path.stem)
                        or path.suffix != ".json" or path.resolve().parent != folder.resolve()
                        or now - path.stat().st_mtime < max(0, grace_seconds)):
                    continue
                try:
                    value = json.loads(self.read_block(path.stem))
                    rows = value["bars"]
                    expired = rows and all(_stamp(row["dt"]).timestamp() < cutoff for row in rows)
                    if expired or path.stem not in live:
                        path.unlink()
                        self._verified.pop(path.stem, None)
                        removed += 1
                except (OSError, ValueError, TypeError, KeyError):
                    continue
        return removed
