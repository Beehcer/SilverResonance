"""Trading-clock candles built from 3/15-minute rows, with no SDK/network I/O.

``trading_time`` uses Quote.trading_time's day/night pairs. Night endpoints
may exceed 24:00 (TQ uses 25:00 for 01:00). The recurring schedule is not a
holiday calendar: pass actual trading dates when available. No absent price
is filled and a missing source row never shifts subsequent candle boundaries.
Official field format: https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.objs.html
"""

from bisect import bisect_left, bisect_right
from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
import math
import re


TZ = timezone(timedelta(hours=8))
SOURCE_SECONDS = 180
PERIOD_SECONDS = 1800
CANDLE_BASIS = "trading_minutes_v1"


def _datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("K线时间必须为ISO时间或datetime")
    return (parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ))


def _iso(value):
    return value.isoformat(timespec="seconds")


def _date(value):
    if isinstance(value, datetime):
        return _datetime(value).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", value):
        raise ValueError("交易时段时间无效")
    parts = [int(part) for part in value.split(":")]
    hour, minute = parts[:2]
    second = parts[2] if len(parts) == 3 else 0
    if hour > 36 or minute >= 60 or second >= 60:
        raise ValueError("交易时段时间无效")
    return hour * 3600 + minute * 60 + second


def _schedule(trading_time, source_period):
    result = {}
    for kind in ("night", "day"):
        raw = (trading_time.get(kind) if hasattr(trading_time, "get")
               else getattr(trading_time, kind, None))
        if not isinstance(raw, (list, tuple)):
            raise ValueError("需要已就绪的日盘和夜盘交易时段")
        pairs = []
        for pair in raw:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("交易时段必须为起止时间对")
            start, end = map(_clock, pair)
            if kind == "night":
                if start < 18 * 3600:
                    start += 86400
                if end <= start:
                    end += 86400
                valid = 18 * 3600 <= start < end <= 36 * 3600
            else:
                valid = 6 * 3600 <= start < end <= 18 * 3600
            if (not valid or start % source_period or end % source_period
                    or (end - start) % source_period):
                raise ValueError("交易时段必须能被源K周期完整分割")
            pairs.append((start, end))
        pairs.sort()
        if any(previous[1] > current[0] for previous, current in zip(pairs, pairs[1:])):
            raise ValueError("交易时段重叠")
        result[kind] = pairs
    if not result["day"]:
        raise ValueError("日盘交易时段不能为空")
    return result


class _Calendar:
    def __init__(self, trading_days):
        self.days = sorted(set(map(_date, trading_days))) if trading_days is not None else None
        if self.days == []:
            raise ValueError("交易日列表不能为空")

    def next(self, value):
        if self.days is not None:
            index = bisect_right(self.days, value)
            if index == len(self.days):
                raise ValueError("交易日列表未覆盖夜盘对应日期")
            return self.days[index]
        result = value + timedelta(days=1)
        while result.weekday() >= 5:
            result += timedelta(days=1)
        return result

    def previous(self, value):
        if self.days is not None:
            index = bisect_left(self.days, value)
            # The first known trading date's preceding night may not be in the
            # calendar. Actual observed night timestamps override this fallback.
            if index > 0:
                return self.days[index - 1]
        result = value - timedelta(days=1)
        while result.weekday() >= 5:
            result -= timedelta(days=1)
        return result

    def check_day(self, value):
        if self.days is not None:
            if value not in self.days:
                raise ValueError("K线日期不在提供的交易日中")
        elif value.weekday() >= 5:
            raise ValueError("周末白盘K线无法归入商品交易时段")


def _midnight(value):
    return datetime.combine(value, time.min, tzinfo=TZ)


def _locate(dt, schedule, calendar):
    seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
    if dt.microsecond:
        raise ValueError("源K线起点未对齐")
    for start, end in schedule["day"]:
        if start <= seconds < end:
            calendar.check_day(dt.date())
            return dt.date(), None
    for base in (dt.date(), dt.date() - timedelta(days=1)):
        offset = int((dt - _midnight(base)).total_seconds())
        for start, end in schedule["night"]:
            if start <= offset < end:
                return calendar.next(base), base
    raise ValueError("K线起点不在提供的交易时段内")


