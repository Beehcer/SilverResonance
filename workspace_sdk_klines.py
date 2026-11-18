"""Read-only K-line snapshots for the pinned TQSDK 3.10.1 session.

That SDK only copies its latest row (or shifted tail) from ``root.data`` to
its DataFrame. A late correction to a closed bar can therefore exist in the
received root while the public frame remains stale. This adapter copies the
already-received root, never edits SDK objects and never opens a connection.
Call ``observe`` after every event pump, then ``snapshot`` on that same thread.
"""
from collections.abc import Mapping
from importlib.metadata import version
import math

import numpy as np


FIELDS = ("datetime", "open", "high", "low", "close", "volume", "open_oi", "close_oi")
SUPPORTED_VERSION = "3.10.1"


class SDKKlineSnapshotError(ValueError):
    """The received source cannot be reconciled; retain stale published data."""


class UnsupportedSDKKlineSource(SDKKlineSnapshotError):
    pass


def _integer(value, code):
    if isinstance(value, (bool, str)):
        raise SDKKlineSnapshotError(code)
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise SDKKlineSnapshotError(code) from None
    if result != value:
        raise SDKKlineSnapshotError(code)
    return result


class WorkspaceSDKKlines:
    def __init__(self, api, sdk_version=None):
        self.api = api
        self.sdk_version = str(sdk_version if sdk_version is not None else version("tqsdk"))
        if self.sdk_version != SUPPORTED_VERSION:
            raise UnsupportedSDKKlineSource("unsupported_sdk_version")
        if not isinstance(getattr(api, "_serials", None), dict):
            raise UnsupportedSDKKlineSource("missing_sdk_serial_registry")
        if not all(isinstance(getattr(api, name, None), list) for name in ("_diffs", "_sync_diffs")):
            raise UnsupportedSDKKlineSource("missing_sdk_diff_lists")
        self._states = {}
        self._pending = {}
        self._full = set()
        self._errors = {}
        self._stats = {}

    def _statistics(self, key):
        return self._stats.setdefault(key, {"snapshots": 0, "root_rows_read": 0,
                                            "late_revision_rows": 0, "frame_mismatch_rows": 0,
                                            "failures": 0})

    def _known_keys(self):
        result = set(self._states) | set(self._pending)
        for serial in self.api._serials.values():
            chart = serial.get("chart", {}) if isinstance(serial, Mapping) else {}
            symbol, duration = chart.get("ins_list"), chart.get("duration")
            if isinstance(symbol, str) and "," not in symbol and isinstance(duration, int) and duration > 0 and duration % 1000000000 == 0:
                result.add((symbol, duration // 1000000000))
        return result

    def observe(self):
        """Accumulate changed ids across pumps until a successful snapshot.

        Reading both lists also catches updates pumped during SDK subscription.
        Repeated packs are harmless: ids are a set and values come from the
        current root, never from a stale or partial diff payload.
        """
        changed = set()
        packs = list(self.api._sync_diffs) + list(self.api._diffs)
        for pack in packs:
            if not isinstance(pack, Mapping):
                raise SDKKlineSnapshotError("invalid_sdk_diff_pack")
            if "klines" not in pack:
                continue
            klines = pack["klines"]
            if not isinstance(klines, Mapping):
                keys = self._known_keys()
                self._full.update(keys)
                changed.update(keys)
                continue
            for symbol, periods in klines.items():
                if not isinstance(periods, Mapping):
                    keys = {key for key in self._known_keys() if key[0] == symbol}
                    self._full.update(keys)
                    changed.update(keys)
                    continue
                for raw_period, node in periods.items():
                    try:
                        duration = int(raw_period)
                    except (TypeError, ValueError):
                        continue
                    if duration <= 0 or duration % 1000000000:
                        continue
                    key = (symbol, duration // 1000000000)
                    changed.add(key)
                    if not isinstance(node, Mapping):
                        self._full.add(key)
                        continue
                    if "data" not in node:
                        continue
                    data = node["data"]
                    if not isinstance(data, Mapping):
                        self._full.add(key)
                        continue
                    pending = self._pending.setdefault(key, set())
                    for row_id in data:
                        try:
                            number = int(row_id)
                        except (TypeError, ValueError):
                            self._full.add(key)
                            continue
                        if str(number) != str(row_id) or number < 0:
                            self._full.add(key)
                            continue
                        pending.add(number)
        return changed

    def has_pending(self, symbol, seconds):
        key = (symbol, seconds)
        return key in self._full or bool(self._pending.get(key))

    def _context(self, symbol, seconds, frame):
        meta = self.api._serials.get(id(frame))
        if not isinstance(meta, Mapping) or meta.get("df") is not frame:
            raise SDKKlineSnapshotError("unregistered_sdk_frame")
        roots, chart = meta.get("root"), meta.get("chart")
        if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], Mapping) or not isinstance(chart, Mapping):
            raise UnsupportedSDKKlineSource("unsupported_sdk_serial_shape")
        root = roots[0]
        if root.get("_path") != ["klines", symbol, str(seconds * 1000000000)]:
            raise SDKKlineSnapshotError("root_symbol_period_mismatch")
        tree = getattr(self.api, "_data", None)
        for part in ("klines", symbol, str(seconds * 1000000000)):
            tree = tree.get(part) if isinstance(tree, Mapping) else None
        if tree is not root:
            raise SDKKlineSnapshotError("detached_sdk_serial_root")
        if chart.get("ins_list") != symbol or chart.get("duration") != seconds * 1000000000:
            raise SDKKlineSnapshotError("chart_symbol_period_mismatch")
        if meta.get("adj_type") is not None:
            raise UnsupportedSDKKlineSource("adjusted_serial_not_supported")
        if meta.get("init") is not True:
            raise SDKKlineSnapshotError("sdk_serial_not_ready")
        if not isinstance(root.get("data"), Mapping):
            raise SDKKlineSnapshotError("root_data_missing")
        if not all(field in frame for field in ("id",) + FIELDS):
            raise SDKKlineSnapshotError("frame_fields_missing")
        ids_raw = np.asarray(frame["id"])
        stamps = np.asarray(frame["datetime"])
        if ids_raw.ndim != 1 or len(ids_raw) == 0 or len(stamps) != len(ids_raw) or meta.get("width") != len(ids_raw):
            raise SDKKlineSnapshotError("frame_window_invalid")
        if not np.all(np.isfinite(ids_raw)) or not np.all(ids_raw == np.floor(ids_raw)):
            raise SDKKlineSnapshotError("frame_ids_invalid")
        ids = ids_raw.astype(np.int64)
        if np.any(np.diff(ids) != 1):
            raise SDKKlineSnapshotError("frame_ids_not_contiguous")
        valid_stamps = stamps[ids >= 0]
        if (not np.all(np.isfinite(valid_stamps)) or np.any(valid_stamps <= 0)
                or np.any(valid_stamps != np.floor(valid_stamps)) or np.any(np.diff(valid_stamps) <= 0)):
            raise SDKKlineSnapshotError("frame_timestamps_invalid")
        if _integer(root.get("last_id"), "root_last_id_invalid") != int(ids[-1]):
            raise SDKKlineSnapshotError("root_frame_last_id_mismatch")
        if not all(len(frame[field]) == len(ids) for field in FIELDS):
            raise SDKKlineSnapshotError("frame_columns_misaligned")
        return meta, root, ids, stamps

    def _read_row(self, root, row_id, frame_stamp, default):
        if row_id < 0:
            if not isinstance(default, Mapping) or not all(field in default for field in FIELDS):
                raise UnsupportedSDKKlineSource("sdk_defaults_missing")
            # SDK prehistory placeholders remain empty and are filtered by the reader.
            return np.asarray([default[field] for field in FIELDS], dtype=float)
        item = root["data"].get(str(row_id))
        if not isinstance(item, Mapping) or not all(field in item for field in FIELDS):
            raise SDKKlineSnapshotError("root_row_missing_or_incomplete")
        stamp = _integer(item["datetime"], "root_datetime_invalid")
        if stamp <= 0 or stamp != _integer(frame_stamp, "frame_datetime_invalid"):
            raise SDKKlineSnapshotError("root_frame_datetime_mismatch")
        if any(value is None or isinstance(value, (str, bool)) for value in (item[field] for field in FIELDS)):
            raise SDKKlineSnapshotError("root_row_invalid_value")
        try:
            values = np.asarray([item[field] for field in FIELDS], dtype=float)
        except (TypeError, ValueError, OverflowError):
            raise SDKKlineSnapshotError("root_row_invalid_value") from None
        if not np.all(np.isfinite(values)):
            raise SDKKlineSnapshotError("root_row_nonfinite")
        _, op, high, low, close, volume, open_oi, close_oi = values
        if low <= 0 or high < max(op, close) or low > min(op, close) or high < low:
            raise SDKKlineSnapshotError("root_row_invalid_ohlc")
        if min(volume, open_oi, close_oi) < 0 or any(v != math.floor(v) for v in (volume, open_oi, close_oi)):
            raise SDKKlineSnapshotError("root_row_invalid_volume_oi")
        return values

    def snapshot(self, symbol, seconds, frame):
        """Return immutable columns, sourced solely from this session's root."""
        key = (symbol, seconds)
        stats = self._statistics(key)
        try:
            meta, root, ids, stamps = self._context(symbol, seconds, frame)
            state = self._states.get(key)
            rebuild = state is None or state["frame"] is not frame or key in self._full
            if state is not None and ids[-1] < state["ids"][-1]:
                # A provider rewind/reconnect invalidates assumptions about the
                # overlapping retained rows, even if individual ids still match.
                rebuild = True
            previous = None if rebuild else state
            same_window = previous is not None and np.array_equal(previous["ids"], ids)
            if same_window:
                if not np.array_equal(previous["matrix"][:, 0], stamps, equal_nan=True):
                    raise SDKKlineSnapshotError("frame_datetime_changed_for_existing_id")
                matrix = previous["matrix"]
                todo = {row_id for row_id in self._pending.get(key, ()) if ids[0] <= row_id <= ids[-1]}
            else:
                matrix = np.empty((len(ids), len(FIELDS)), dtype=float)
                todo = set(map(int, ids))
                if previous is not None:
                    lo, hi = max(int(ids[0]), int(previous["ids"][0])), min(int(ids[-1]), int(previous["ids"][-1]))
                    if hi >= lo:
                        start, old_start, count = lo - int(ids[0]), lo - int(previous["ids"][0]), hi - lo + 1
                        rows = previous["matrix"][old_start:old_start + count]
                        if not np.array_equal(rows[:, 0], stamps[start:start + count], equal_nan=True):
                            raise SDKKlineSnapshotError("rolling_datetime_mismatch")
                        matrix[start:start + count] = rows
                        todo.difference_update(range(lo, hi + 1))
                        todo.update(row_id for row_id in self._pending.get(key, ()) if lo <= row_id <= hi)
            # Newest values are cheap to recheck even after an internal subscription pump.
            todo.update(map(int, ids[-2:]))
            dirty = not same_window
            revised = []
            mismatch = []
            for row_id in sorted(todo):
                index = row_id - int(ids[0])
                values = self._read_row(root, row_id, stamps[index], meta.get("default"))
                stats["root_rows_read"] += 1
                changed = rebuild or not same_window or not np.array_equal(matrix[index], values, equal_nan=True)
                old_index = row_id - int(previous["ids"][0]) if previous is not None else -1
                old_exists = previous is not None and 0 <= old_index < len(previous["ids"])
                if old_exists and row_id < previous["ids"][-1] and not np.array_equal(previous["matrix"][old_index], values, equal_nan=True):
                    revised.append(row_id)
                if row_id >= 0:
                    frame_values = np.asarray([frame[field][index] for field in FIELDS], dtype=float)
                    if not np.array_equal(frame_values, values, equal_nan=True):
                        mismatch.append(row_id)
                if changed:
                    if not dirty:
                        matrix = matrix.copy()
                        dirty = True
                    matrix[index] = values
            if dirty:
                matrix.setflags(write=False)
                columns = {field: matrix[:, index] for index, field in enumerate(FIELDS)}
                ids = ids.copy()
                ids.setflags(write=False)
            else:
                columns, ids = previous["columns"], previous["ids"]
            recent = list(dict.fromkeys((state["recent"] if state else []) + revised + mismatch))[-5:]
            stats["snapshots"] += 1
            stats["late_revision_rows"] += len(revised)
            # Count newly observed discrepancies, rather than counting a persistent stale frame each pump.
            prior_mismatch = state["mismatch_values"] if state else {}
            mismatch_values = {row_id: tuple(matrix[row_id - int(ids[0])]) for row_id in mismatch}
            stats["frame_mismatch_rows"] += sum(prior_mismatch.get(row_id) != value for row_id, value in mismatch_values.items())
            retained_mismatch = {row_id: values for row_id, values in prior_mismatch.items() if ids[0] <= row_id <= ids[-1]}
            for row_id in todo:
                retained_mismatch.pop(row_id, None)
            retained_mismatch.update(mismatch_values)
            self._states[key] = {"frame": frame, "ids": ids, "matrix": matrix, "columns": columns,
                                 "recent": recent, "mismatch_values": retained_mismatch}
            self._pending.pop(key, None)
            self._full.discard(key)
            self._errors.pop(key, None)
            return columns
        except (SDKKlineSnapshotError, TypeError, ValueError, KeyError, IndexError) as error:
            stats["failures"] += 1
            self._errors[key] = str(error) if isinstance(error, SDKKlineSnapshotError) else "invalid_sdk_source_structure"
            if isinstance(error, SDKKlineSnapshotError):
                raise
            raise SDKKlineSnapshotError("invalid_sdk_source_structure") from error

    def diagnostics(self, symbol, seconds, limit=3):
        """Small market-only comparison; no credentials, accounts or raw packs."""
        key = (symbol, seconds)
        limit = max(0, min(5, int(limit)))
        result = {"version": self.sdk_version, "symbol": symbol, "seconds": seconds,
                  "pending": self.has_pending(symbol, seconds), "error": self._errors.get(key),
                  **dict(self._statistics(key)), "rows": []}
        state = self._states.get(key)
        if state is None or not limit:
            return result
        try:
            _, root, ids, _ = self._context(symbol, seconds, state["frame"])
            candidates = list(dict.fromkeys(list(map(int, ids[-limit:])) + state["recent"]))[-limit:]
            def clean(values):
                return {field: float(value) if math.isfinite(float(value)) else None for field, value in zip(FIELDS, values)}
            for row_id in candidates:
                if row_id < 0 or not ids[0] <= row_id <= ids[-1] or not state["ids"][0] <= row_id <= state["ids"][-1]:
                    continue
                index, old_index = row_id - int(ids[0]), row_id - int(state["ids"][0])
                raw = root["data"].get(str(row_id), {})
                result["rows"].append({"id": row_id,
                                       "raw": clean([raw.get(field, float("nan")) for field in FIELDS]),
                                       "frame": clean([state["frame"][field][index] for field in FIELDS]),
                                       "snapshot": clean(state["matrix"][old_index])})
        except (SDKKlineSnapshotError, TypeError, ValueError, KeyError, IndexError):
            result["comparison_error"] = "source_comparison_unavailable"
        return result
