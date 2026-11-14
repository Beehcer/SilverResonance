"""Per-contract workspace chart subscriptions; independent of the legacy focus tab."""
import hashlib
import json
import math
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from workspace_history import six_month_cutoff

TZ = timezone(timedelta(hours=8))
PERIODS = {"day": 86400, "m30": 1800, "m3": 180}
RETENTION_BARS = {86400: 180, 1800: 6000, 900: 10000, 180: 10000}
MAX_APPEND_BARS = 64
CURSORS_PER_SYMBOL = 128


def validate_candle_reference(value):
    if (not isinstance(value, dict) or set(value) != {"basis", "target_basis", "source_times", "target_base_time"}
            or value["basis"] != "native_v1" or value["target_basis"] != "trading_minutes_v1"):
        raise ValueError("K线坐标转换基准无效")
    times = value["source_times"]
    if (not isinstance(times, list) or not 1 <= len(times) <= 6000
            or any(type(t) not in (int, float) or not math.isfinite(t) or t % 1 or not 0 <= t <= 253402300799000 for t in times)
            or any(a >= b for a, b in zip(times, times[1:]))):
        raise ValueError("K线坐标来源时间轴无效")
    base = value["target_base_time"]
    if type(base) not in (int, float) or not math.isfinite(base) or base % 1 or not 0 <= base <= 253402300799000:
        raise ValueError("K线转换目标基准无效")


