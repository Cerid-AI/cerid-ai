#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge-architecture success-metrics collector.

Six metrics: wiki_coverage_pct, wiki_p95_staleness_hours,
faithfulness_compiled, chunks_per_answer_reduction_pct,
memory_entity_linkage_pct, contradiction_surfacing_p95_hours.

Usage::

    python scripts/k_program_metrics.py [--output PATH] [--cron]

``--cron`` appends a timestamped row to the current week's
``tasks/<monday>-k-program-metrics.md``. Reads from Neo4j
($NEO4J_*) and Redis ($REDIS_URL); silently emits ``available:
false`` when env unset.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Add src/mcp to path so we can reuse the app's deps
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))


def _get_neo4j():
    """Get a Neo4j driver. Returns None when env not configured."""
    try:
        from neo4j import GraphDatabase

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD")
        if not password:
            return None
        return GraphDatabase.driver(uri, auth=(user, password))
    except Exception:
        return None


def _get_redis():
    """Get a Redis client. Returns None when env not configured."""
    try:
        import redis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return redis.from_url(url)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Metric 1 — wiki coverage (active entities with summary)
# ---------------------------------------------------------------------------


def metric_wiki_coverage(driver) -> dict[str, Any]:
    """% active entities with summary. Active = mention_count >= 5."""
    if driver is None:
        return {"available": False, "reason": "neo4j_unavailable"}
    try:
        with driver.session() as session:
            row = session.run(
                """
                MATCH (e:Entity)
                WHERE coalesce(e.mention_count, 0) >= 5
                WITH count(e) AS active,
                     sum(CASE WHEN e.summary IS NOT NULL THEN 1 ELSE 0 END) AS with_summary
                RETURN active, with_summary
                """
            ).single()
            active = int(row["active"]) if row else 0
            with_summary = int(row["with_summary"]) if row else 0
        return {
            "available": True,
            "target_pct": 80.0,
            "actual_pct": round(100.0 * with_summary / active, 2) if active else 0.0,
            "denominator": active,
            "numerator": with_summary,
            "meets_target": (with_summary / active >= 0.80) if active else False,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 2 — p95 wiki staleness (active entities)
# ---------------------------------------------------------------------------


def metric_wiki_staleness(driver) -> dict[str, Any]:
    """p95 hours since summary_updated_at for entities with mention_count >= 10."""
    if driver is None:
        return {"available": False, "reason": "neo4j_unavailable"}
    try:
        now = datetime.now(tz=timezone.utc)
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (e:Entity)
                WHERE coalesce(e.mention_count, 0) >= 10
                  AND e.summary_updated_at IS NOT NULL
                RETURN e.summary_updated_at AS ts
                """
            )
            ages_hours: list[float] = []
            for r in rows:
                ts = r["ts"]
                try:
                    dt = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    delta_h = (now - dt).total_seconds() / 3600.0
                    if delta_h >= 0:
                        ages_hours.append(delta_h)
                except (ValueError, TypeError):
                    continue
        if not ages_hours:
            return {"available": True, "target_hours": 168, "actual_hours": None, "denominator": 0}
        ages_hours.sort()
        idx = max(0, int(0.95 * len(ages_hours)) - 1)
        p95 = ages_hours[idx]
        return {
            "available": True,
            "target_hours": 168,  # 7 days
            "actual_hours": round(p95, 2),
            "denominator": len(ages_hours),
            "meets_target": p95 <= 168,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 3 — faithfulness on compiled-summary intent (placeholder — RAGAS path)
# ---------------------------------------------------------------------------


def metric_faithfulness(redis_client) -> dict[str, Any]:
    """RAGAS faithfulness on compiled_summary intent class.

    Reads from the nightly RAGAS run output, keyed by intent class.
    The CI ragas-eval job writes results into Redis under
    ``cerid:ragas:by_intent:<intent>`` as a JSON-encoded summary.
    """
    if redis_client is None:
        return {"available": False, "reason": "redis_unavailable"}
    try:
        raw = redis_client.get("cerid:ragas:by_intent:compiled_summary")
        if not raw:
            return {
                "available": True,
                "target": 0.92,
                "actual": None,
                "denominator": 0,
                "note": "no RAGAS by-intent data yet; runs nightly via ragas-eval CI",
            }
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        faithfulness = data.get("faithfulness")
        n = data.get("n", 0)
        return {
            "available": True,
            "target": 0.92,
            "actual": round(float(faithfulness), 3) if faithfulness is not None else None,
            "denominator": n,
            "meets_target": float(faithfulness or 0) >= 0.92,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 4 — chunks per answer reduction (compiled-summary class)
# ---------------------------------------------------------------------------


def metric_chunks_per_answer(redis_client) -> dict[str, Any]:
    """Median chunks fetched per answer for compiled-summary class.

    Read from a Redis time-series populated by
    ``pkb_answer_with_citations`` — keyed by
    ``cerid:metrics:chunks_per_answer:<intent>:<bucket>``.
    """
    if redis_client is None:
        return {"available": False, "reason": "redis_unavailable"}
    try:
        bucket = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        prev_week = (datetime.now(tz=timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        cur_raw = redis_client.get(f"cerid:metrics:chunks_per_answer:compiled_summary:{bucket}")
        baseline_raw = redis_client.get(f"cerid:metrics:chunks_per_answer:baseline:{prev_week}")
        if not cur_raw or not baseline_raw:
            return {
                "available": True,
                "target_reduction_pct": 30.0,
                "actual_reduction_pct": None,
                "note": "needs a week of data; daily buckets accumulate post-deploy",
            }
        cur = float(cur_raw if isinstance(cur_raw, str) else cur_raw.decode())
        baseline = float(baseline_raw if isinstance(baseline_raw, str) else baseline_raw.decode())
        reduction = 100.0 * (baseline - cur) / baseline if baseline else 0.0
        return {
            "available": True,
            "target_reduction_pct": 30.0,
            "actual_reduction_pct": round(reduction, 1),
            "current_median": round(cur, 2),
            "baseline_median": round(baseline, 2),
            "meets_target": reduction >= 30.0,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 5 — memory → entity linkage rate
# ---------------------------------------------------------------------------


def metric_memory_entity_linkage(driver) -> dict[str, Any]:
    """% of memory artifacts with at least one MENTIONS edge."""
    if driver is None:
        return {"available": False, "reason": "neo4j_unavailable"}
    try:
        with driver.session() as session:
            row = session.run(
                """
                MATCH (m:Artifact)
                WHERE m.memory_type IS NOT NULL
                  AND coalesce(m.archived, false) = false
                WITH count(m) AS total,
                     sum(CASE WHEN exists((m)-[:MENTIONS]->(:Entity)) THEN 1 ELSE 0 END) AS linked
                RETURN total, linked
                """
            ).single()
            total = int(row["total"]) if row else 0
            linked = int(row["linked"]) if row else 0
        return {
            "available": True,
            "target_pct": 70.0,
            "actual_pct": round(100.0 * linked / total, 2) if total else 0.0,
            "denominator": total,
            "numerator": linked,
            "meets_target": (linked / total >= 0.70) if total else False,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 6 — contradiction surfacing p95
# ---------------------------------------------------------------------------


def metric_contradiction_surfacing(driver) -> dict[str, Any]:
    """p95(detected_at -> first_user_view_of_entity).

    Approximation: takes the Sunday-cron drift-lint enqueues as the
    "first surface" event since the wiki refresh that follows actually
    shows the contradiction in the UI. The exact "first user view"
    metric requires UI telemetry not yet wired.
    """
    if driver is None:
        return {"available": False, "reason": "neo4j_unavailable"}
    try:
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (f:ContradictionFinding)<-[:HAS_CONTRADICTION]-(e:Entity)
                WHERE f.detected_at IS NOT NULL
                  AND e.summary_updated_at IS NOT NULL
                  AND e.summary_updated_at > f.detected_at
                RETURN f.detected_at AS detected, e.summary_updated_at AS surfaced
                """
            )
            deltas_hours: list[float] = []
            for r in rows:
                try:
                    d = datetime.fromisoformat(r["detected"])
                    s = datetime.fromisoformat(r["surfaced"])
                    if d.tzinfo is None:
                        d = d.replace(tzinfo=timezone.utc)
                    if s.tzinfo is None:
                        s = s.replace(tzinfo=timezone.utc)
                    delta_h = (s - d).total_seconds() / 3600.0
                    if delta_h >= 0:
                        deltas_hours.append(delta_h)
                except (ValueError, TypeError):
                    continue
        if not deltas_hours:
            return {"available": True, "target_hours": 24, "actual_hours": None, "denominator": 0}
        deltas_hours.sort()
        idx = max(0, int(0.95 * len(deltas_hours)) - 1)
        p95 = deltas_hours[idx]
        return {
            "available": True,
            "target_hours": 24,
            "actual_hours": round(p95, 2),
            "denominator": len(deltas_hours),
            "meets_target": p95 <= 24,
            "note": "approximation via summary_updated_at; exact UI-view metric requires telemetry wiring",
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def collect_all() -> dict[str, Any]:
    driver = _get_neo4j()
    redis_client = _get_redis()
    snapshot = {
        "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        "metrics": {
            "wiki_coverage": metric_wiki_coverage(driver),
            "wiki_staleness": metric_wiki_staleness(driver),
            "faithfulness_compiled": metric_faithfulness(redis_client),
            "chunks_per_answer_reduction": metric_chunks_per_answer(redis_client),
            "memory_entity_linkage": metric_memory_entity_linkage(driver),
            "contradiction_surfacing": metric_contradiction_surfacing(driver),
        },
    }
    # Top-level meets_target summary
    targets_met = 0
    targets_eval = 0
    for name, m in snapshot["metrics"].items():
        if m.get("available") and "meets_target" in m:
            targets_eval += 1
            if m["meets_target"]:
                targets_met += 1
    snapshot["targets_met"] = targets_met
    snapshot["targets_evaluated"] = targets_eval
    if driver is not None:
        driver.close()
    return snapshot


