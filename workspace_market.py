"""Per-contract workspace chart subscriptions; independent of the legacy focus tab."""
import copy
import math
import threading
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
PERIODS = {"day": 86400, "m30": 1800, "m3": 180}
RETENTION_BARS = {86400: 180, 1800: 6000, 180: 10000}


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
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.requests = {}
        self.data = {}

    def request(self, symbol):
        with self.lock:
            self.requests[symbol] = self.clock()

    def requested(self):
        with self.lock:
            return [symbol for symbol, checked in self.requests.items() if self.clock() - checked < 180]

    def cache(self, symbol, period, bars, simulated=False):
        if period not in PERIODS.values() or not bars:
            return
        now = self.clock()
        rows = []
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
                stamp = int(now // period) * period - (len(selected) - index - 1) * period
            if stamp is None or (rows and stamp <= timestamp(rows[-1]["dt"])):
                raise ValueError("行情时间缺失或顺序无效")
            row = {key: bar[key] for key in ("open", "high", "low", "close", "volume", "oi") if key in bar}
            row["dt"] = iso(stamp)
            rows.append(row)
        with self.lock:
            previous = self.data.get((symbol, period))
            # Corrections to older bars are historical evidence, not a new quote.
            changed = not previous or previous["bars"][-1] != rows[-1]
            start = timestamp(rows[-1]["dt"])
            latest_possible = start + period if period < 86400 else start
            market_at = min(now, latest_possible) if start <= now else start
            self.data[(symbol, period)] = {
                "bars": rows, "checked_at": now,
                "market_at": market_at if changed else previous["market_at"],
                "error": None,
            }

    def refresh(self, getter, simulated=False):
        for symbol in self.requested():
            for period in PERIODS.values():
                try:
                    self.cache(symbol, period, getter(symbol, period), simulated)
                except Exception:
                    # Do not renew old evidence when a subscription fails.
                    with self.lock:
                        old = self.data.get((symbol, period))
                        if old:
                            old["error"] = "该周期行情刷新失败"

    def snapshot(self, symbol, preset, state, simulated=False):
        self.request(symbol)
        now = self.clock()
        periods, freshness = {}, {}
        with self.lock:
            for key, duration in PERIODS.items():
                cached = copy.deepcopy(self.data.get((symbol, duration), {}))
                bars = cached.get("bars", [])
                for index, bar in enumerate(bars):
                    bar["closed"] = index < len(bars) - 1
                    if duration == 86400:
                        # Keep the trading-date label for chart axes; do not mistake
                        # Monday's label on Friday night for a future closing event.
                        bar["source_trade_day"] = datetime.fromtimestamp(timestamp(bar["dt"]), TZ).date().isoformat()
                        bar["session_boundary"] = iso(daily_boundary(timestamp(bar["dt"])))
                    if bar["closed"]:
                        start, next_start = timestamp(bar["dt"]), timestamp(bars[index + 1]["dt"])
                        if duration < 86400:
                            end = min(start + duration, next_start)
                        else:
                            label = datetime.fromtimestamp(start, TZ).replace(hour=18, minute=0, second=0, microsecond=0)
                            end = min(label.timestamp(), daily_boundary(next_start))
                        bar["end_time"] = bar["closed_at"] = iso(end)
                periods[key] = bars
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
        main_covers_trigger = (m3_start is not None and m30_start is not None
                               and m30_start <= m3_start < m30_start + PERIODS["m30"])
        freshness["m30"]["covers_trigger"] = main_covers_trigger
        day_current = bool(m3_start is not None and periods["day"]
                           and periods["day"][-1]["source_trade_day"] == trading_day(m3_start).isoformat())
        freshness["day"]["matches_trading_day"] = day_current
        fresh = (connected and all(item["checked_recent"] for item in freshness.values())
                 and freshness["m3"]["changed_recent"] and main_covers_trigger and day_current)
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
        elif not main_covers_trigger or not day_current:
            feed_status, message = "aligning", "各周期行情尚未对齐 · 暂用于观察与计划"
        elif fresh:
            feed_status, message = "live", "真实行情更新中"
        else:
            feed_status, message = "idle", "真实行情已连接 · 暂无新报价，可查看K线与编写计划"
        return {
            "symbol": symbol, "name": preset.get("name", symbol),
            "tick": preset.get("tick"), "tick_value": preset.get("tick_value"),
            "price": last["close"] if last else None, "source": "mock" if simulated else "live",
            "fresh": bool(fresh), "connected": connected, "as_of": iso(now),
            "feed_status": feed_status,
            "quote_at": freshness["m3"]["quote_at"], "periods": periods, "freshness": freshness,
            "message": message,
        }