def reference_id(reference):
    return hashlib.sha256(json.dumps(reference, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def data_length_for_period(period):
    return RETENTION_BARS.get(period, 120)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def iso(value):
    return datetime.fromtimestamp(value, TZ).isoformat(timespec="seconds")


def trading_day(value):
    """TQ trading-date boundary: 18:00, with weekend dates rolled to Monday.

    This is a data-label convention, not a claim that the exchange opens at 18:00.
    See tqsdk/datetime.py _get_trading_day_from_timestamp.
    """
    local = datetime.fromtimestamp(value, TZ)
    label = (local + timedelta(days=1) if local.hour >= 18 else local).date()
    if label.weekday() >= 5:
        label += timedelta(days=7 - label.weekday())
    return label


def daily_boundary(value):
    label = datetime.fromtimestamp(value, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = label - timedelta(hours=6)
    if start.weekday() >= 5:
        start -= timedelta(days=start.weekday() - 4)
    return start.timestamp()


class WorkspaceMarketCache:
    def __init__(self, clock=time.time, allowed_symbols=None, reference_provider=None, reference_loader=None):
        self.clock = clock
        self.allowed_symbols = allowed_symbols
        self.lock = threading.RLock()
        self.requests = {}
        self.data = {}
        self.epoch = secrets.token_hex(16)
        self.cursors = {}
        self.reference_provider = reference_provider
        self.reference_loader = reference_loader
        self.trading_symbols = set()
        self.references = {}
        self.reference_ids = {}
        self.coverage = {}

    def enable_trading_minutes(self, symbol):
        with self.lock:
            self.trading_symbols.add(symbol)

    def publish_trading_m30(self, symbol, bars, coverage):
        if not bars:
            return False
        selected = bars[-data_length_for_period(1800):]
        # The clock aggregator validates input OHLC and produces immutable rows.
        # Preserve shared historical rows instead of formatting 5,000 bars/tick.
        rows = list(selected)
        with self.lock:
            native_entry = self.data.get((symbol, 1800), {})
            native = native_entry.get("bars", [])
            reference = self.references.get(symbol)
        if reference is None and self.reference_loader:
            reference = self.reference_loader(symbol)
            if reference is not None:
                validate_candle_reference(reference)
        if reference is None:
            native_start = timestamp(native[-1]["dt"]) if native else None
            source_last = timestamp(rows[-1].get("source_last_start"))
            if (not native or native_entry.get("error")
                    or not 0 <= self.clock() - native_entry.get("checked_at", 0) <= 20
                    or source_last is None or not native_start <= source_last < native_start + 1800):
                return False
            proposed = {"basis": "native_v1", "target_basis": "trading_minutes_v1",
                        "source_times": [int(timestamp(row["dt"]) * 1000) for row in native],
                        "target_base_time": int(timestamp(rows[-1]["dt"]) * 1000)}
            reference = self.reference_provider(symbol, proposed) if self.reference_provider else proposed
            validate_candle_reference(reference)
        with self.lock:
            self.references[symbol] = reference
            if symbol not in self.reference_ids:
                self.reference_ids[symbol] = reference_id(reference)
            self.coverage[symbol] = dict(coverage)
            self._cache_rows(symbol, 1800, rows, key=(symbol, "trade_m30"))
        return True

    def _allowed(self):
        return set(self.allowed_symbols()) if self.allowed_symbols is not None else None

    def request(self, symbol):
        with self.lock:
            allowed = self._allowed()
            if allowed is not None and symbol not in allowed:
                return False
            self.requests[symbol] = self.clock()
            return True

    def requested(self):
        with self.lock:
            allowed = self._allowed()
            if allowed is not None:
                self.requests = {symbol: checked for symbol, checked in self.requests.items() if symbol in allowed}
            # Once viewed, keep its existing SDK subscriptions warm for this process.
            return list(self.requests)

    def _prepare_rows(self, period, bars, simulated=False):
        rows = []
        stamps = []
        selected = bars[-data_length_for_period(period):]
        for index, bar in enumerate(selected):
            prices = [bar.get(key) for key in ("open", "high", "low", "close")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in prices):
                raise ValueError("行情包含无效价格")
            o, h, l, c = prices
            if not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError("行情高低价顺序无效")
            stamp = timestamp(bar.get("dt"))
            if simulated and stamp is None:
                stamp = int(self.clock() // period) * period - (len(selected) - index - 1) * period
            if stamp is None or (stamps and stamp <= stamps[-1]):
                raise ValueError("行情时间缺失或顺序无效")
            row = {key: bar[key] for key in ("open", "high", "low", "close", "volume", "oi") if key in bar}
            row["dt"] = iso(stamp)
            rows.append(row)
            stamps.append(stamp)
        for index, row in enumerate(rows):
            start = stamps[index]
            row["closed"] = index < len(rows) - 1
            if period == 86400:
                # A Monday trading-date label can already be live on Friday night.
                row["source_trade_day"] = datetime.fromtimestamp(start, TZ).date().isoformat()
                row["session_boundary"] = iso(daily_boundary(start))
            if row["closed"]:
                if period < 86400:
                    end = min(start + period, stamps[index + 1])
                else:
                    label = datetime.fromtimestamp(start, TZ).replace(hour=18, minute=0, second=0, microsecond=0)
                    end = min(label.timestamp(), daily_boundary(stamps[index + 1]))
                row["end_time"] = row["closed_at"] = iso(end)
        return rows

    def _store_rows(self, symbol, period, rows, previous, token, key=None):
        now = self.clock()
        changed = not previous or previous["bars"][-1] != rows[-1]
        start = timestamp(rows[-1]["dt"])
        latest_possible = (timestamp(rows[-1].get("end_time")) or start + period) if period < 86400 else start
        market_at = min(now, latest_possible) if start <= now else start
        # Entries and rows are replaced, never mutated after publication. Snapshot
        # readers can capture references under the lock and copy outside it.
        self.data[key or (symbol, period)] = {
            "bars": rows, "checked_at": now,
            "market_at": market_at if changed else previous["market_at"],
            "error": None, "history_token": token,
        }

    def cache(self, symbol, period, bars, simulated=False):
        if period not in RETENTION_BARS or not bars:
            return False
        rows = self._prepare_rows(period, bars, simulated)
        return self._cache_rows(symbol, period, rows)

    def _cache_rows(self, symbol, period, rows, key=None):
        with self.lock:
            key = key or (symbol, period)
            previous = self.data.get(key)
            old_rows = previous["bars"] if previous else []
            same_history = (len(old_rows) == len(rows) and old_rows[:-2] == rows[:-2]
                            and [row["dt"] for row in old_rows[-2:]] == [row["dt"] for row in rows[-2:]])
            append_only = False
            if previous and not same_history:
                tail_count = min(2, len(old_rows))
                old_tail = [row["dt"] for row in old_rows[-tail_count:]]
                for index in range(max(0, len(rows) - MAX_APPEND_BARS - tail_count), len(rows) - tail_count + 1):
                    if [row["dt"] for row in rows[index:index + tail_count]] != old_tail:
                        continue
                    added, dropped = len(rows) - index - tail_count, len(old_rows) - tail_count - index
                    append_only = (0 <= dropped <= added <= MAX_APPEND_BARS
                                   and old_rows[dropped:-tail_count] == rows[:index])
                    break
            token = previous["history_token"] if same_history or append_only else secrets.token_hex(16)
            self._store_rows(symbol, period, rows, previous, token, key)
        return True

    def update_tail(self, symbol, period, bars):
        """Replace only a verified unchanged-window tail; otherwise request full data."""
        return self.update_window(symbol, period, bars, appended=0)

    def update_window(self, symbol, period, bars, appended=0):
        """Apply a provider-verified unchanged prefix plus final old bars/new bars."""
        if (period not in RETENTION_BARS or isinstance(appended, bool) or not isinstance(appended, int)
                or not 0 <= appended <= MAX_APPEND_BARS or not bars or len(bars) > MAX_APPEND_BARS + 2):
            return False
        rows = self._prepare_rows(period, bars)
        with self.lock:
            previous = self.data.get((symbol, period))
            if not previous:
                return False
            old_rows = previous["bars"]
            tail_count = min(2, len(old_rows))
            if (len(rows) != tail_count + appended
                    or [r["dt"] for r in old_rows[-tail_count:]] != [r["dt"] for r in rows[:tail_count]]):
                return False
            combined = (old_rows[:-tail_count] + rows)[-data_length_for_period(period):]
            self._store_rows(symbol, period, combined, previous, previous["history_token"])
        return True

    def _cursor(self, symbol, entries, history_token):
        description = {key: {"generation": entry.get("history_token", "missing"),
                             "count": len(entry.get("bars", [])),
                             "first": entry["bars"][0]["dt"] if entry.get("bars") else None,
                             "tail": [row["dt"] for row in entry.get("bars", [])[-2:]]}
                       for key, entry in entries.items()}
        token = hashlib.sha256(repr((self.epoch, symbol, description)).encode()).hexdigest()
        with self.lock:
            known = self.cursors.setdefault(symbol, OrderedDict())
            base = known.get(history_token) if history_token else None
            known[token] = description
            known.move_to_end(token)
            while len(known) > CURSORS_PER_SYMBOL:
                known.popitem(last=False)
        if history_token == token:
            base = description
        starts, drops = {}, {}
        if base is None:
            return token, None, None
        for key, current in description.items():
            old, bars = base[key], entries[key].get("bars", [])
            if old["generation"] != current["generation"]:
                return token, None, None
            tail_count = min(2, old["count"])
            if not tail_count:
                if bars:
                    return token, None, None
                starts[key], drops[key] = 0, 0
                continue
            start = next((index for index in range(max(0, len(bars) - MAX_APPEND_BARS - tail_count), len(bars) - tail_count + 1)
                          if [row["dt"] for row in bars[index:index + tail_count]] == old["tail"]), None)
            if start is None:
                return token, None, None
            added, dropped = len(bars) - start - tail_count, old["count"] - tail_count - start
            if (not 0 <= dropped <= added <= MAX_APPEND_BARS or dropped > old["count"] - tail_count
                    or (dropped == 0 and current["first"] != old["first"])):
                return token, None, None
            starts[key], drops[key] = start, dropped
        return token, starts, drops

    def mark_checked(self, symbol, period):
        with self.lock:
            previous = self.data.get((symbol, period))
            if not previous or previous.get("error"):
                return False
            self.data[(symbol, period)] = {**previous, "checked_at": self.clock()}
            return True

    def mark_failed(self, symbol, period):
        with self.lock:
            previous = self.data.get((symbol, period))
            if previous:
                self.data[(symbol, period)] = {**previous, "error": "该周期行情刷新失败"}
            if period == 900 and (symbol, "trade_m30") in self.data:
                old = self.data[(symbol, "trade_m30")]
                self.data[(symbol, "trade_m30")] = {**old, "error": "30分钟源行情刷新失败"}

    def refresh(self, getter, simulated=False):
        for symbol in self.requested():
            for period in PERIODS.values():
                try:
                    if not self.cache(symbol, period, getter(symbol, period), simulated):
                        self.mark_failed(symbol, period)
                except Exception:
                    # Do not renew old evidence when a subscription fails.
                    self.mark_failed(symbol, period)

    def snapshot(self, symbol, preset, state, simulated=False, history_token=None, preview=False):
        self.request(symbol)
        if not simulated and self.reference_loader and symbol not in self.references:
            restored = self.reference_loader(symbol)
            if restored is not None:
                validate_candle_reference(restored)
                with self.lock:
                    self.references[symbol] = restored
                    self.reference_ids[symbol] = reference_id(restored)
                    # A previously published basis cannot be downgraded by a
                    # disabled/misconfigured producer or a process restart.
                    self.trading_symbols.add(symbol)
        now = self.clock()
        periods, freshness = {}, {}
        with self.lock:
            entries = {key: self.data.get((symbol, duration), {}) for key, duration in PERIODS.items()}
            if symbol in self.trading_symbols and not simulated:
                entries["m30"] = self.data.get((symbol, "trade_m30"), {})
            reference = self.references.get(symbol) if not simulated else None
            coverage = self.coverage.get(symbol)
            current_reference_id = self.reference_ids.get(symbol) if reference else None
        # Apply the six-calendar-month display boundary before creating the
        # cursor. Keep source serials and the fixed drawing reference untouched:
        # both are needed to validate SDK tails and old annotation coordinates.
        cutoff = six_month_cutoff(now)
        for key, cached in entries.items():
            rows = cached.get("bars", [])
            if rows and timestamp(rows[0]["dt"]) < cutoff:
                low, high = 0, len(rows)
                while low < high:
                    middle = (low + high) // 2
                    if timestamp(rows[middle]["dt"]) < cutoff:
                        low = middle + 1
                    else:
                        high = middle
                entries[key] = {**cached, "bars": rows[low:]}
        token, starts, drops = self._cursor(symbol, entries, history_token) if not preview else (None, None, None)
        incremental = starts is not None
        counts = {}
        for key, cached in entries.items():
            bars = cached.get("bars", [])
            counts[key] = len(bars)
            selected = bars[-240:] if preview else bars[starts[key]:] if incremental else bars
            periods[key] = [dict(row) for row in selected]
            freshness[key] = {
                "checked_at": cached.get("checked_at"),
                "quote_at": iso(cached["market_at"]) if cached.get("market_at") else None,
                "available": bool(bars),
                "checked_recent": bool(bars and not cached.get("error") and 0 <= now - cached.get("checked_at", 0) <= 20),
                "changed_recent": bool(bars and 0 <= now - cached.get("market_at", 0) <= 20),
            }
        connected = state.get("connected") is True
        m3_start = timestamp(periods["m3"][-1]["dt"]) if periods["m3"] else None
        m30_start = timestamp(periods["m30"][-1]["dt"]) if periods["m30"] else None
        m30_end = (timestamp(periods["m30"][-1].get("end_time")) or m30_start + PERIODS["m30"]) if m30_start is not None else None
        main_covers_trigger = (m3_start is not None and m30_start is not None
                               and m30_start <= m3_start < m30_end)
        freshness["m30"]["covers_trigger"] = main_covers_trigger
        source_complete = all(row.get("quality") != "incomplete" for row in entries["m30"].get("bars", [])[-120:])
        freshness["m30"]["source_complete"] = source_complete
        day_current = bool(m3_start is not None and periods["day"]
                           and periods["day"][-1]["source_trade_day"] == trading_day(m3_start).isoformat())
        freshness["day"]["matches_trading_day"] = day_current
        fresh = (connected and all(item["checked_recent"] for item in freshness.values())
                 and freshness["m3"]["changed_recent"] and main_covers_trigger and day_current and source_complete)
        last = periods["m3"][-1] if periods["m3"] else None
        # A successful read of unchanged bars is not a new market quote. Keep
        # planning available while distinguishing this from a failed refresh.
        if simulated:
            feed_status, message = "mock", "模拟行情 · 不产生实盘开仓许可"
        elif not connected:
            feed_status, message = "disconnected", "行情连接已断开 · 已有K线仍可用于编写计划"
        elif not all(item["available"] for item in freshness.values()):
            feed_status, message = "loading", "真实行情已连接 · 正在加载各周期K线"
        elif not all(item["checked_recent"] for item in freshness.values()):
            feed_status, message = "stale", "行情刷新未确认 · 保留已有K线与计划"
        elif not source_complete:
            feed_status, message = "aligning", "30分钟源K线存在缺口 · 正在核对，暂用于观察与计划"
        elif not main_covers_trigger or not day_current:
            feed_status, message = "aligning", "各周期行情尚未对齐 · 暂用于观察与计划"
        elif fresh:
            feed_status, message = "live", "真实行情更新中"
        else:
            feed_status, message = "idle", "真实行情已连接 · 暂无新报价，可查看K线与编写计划"
        result = {
            "symbol": symbol, "name": preset.get("name", symbol),
            "tick": preset.get("tick"), "tick_value": preset.get("tick_value"),
            "price": last["close"] if last else None, "source": "mock" if simulated else "live",
            "fresh": bool(fresh), "connected": connected, "as_of": iso(now),
            "feed_status": feed_status,
            "quote_at": freshness["m3"]["quote_at"], "periods": periods, "freshness": freshness,
            "history_token": token, "incremental": incremental, "period_counts": counts,
            "history_retention": {"months": 6, "cutoff": iso(cutoff)},
            "message": message,
        }
        if incremental:
            result.update(base_history_token=history_token, period_drops=drops)
        if reference:
            result["candle_basis"] = {"m30": "trading_minutes_v1"}
            result["anchor_reference_id"] = current_reference_id
            result["history_coverage"] = {"m30": dict(coverage or {})}
            if not incremental:
                result["anchor_reference"] = {"m30": reference}
        if preview:
            result.update(partial=True, fresh=False, feed_status="preview",
                          message="近期K线预览 · 正在加载完整历史")
        return result
