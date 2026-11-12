"""Prepare evidence-based legacy forecast repairs; never contact or modify a server.

Usage: python scripts/repair_forecast_anchors.py --snapshot snapshot.json
       --history history.json --output repair-review.json
The snapshot contains ``plan`` and a full ``market`` response. The output is a
dry-run review artifact, not an executable request. Apply only after re-reading
the exact plan revision and comparing every expected object/point unchanged.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import math
from pathlib import Path


INTERVALS = {"day": 86400000, "m30": 1800000, "m3": 180000}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _stamp(value):
    if _number(value):
        return value if 0 <= value <= 253402300799000 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # An unqualified local time cannot establish an audit cutoff safely.
        return parsed.timestamp() * 1000 if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _scope(plan):
    return plan.get("symbol"), plan.get("board_id") or ""


def _index(items):
    if not isinstance(items, list):
        raise ValueError("Full object and point snapshots are required")
    result = {}
    for item in items:
        identity = item.get("id") if isinstance(item, dict) else None
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("Object and point identities must be unique")
        result[identity] = item
    return result


def _context(obj):
    return obj.get("kind"), obj.get("period"), obj.get("source_period")


def _period(obj):
    source = obj.get("source_period")
    return source if source in INTERVALS else obj.get("period") if obj.get("period") in INTERVALS else None


def _source_base(period, at, market):
    rows = market.get("periods", {}).get(period)
    if not isinstance(rows, list) or not rows:
        return None, "source_history_unavailable"
    counts = market.get("period_counts", {})
    if period in counts and counts[period] != len(rows):
        return None, "source_history_incomplete"
    stamps = [_stamp(row.get("dt", row.get("time"))) for row in rows]
    if any(value is None for value in stamps) or any(a >= b for a, b in zip(stamps, stamps[1:])):
        return None, "invalid_source_timeline"
    if period != "day":
        started = [value for value in stamps if value <= at]
    else:
        # Daily timestamps are trading-date labels, often tomorrow's midnight.
        # Use an actual intraday candle to prove that day's session had begun;
        # the 18:00 metadata boundary itself is not an exchange opening time.
        intraday = [_stamp(row.get("dt", row.get("time")))
                    for key in ("m3", "m30") for row in market.get("periods", {}).get(key, [])]
        intraday = [value for value in intraday if value is not None and value <= at]
        boundaries = [_stamp(row.get("session_boundary")) for row in rows]
        started = []
        for index, base in enumerate(stamps):
            boundary = boundaries[index]
            upper = boundaries[index + 1] if index + 1 < len(rows) else math.inf
            if boundary is not None and upper is not None and any(boundary <= value < upper for value in intraday):
                started.append(base)
        if not started:
            return None, "daily_start_not_proven"
    return (started[-1], None) if started else (None, "source_history_unavailable")


def build_repairs(current_plan, history, market):
    """Return conservative node repairs backed by adjacent full audit snapshots.

    No argument is mutated. Price/style-only updates do not move the evidence
    cutoff. Missing history, explicit absolute coordinates, and period changes
    fail closed. The caller must perform revision + point-value CAS before any
    separately authorized application; this module intentionally has no apply.
    """
    if not isinstance(current_plan, dict) or not current_plan.get("symbol"):
        raise ValueError("A current scoped plan is required")
    revision = current_plan.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("A persisted current revision is required")
    if not isinstance(market, dict) or market.get("symbol") != current_plan["symbol"]:
        raise ValueError("Market and plan symbols differ")
    if market.get("partial") or market.get("incremental") or not isinstance(market.get("periods"), dict):
        raise ValueError("A full market snapshot is required")
    if isinstance(history, dict):
        history = history.get("history")
    if not isinstance(history, list) or not history:
        raise ValueError("Full audit history is required")
    entries = []
    seen = set()
    for entry in history:
        if not isinstance(entry, dict) or not isinstance(entry.get("plan"), dict):
            raise ValueError("Full audit snapshots are required")
        plan, number = entry["plan"], entry.get("revision")
        if (not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in seen
                or number != plan.get("revision") or _scope(plan) != _scope(current_plan)):
            raise ValueError("Audit revisions or plan scope are inconsistent")
        seen.add(number)
        entries.append({"revision": number, "at": entry.get("at"), "plan": plan,
                        "objects": _index(plan.get("objects"))})
    entries.sort(key=lambda entry: entry["revision"])
    if entries[-1]["revision"] != revision or entries[-1]["plan"] != current_plan:
        raise ValueError("Current plan and latest audit snapshot differ; fetch both again")
    report = {"dry_run": True, "symbol": current_plan["symbol"],
              "board_id": current_plan.get("board_id") or "", "expected_revision": revision,
              "repairs": [], "object_edits": [], "skipped": []}

    for obj in _index(current_plan.get("objects")).values():
        if obj.get("kind") not in ("wave", "path"):
            continue
        try:
            points = _index(obj.get("points"))
        except ValueError:
            report["skipped"].append({"object_id": obj["id"], "reason": "unstable_point_ids"})
            continue
        updates = []
        for point in points.values():
            reason, evidence = None, None
            if "chart_anchor" in point:
                reason = "explicit_absolute_time" if point["chart_anchor"] is None else "already_anchored"
            elif not _number(point.get("time")):
                reason = "invalid_point_time"
            elif not _period(obj):
                reason = "source_period_unknown"
            else:
                for index in range(len(entries) - 1, 0, -1):
                    newer, older = entries[index], entries[index - 1]
                    if newer["revision"] != older["revision"] + 1:
                        reason = "audit_gap"
                        break
                    new_obj, old_obj = newer["objects"].get(obj["id"]), older["objects"].get(obj["id"])
                    if new_obj is None or _context(new_obj) != _context(obj) or (old_obj is not None and _context(old_obj) != _context(obj)):
                        reason = "period_or_kind_changed"
                        break
                    try:
                        new_point = _index(new_obj.get("points")).get(point["id"])
                        old_point = _index(old_obj.get("points")).get(point["id"]) if old_obj is not None else None
                    except ValueError:
                        reason = "unstable_point_ids"
                        break
                    if new_point is None or new_point.get("time") != point["time"]:
                        reason = "audit_point_mismatch"
                        break
                    if "chart_anchor" in new_point or old_point is not None and "chart_anchor" in old_point:
                        reason = "prior_explicit_coordinate"
                        break
                    if old_point is None or old_point.get("time") != point["time"]:
                        at, previous_at = _stamp(newer["at"]), _stamp(older["at"])
                        if at is None or previous_at is None or previous_at > at:
                            reason = "audit_time_unproven"
                        else:
                            evidence = {"revision": newer["revision"], "previous_revision": older["revision"],
                                        "at": newer["at"], "change": "inserted" if old_point is None else "time_changed",
                                        "source_period": _period(new_obj), "cutoff": "audit_time"}
                        break
                if reason is None and evidence is None:
                    reason = "no_proven_time_change"
            if evidence is not None:
                at = _stamp(evidence["at"])
                market_at = _stamp(market.get("as_of"))
                if market_at is not None and market_at < at:
                    reason = "market_precedes_evidence"
                else:
                    base, reason = _source_base(evidence["source_period"], at, market)
                    if reason is None:
                        offset = (point["time"] - base) / INTERVALS[evidence["source_period"]]
                        if not 0 < offset <= 10000:
                            reason = "historical_point" if offset <= 0 else "offset_exceeds_limit"
                        else:
                            anchor = {"period": evidence["source_period"], "base_time": int(base), "offset": offset}
                            update = {"id": point["id"], "changes": {"time": point["time"], "chart_anchor": anchor}}
                            evidence.update(base_time=int(base), offset=offset)
                            report["repairs"].append({"object_id": obj["id"], "point_id": point["id"],
                                                      "expected_point": copy.deepcopy(point),
                                                      "expected_object_context": dict(zip(("kind", "period", "source_period"), _context(obj))),
                                                      "evidence": evidence, "node_update": copy.deepcopy(update)})
                            updates.append(update)
            if reason is not None:
                report["skipped"].append({"object_id": obj["id"], "point_id": point["id"], "reason": reason})
        if updates:
            report["object_edits"].append({"id": obj["id"], "node_updates": updates})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path, help="Local JSON containing current plan and full market")
    parser.add_argument("--history", required=True, type=Path, help="Local full audit snapshots JSON")
    parser.add_argument("--output", type=Path, help="Create a new local review JSON; existing files are never overwritten")
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        history = json.loads(args.history.read_text(encoding="utf-8"))
        report = build_repairs(snapshot["plan"], history, snapshot["market"])
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(output)
            print(json.dumps({"dry_run": True, "repairs": len(report["repairs"]), "skipped": len(report["skipped"])}))
        else:
            print(output, end="")
    except (KeyError, ValueError, OSError) as error:
        parser.exit(2, "Repair review failed: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