def _slots(day, night_date, schedule, source_period):
    result = []
    for kind, base in (("night", night_date), ("day", day)):
        midnight = _midnight(base)
        for start, end in schedule[kind]:
            result.extend(midnight + timedelta(seconds=value)
                          for value in range(start, end, source_period))
    if any(a >= b for a, b in zip(result, result[1:])):
        raise ValueError("夜盘与日盘日期冲突")
    return result


def _validate_row(row):
    values = [row.get(key) for key in ("open", "high", "low", "close")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("源K线包含无效价格")
    opening, high, low, close = values
    if not low <= min(opening, close) <= max(opening, close) <= high:
        raise ValueError("源K线高低价无效")
    for key in ("volume", "oi"):
        value = row.get(key, 0)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or (key == "volume" and value < 0)):
            raise ValueError("源K线成交量或持仓变化无效")


def aggregate_m30(bars, trading_time, *, source_period=SOURCE_SECONDS,
                  as_of=None, trading_days=None,
                  drop_partial_start=True):
    """Return 30-trading-minute OHLC rows without shifting across missing data.

    Day/night schedules are half-open intervals. All scheduled trading slots in
    one trading day form a clock; breaks do not consume minutes. The clock resets
    at the next trading day, so a final 15-minute day-end candle is valid.

    ``complete`` means every planned source slot was received; it does not mean
    the latest source candle has finished. ``closed`` also requires the planned
    end to have passed the supplied observation watermark. Without ``as_of``, the
    latest source start (or end when explicitly closed) is the watermark. Neither
    field asserts a live feed. Missing elapsed slots yield quality=incomplete;
    future absent slots remain pending. Entirely absent groups are never priced.

    A recurring quote schedule cannot prove historical holiday cancellations.
    ``trading_days`` handles the trading-date mapping; observed night dates take
    precedence over an inferred preceding trading date. Omitted whole sessions
    are not invented. The 16 configured commodities' night durations are exact
    30-minute multiples, so omitted full nights do not shift daytime boundaries.
    """
    if isinstance(source_period, bool) or source_period not in (180, 900):
        raise ValueError("源K周期必须为180或900秒")
    slots_per_group = PERIOD_SECONDS // source_period
    schedule = _schedule(trading_time, source_period)
    calendar = _Calendar(trading_days)
    if not bars:
        return []
    days = OrderedDict()
    previous = None
    first = None
    for row in bars:
        _validate_row(row)
        dt = _datetime(row.get("dt"))
        if previous is not None and dt <= previous:
            raise ValueError("源K线时间必须严格递增且不可重复")
        previous = dt
        first = first or dt
        day, night_date = _locate(dt, schedule, calendar)
        bucket = days.setdefault(day, {"rows": [], "night_date": None})
        if night_date is not None:
            if bucket["night_date"] not in (None, night_date):
                raise ValueError("同一交易日存在两个不同夜盘日期")
            bucket["night_date"] = night_date
        bucket["rows"].append((dt, row))
    watermark = (_datetime(as_of) if as_of is not None else
                 previous + timedelta(seconds=source_period if bars[-1].get("closed") is True else 0))
    if watermark < previous:
        raise ValueError("观察时间早于最后一根源K线")
    result = []
    for day, bucket in days.items():
        night_date = bucket["night_date"] or calendar.previous(day)
        slots = _slots(day, night_date, schedule, source_period)
        slot_index = {dt: index for index, dt in enumerate(slots)}
        groups = OrderedDict()
        for dt, row in bucket["rows"]:
            if dt not in slot_index:
                raise ValueError("源K线起点未对齐交易时钟")
            groups.setdefault(slot_index[dt] // slots_per_group, []).append((dt, row))
        previous_group = None
        for group_index, observed in groups.items():
            planned = slots[group_index * slots_per_group:(group_index + 1) * slots_per_group]
            truncated_start = observed[0][0] == first and first != planned[0]
            if truncated_start and drop_partial_start:
                previous_group = group_index
                continue
            received = {dt for dt, _ in observed}
            missing = [dt for dt in planned if dt <= watermark and dt not in received]
            complete = len(observed) == len(planned)
            end = planned[-1] + timedelta(seconds=source_period)
            closed = complete and watermark >= end
            rows = [row for _, row in observed]
            item = {
                "dt": _iso(planned[0]), "end_time": _iso(end),
                "open": rows[0]["open"], "high": max(row["high"] for row in rows),
                "low": min(row["low"] for row in rows), "close": rows[-1]["close"],
                "volume": sum(row.get("volume", 0) for row in rows),
                "oi": sum(row.get("oi", 0) for row in rows),
                "closed": closed, "complete": complete,
                "quality": "incomplete" if missing else "complete" if closed else "live",
                "source_trade_day": day.isoformat(), "candle_basis": CANDLE_BASIS,
                "source_period_seconds": source_period,
                "expected_slots": len(planned), "observed_slots": len(observed),
                "missing_slots": len(missing),
                "pending_slots": len(planned) - len(observed) - len(missing),
                "missing_times": [_iso(dt) for dt in missing],
                "source_start": _iso(observed[0][0]),
                "source_last_start": _iso(observed[-1][0]),
                "trading_minutes": len(planned) * source_period // 60,
                "truncated_start": truncated_start,
                "unobserved_prior_groups": (group_index - previous_group - 1
                                             if previous_group is not None else 0),
                "calendar_quality": "provided" if calendar.days is not None else "weekday_assumed",
            }
            if closed:
                item["closed_at"] = item["end_time"]
            result.append(item)
            previous_group = group_index
    return result


class TradingMinuteAggregator:
    """Bounded source history with immutable-result, per-trading-day rebuilding.

    ``replace`` is for first load or a provider-detected historical correction.
    ``update_tail`` accepts the last min(2, old_count) rows followed by at most64
    newly appended rows, after the SDK reader has checked the untouched prefix.
    It validates overlap; on failure it leaves all cached data unchanged and
    raises ValueError so the caller can fall back to ``replace``. No SDK is used.

    The caller must not mutate returned bar dictionaries. Old returned rows are
    never mutated by this class. A schedule/calendar change requires a new
    instance or ``replace`` with an instance constructed for that configuration.
    """

    def __init__(self, trading_time, *, source_period=SOURCE_SECONDS,
                 trading_days=None, max_source_bars=10000):
        if isinstance(source_period, bool) or source_period not in (180, 900):
            raise ValueError("源K周期必须为180或900秒")
        if type(max_source_bars) is not int or not 2 <= max_source_bars <= 10000:
            raise ValueError("源K历史上限必须在2到10000之间")
        self.schedule = _schedule(trading_time, source_period)
        self.trading_time = {key: [list(pair) for pair in
                                  (trading_time.get(key) if hasattr(trading_time, "get") else getattr(trading_time, key))]
                             for key in ("day", "night")}
        self.calendar = _Calendar(trading_days)
        self.trading_days = list(self.calendar.days) if self.calendar.days is not None else None
        self.source_period = source_period
        self.max_source_bars = max_source_bars
        self._days = OrderedDict()
        self._result_days = OrderedDict()
        self._count = 0
        self._tail = []
        self.bars = []
        self.coverage = {}
        self.last_rebuilt_days = ()

    def _day(self, row):
        return _locate(_datetime(row["dt"]), self.schedule, self.calendar)[0]

    def _watermark(self, rows, as_of):
        if as_of is not None:
            return _datetime(as_of)
        return (_datetime(rows[-1]["dt"]) +
                timedelta(seconds=self.source_period if rows[-1].get("closed") is True else 0))

    def _aggregate_day(self, day, rows, first_day, watermark):
        result = aggregate_m30(rows, self.trading_time, source_period=self.source_period,
                               as_of=watermark, trading_days=self.trading_days,
                               drop_partial_start=day == first_day)
        # A missing first slot on a later day is a data gap, not truncation of
        # the historical window. Per-day calculation must preserve that distinction.
        if day != first_day and result:
            result[0]["truncated_start"] = False
        return result

    def _publish(self, days, result_days, count, rebuilt):
        bars = [row for group in result_days.values() for row in group]
        raw_first = next(iter(days.values()))[0] if days else None
        last = next(reversed(days.values())) if days else []
        tail = last[-2:]
        if len(tail) < 2 and len(days) > 1:
            previous = list(days.values())[-2]
            tail = previous[-(2 - len(tail)):] + tail
        coverage = {
            "candle_basis": CANDLE_BASIS,
            "source_period_seconds": self.source_period,
            "source_history_limit": self.max_source_bars,
            "available_source_bars": count,
            "available_candles": len(bars),
            "first_source_at": raw_first["dt"] if raw_first else None,
            "last_source_at": tail[-1]["dt"] if tail else None,
            "first_candle_at": bars[0]["dt"] if bars else None,
            "last_candle_at": bars[-1]["dt"] if bars else None,
            "incomplete_candles": sum(row["quality"] == "incomplete" for row in bars),
            "unobserved_groups": sum(row["unobserved_prior_groups"] for row in bars),
            "dropped_initial_source_bars": (sum(1 for row in next(iter(days.values()), [])
                                                 if _datetime(row["dt"]) < _datetime(bars[0]["dt"]))
                                              if bars else count),
            "calendar_quality": "provided" if self.trading_days is not None else "weekday_assumed",
            "history_complete": False,  # A finite SDK window never proves lifetime coverage.
        }
        self._days, self._result_days = days, result_days
        self._count, self._tail = count, tail
        self.bars, self.coverage = bars, coverage
        self.last_rebuilt_days = tuple(day.isoformat() for day in rebuilt)
        return self.bars

    def replace(self, bars, *, as_of=None):
        source = [dict(row) for row in bars[-self.max_source_bars:]]
        if not source:
            return self._publish(OrderedDict(), OrderedDict(), 0, ())
        # Validate ordering across day boundaries as well as within each day.
        previous = None
        days = OrderedDict()
        for row in source:
            _validate_row(row)
            stamp = _datetime(row["dt"])
            if previous is not None and stamp <= previous:
                raise ValueError("源K线时间必须严格递增且不可重复")
            previous = stamp
            days.setdefault(self._day(row), []).append(row)
        watermark = self._watermark(source, as_of)
        first_day = next(iter(days))
        result_days = OrderedDict((day, self._aggregate_day(day, rows, first_day, watermark))
                                  for day, rows in days.items())
        return self._publish(days, result_days, len(source), days)

    def update_tail(self, bars, *, appended=0, as_of=None):
        if (not self._count or type(appended) is not int or not 0 <= appended <= 64
                or len(bars) != min(2, self._count) + appended):
            raise ValueError("增量尾K数量不匹配，需要重新加载完整源K")
        tail_count = min(2, self._count)
        if ([_datetime(row["dt"]) for row in bars[:tail_count]] !=
                [_datetime(row["dt"]) for row in self._tail]):
            raise ValueError("增量尾K时间不匹配，需要重新加载完整源K")
        source = [dict(row) for row in bars]
        previous = None
        row_days = []
        for row in source:
            _validate_row(row)
            stamp = _datetime(row["dt"])
            if previous is not None and stamp <= previous:
                raise ValueError("增量源K时间必须严格递增")
            previous = stamp
            row_days.append(self._day(row))
        watermark = self._watermark(source, as_of)
        days = self._days.copy()
        affected = set(row_days)
        # Copy only the old tail's day buckets before removing/replacing rows.
        remove = tail_count
        while remove:
            day = next(reversed(days))
            group = list(days[day])
            affected.add(day)
            take = min(remove, len(group))
            group = group[:-take]
            remove -= take
            if group:
                days[day] = group
            else:
                del days[day]
        for day, row in zip(row_days, source):
            if day not in days:
                days[day] = []
            elif days[day] is self._days.get(day):
                days[day] = list(days[day])
            days[day].append(row)
        count = self._count + appended
        drop = max(0, count - self.max_source_bars)
        count -= drop
        while drop:
            day = next(iter(days))
            affected.add(day)
            take = min(drop, len(days[day]))
            group = days[day][take:]
            drop -= take
            if group:
                days[day] = group
            else:
                del days[day]
        first_day = next(iter(days))
        # If the old first day was removed, the new initial partial group gets
        # the same safe truncation treatment as a complete history reload.
        if first_day != next(iter(self._days)):
            affected.add(first_day)
        result_days = OrderedDict()
        rebuilt = []
        for day, rows in days.items():
            if day in affected:
                result_days[day] = self._aggregate_day(day, rows, first_day, watermark)
                rebuilt.append(day)
            else:
                result_days[day] = self._result_days[day]
        return self._publish(days, result_days, count, rebuilt)
