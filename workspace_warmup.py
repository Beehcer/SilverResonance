"""Bounded catalogue warmup in the existing SDK session, plus disk-only archival."""
import threading
import time
import math
from datetime import datetime

from workspace_history import TZ, six_month_cutoff


def source_history_length(trading_time, now, *, period=900):
    """A half-year storage budget; this does not decide whether trading is open."""
    from workspace_candles import _schedule
    schedule = _schedule(trading_time, period)
    daily = sum((end - start) // period for pairs in schedule.values() for start, end in pairs)
    span = (datetime.fromtimestamp(now, TZ).date() - datetime.fromtimestamp(six_month_cutoff(now), TZ).date()).days + 1
    # A conservative weekday upper bound, plus four sessions and boundary rows.
    # Holidays can only reduce the amount required; no missing bar is invented.
    days = math.ceil(span / 7) * 5 + 4
    return min(10000, max(512, math.ceil((daily * days + 64) / 128) * 128))


class CatalogueWarmup:
    """Choose one background contract at a time; viewers take precedence.

    This class never calls the SDK. The owner calls select/observe on its SDK
    thread. Existing serials continue receiving provider updates even after a
    slow initial history has released its background scheduling slot.
    """
    def __init__(self, catalogue, *, clock=time.time, timeout=30, max_symbols=64):
        self.catalogue, self.clock = catalogue, clock
        self.timeout, self.max_symbols = timeout, max_symbols
        self.lock = threading.RLock()
        self.records, self.background, self.cursor = {}, None, 0
        self.catalogue_overflow = 0

    def _sync(self):
        symbols = list(dict.fromkeys(self.catalogue()))
        self.catalogue_overflow = max(0, len(symbols) - self.max_symbols)
        symbols = symbols[:self.max_symbols]
        self.records = {symbol: self.records.get(symbol, {
            "symbol": symbol, "ready": False, "started_at": None, "ready_at": None,
            "next_retry_at": 0, "attempts": 0, "error": None, "periods": {}}) for symbol in symbols}
        if self.background not in self.records:
            self.background = None
        return symbols

    def select(self, priorities, missing):
        with self.lock:
            symbols, now = self._sync(), self.clock()
            priorities = [symbol for symbol in priorities if symbol in self.records]
            chosen = []
            for symbol in priorities:
                record = self.records[symbol]
                if missing(symbol) and now >= record["next_retry_at"]:
                    record["started_at"] = now
                    chosen.append(symbol)
                    if len(chosen) == 2:
                        break
            if chosen:
                return chosen
            # Let a foreground first load finish before adding another bulk
            # history request. Quotes and already-ready serials keep running.
            if any(not self.records[s]["ready"] and self.records[s]["started_at"] is not None
                   and now - self.records[s]["started_at"] < self.timeout for s in priorities):
                return []
            if self.background:
                record = self.records[self.background]
                if record["ready"] or record["next_retry_at"] > now:
                    self.background = None
                elif now - record["started_at"] < self.timeout:
                    return []
                else:
                    record["error"] = record["error"] or "历史初始化超时，其他品种继续预热"
                    self.background = None
            if not symbols:
                return []
            for offset in range(len(symbols)):
                index = (self.cursor + offset) % len(symbols)
                symbol, record = symbols[index], self.records[symbols[index]]
                if record["ready"] or now < record["next_retry_at"] or not missing(symbol):
                    continue
                record["started_at"] = now
                self.background, self.cursor = symbol, (index + 1) % len(symbols)
                return [symbol]
            return []

    def subscription_failed(self, symbol, error):
        with self.lock:
            self._sync()
            record = self.records.get(symbol)
            if not record:
                return
            record["attempts"] += 1
            record["error"] = "订阅重试: " + type(error).__name__
            record["next_retry_at"] = self.clock() + min(60, 2 ** min(record["attempts"], 6))

    def observe(self, symbol, periods, *, error=None):
        with self.lock:
            record = self.records.get(symbol)
            if not record:
                return False
            was_ready = record["ready"]
            record["periods"] = periods
            ready = set(periods) == {"day", "m30", "m3"} and all(item["count"] and not item.get("error") for item in periods.values())
            record["ready"] = ready and not error
            if record["ready"]:
                record["ready_at"] = record["ready_at"] or self.clock()
                record["error"], record["next_retry_at"], record["attempts"] = None, 0, 0
            elif error:
                record["error"] = error
            return record["ready"] and not was_ready

    def status(self):
        with self.lock:
            self._sync()
            values = []
            for record in self.records.values():
                status = "ready" if record["ready"] else "retrying" if record["error"] else "loading" if record["started_at"] is not None else "queued"
                values.append({**record, "status": status,
                               "periods": {key: dict(value) for key, value in record["periods"].items()}})
            return {"symbols": values, "ready": sum(item["ready"] for item in values), "total": len(values),
                    "active_background": self.background, "catalogue_overflow": self.catalogue_overflow,
                    "background_concurrency": 1, "max_symbols": self.max_symbols}

    def symbols(self):
        with self.lock:
            return self._sync()


class ArchiveWarmer:
    """One bounded I/O worker; snapshots are taken one contract at a time.

    No provider API, subscriptions, users, or live freshness are created here.
    The archive's optional publication hook handles OSS independently.
    """
    def __init__(self, catalogue, snapshot_provider, archive, *, priorities=lambda: (),
                 clock=time.time, interval=15 * 60, max_symbols=64):
        self.catalogue, self.snapshot_provider, self.archive = catalogue, snapshot_provider, archive
        self.priorities, self.clock, self.interval, self.max_symbols = priorities, clock, interval, max_symbols
        self.lock = threading.RLock()
        self.records, self.thread, self.cursor, self.last_error = {}, None, 0, None
        self.urgent = []
        self.stopped, self.wake = threading.Event(), threading.Event()

    def _symbols(self):
        symbols = list(dict.fromkeys(self.catalogue()))[:self.max_symbols]
        priorities = [symbol for symbol in self.priorities() if symbol in symbols]
        return list(dict.fromkeys(priorities + symbols))

    def step(self):
        symbols, now = self._symbols(), self.clock()
        with self.lock:
            self.records = {symbol: self.records.get(symbol, {"symbol": symbol, "status": "waiting_source",
                            "saved_at": None, "checked_at": None, "next_retry_at": 0, "attempts": 0, "error": None,
                            "counts": {}}) for symbol in symbols}
            order = symbols[self.cursor:] + symbols[:self.cursor]
            self.urgent = [symbol for symbol in self.urgent if symbol in self.records]
            order = list(dict.fromkeys(self.urgent + order))
            chosen = next((symbol for symbol in order if now >= self.records[symbol]["next_retry_at"]), None)
            if chosen is None:
                return False
            self.cursor = (symbols.index(chosen) + 1) % len(symbols)
            self.urgent = [symbol for symbol in self.urgent if symbol != chosen]
            record = self.records[chosen]
        try:
            market = self.snapshot_provider(chosen) or {}
            complete = market and not market.get("partial") and all(market.get("periods", {}).get(p) for p in ("day", "m30", "m3"))
            if not complete:
                # A restart can already have durable charts while the SDK is
                # still warming. Read a small preview once for honest status.
                if record["saved_at"] is None:
                    cached = self.archive.metadata(chosen, source=market.get("source", "live"),
                                                   expected_reference_id=market.get("anchor_reference_id"))
                    if cached:
                        with self.lock:
                            record["saved_at"], record["counts"] = cached["saved_at"], cached["period_counts"]
                with self.lock:
                    record.update(status="cached" if record["saved_at"] is not None else "waiting_source",
                                  next_retry_at=now + 5)
                return True
            result = self.archive.bootstrap(market)
            metadata = self.archive.metadata(chosen, source=market.get("source", "live"),
                                             expected_reference_id=market.get("anchor_reference_id"))
            with self.lock:
                record.update(status="archived", saved_at=metadata["saved_at"] if metadata else None,
                              checked_at=now, next_retry_at=now + self.interval,
                              attempts=0, error=None, counts=dict(market["period_counts"]),
                              snapshot_id=result["snapshot_id"])
        except Exception as error:
            with self.lock:
                record["attempts"] += 1
                record.update(status="retrying", error=type(error).__name__,
                              next_retry_at=now + min(300, 5 * 2 ** min(record["attempts"] - 1, 6)))
        return True

    def notify_ready(self, symbol):
        with self.lock:
            if symbol in self.records:
                self.records[symbol]["next_retry_at"] = 0
            if symbol not in self.urgent and len(self.urgent) < self.max_symbols:
                self.urgent.append(symbol)
        self.wake.set()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stopped.clear()
            def work():
                while not self.stopped.is_set():
                    try:
                        self.step()
                        self.last_error = None
                    except Exception as error:
                        self.last_error = type(error).__name__
                    self.wake.wait(.25)
                    self.wake.clear()
            self.thread = threading.Thread(target=work, name="workspace-archive-warmer", daemon=True)
            self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
        return not self.thread or not self.thread.is_alive()

    def status(self):
        with self.lock:
            return {"running": bool(self.thread and self.thread.is_alive()), "interval_seconds": self.interval,
                    "error": self.last_error,
                    "symbols": [{**value, "counts": dict(value["counts"])} for value in self.records.values()]}