def append_to_weekly_log(snapshot: dict[str, Any]) -> Path:
    """Append a one-line summary to tasks/2026-MM-DD-k-program-metrics.md.

    Auto-creates the weekly file (Monday-of-week dated) so each S5 soak
    week gets its own row of snapshots. Markdown table format keeps it
    diff-friendly and human-readable.
    """
    now = datetime.now(tz=timezone.utc)
    # Monday of the current ISO week
    monday = now - timedelta(days=now.weekday())
    week_path = Path(__file__).resolve().parent.parent / "tasks" / f"{monday.strftime('%Y-%m-%d')}-k-program-metrics.md"
    is_new = not week_path.exists()
    with week_path.open("a") as f:
        if is_new:
            f.write("# K-program metrics — week of " + monday.strftime("%Y-%m-%d") + "\n\n")
            f.write("Generated by `scripts/k_program_metrics.py --cron` during the S5 14-day soak.\n\n")
            f.write("| Captured at | Coverage % | Staleness p95 h | Faithfulness | Chunks reduction % | Memory linkage % | Contradiction p95 h | Targets met |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
        m = snapshot["metrics"]
        row = (
            f"| {snapshot['captured_at']} "
            f"| {m['wiki_coverage'].get('actual_pct', '—')} "
            f"| {m['wiki_staleness'].get('actual_hours', '—')} "
            f"| {m['faithfulness_compiled'].get('actual', '—')} "
            f"| {m['chunks_per_answer_reduction'].get('actual_reduction_pct', '—')} "
            f"| {m['memory_entity_linkage'].get('actual_pct', '—')} "
            f"| {m['contradiction_surfacing'].get('actual_hours', '—')} "
            f"| {snapshot.get('targets_met', 0)}/{snapshot.get('targets_evaluated', 0)} |\n"
        )
        f.write(row)
    return week_path


def main() -> None:
    parser = argparse.ArgumentParser(description="K-program §9 metrics collector")
    parser.add_argument("--output", type=str, default=None, help="Write JSON to this path (default: stdout)")
    parser.add_argument("--cron", action="store_true", help="Append weekly markdown row")
    args = parser.parse_args()

    snapshot = collect_all()
    payload = json.dumps(snapshot, indent=2, default=str)

    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(payload)

    if args.cron:
        path = append_to_weekly_log(snapshot)
        print(f"Appended row to {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
