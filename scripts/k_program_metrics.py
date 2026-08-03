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

Two run contexts:

* **Inside docker** (`docker exec ai-companion-mcp python
  scripts/k_program_metrics.py --cron`) — env vars are seeded by
  ``compose`` so hostnames like ``ai-companion-neo4j`` resolve via
  the bridge network.
* **From the host** — the script auto-loads repo-root ``.env`` so
  ``NEO4J_PASSWORD`` etc. are picked up. Override the docker-network
  hostnames to ``localhost`` (preserve auth in the Redis URL)::

      NEO4J_URI=bolt://localhost:7687 \
      REDIS_URL="redis://:${REDIS_PASSWORD}@localhost:6379/0" \
        .venv/bin/python scripts/k_program_metrics.py --cron

Recommended schedule for the S4 soak: a daily cron at midnight UTC
during the 14-day window, writing into a single Monday-rooted
weekly file under ``tasks/``.
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

# Below this many observations a percentile is noise, not a measurement.
# A gate that reports pass/fail from one row states a conclusion it cannot
# support — and "insufficient data" is a different decision for the operator
# than "measured and failing".
_MIN_P95_SAMPLES = 20

# Metrics formally closed as GA gates (owner decision, 2026-08-02). They are
# still collected and reported — closure is a decision not to *gate* on them,
# not a decision to stop looking — but they are excluded from the pass count.
# Authoritative rationale: docs/GA_CHECKLIST.md § "K-program §9 success metrics".
CLOSED_METRICS: dict[str, str] = {
    "contradiction_surfacing": (
        "Closed 2026-08-02. Never had a measurable population: of 14 live "
        "ContradictionFinding nodes, 13 carry entity_slug='' because their "
        "source artifact was deleted before the anchor lookup could resolve it "
        "(one references 'probe-art-1', i.e. test data), so they never got the "
        "HAS_CONTRADICTION edge this metric traverses. The surviving n=1 cannot "
        "support a p95. The metric was always self-declared as an approximation "
        "via summary_updated_at pending UI-view telemetry that was never built. "
        "Gating GA on a single-user corpus's contradiction latency was not worth "
        "the telemetry build; revisit if multi-user or a larger corpus makes the "
        "population real."
    ),
}

# How many of the still-gating metrics must pass. Deliberately held at the
# original absolute count (4) rather than rescaled when a metric was closed:
# retiring something we cannot measure must not reduce how much evidence GA
# requires. 4-of-6 became 4-of-5, which is a STRICTER ratio, not a looser one.
GATE_REQUIRED = 4


def _load_dotenv_into_environ() -> None:
    """Best-effort load of repo-root .env so the operator can run this
    script outside Docker without exporting credentials by hand. Lines
    already in os.environ win (Docker / CI context). Quoted values are
    stripped. Lines starting with '#' or blank are skipped.

    Silent on missing file — the metrics functions already report
    available: false when credentials aren't reachable.
    """
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in os.environ:  # caller-set env wins
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value
    except OSError:
        return


def _get_neo4j():
    """Get a Neo4j driver. Returns None when env not configured.

    Notification filters silence the property-key-does-not-exist warnings
    the driver emits when a fresh corpus hasn't materialized fields like
    ``summary_updated_at`` yet — those are diagnosis, not failure modes,
    and they would otherwise pollute the JSON payload on stdout.
    """
    try:
        from neo4j import GraphDatabase

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD")
        if not password:
            return None
        return GraphDatabase.driver(
            uri,
            auth=(user, password),
            notifications_disabled_classifications=["UNRECOGNIZED"],
        )
    except Exception:  # noqa: BLE001 — driver-side error surfaces in JSON "error" field
        return None


def _get_redis():
    """Get a Redis client. Returns None when env not configured."""
    try:
        import redis

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return redis.from_url(url)
    except Exception:  # noqa: BLE001 — driver-side error surfaces in JSON "error" field
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
    except Exception as exc:  # noqa: BLE001 — error surfaces in JSON "error" field
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 2 — p95 wiki staleness (active entities)
# ---------------------------------------------------------------------------


def metric_wiki_staleness(driver) -> dict[str, Any]:
    """p95 hours since summary_updated_at for REFRESHABLE high-mention entities.

    "Refreshable" = at least one ``(:Artifact)-[:MENTIONS]->`` edge, i.e. the
    entity has a source to summarise from. An entity with none cannot be
    refreshed at all: ``WikiRefreshJob`` returns
    ``skipped="no_source_artifacts"`` and writes nothing, so its
    ``summary_updated_at`` is frozen at whenever it last had a source. Ageing
    those forever and calling the result "wiki staleness" measures orphaning,
    not the freshness of the refresh loop — the thing this gate exists to
    watch. 16 such entities were 25% of the denominator and, being the oldest,
    *were* the p95 on their own.

    They are NOT hidden: ``unrefreshable`` and ``unrefreshable_p95_hours`` are
    reported alongside, so a growing orphan population stays visible rather
    than being laundered out of the gate.
    """
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
                RETURN e.summary_updated_at AS ts,
                       exists((:Artifact)-[:MENTIONS]->(e)) AS refreshable
                """
            )
            ages_hours: list[float] = []
            stuck_hours: list[float] = []
            for r in rows:
                ts = r["ts"]
                try:
                    dt = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    delta_h = (now - dt).total_seconds() / 3600.0
                    if delta_h < 0:
                        continue
                    (ages_hours if r["refreshable"] else stuck_hours).append(delta_h)
                except (ValueError, TypeError):
                    continue
        if not ages_hours:
            return {
                "available": False,
                "reason": "no_refreshable_entities",
                "target_hours": 168,
                "unrefreshable": len(stuck_hours),
            }
        p95 = _p95(ages_hours)
        return {
            "available": True,
            "target_hours": 168,  # 7 days
            "actual_hours": round(p95, 2),
            "denominator": len(ages_hours),
            "meets_target": p95 <= 168,
            # Orphaned-but-summarised entities, surfaced not swallowed.
            "unrefreshable": len(stuck_hours),
            "unrefreshable_p95_hours": (
                round(_p95(stuck_hours), 2) if stuck_hours else None
            ),
        }
    except Exception as exc:  # noqa: BLE001 — error surfaces in JSON "error" field
        return {"available": False, "error": str(exc)}


def _p95(values: list[float]) -> float:
    """p95 by nearest-rank on a copy (callers keep their ordering)."""
    ordered = sorted(values)
    idx = max(0, int(0.95 * len(ordered)) - 1)
    return ordered[idx]


# ---------------------------------------------------------------------------
# Metric 3 — faithfulness on compiled-summary intent (placeholder — RAGAS path)
# ---------------------------------------------------------------------------

# Re-derived 2026-08-03 from measurement, replacing 0.92.
#
# 0.92 was calibrated against a recorded 0.917 that has since been traced to a
# key collision: the nightly fixtures job and the live soak wrote the same
# Redis key, both at n=30, so this metric reported whichever ran last and 0.917
# was the fixtures number. The product has never measured near it.
#
# The floor is a REGRESSION gate, not a quality target: the mean of two
# repeated runs at the shipped configuration (0.763 n=29, 0.776 n=30 → 0.7695)
# minus two run-spreads, rounded down to 0.05.
#
# The spread is 0.013, measured by repeating the run rather than inferred. An
# earlier derivation used 0.05, taken from context_precision moving 0.056
# across the Phase 4 A/B on the theory that it depends only on retrieval and so
# could serve as a control. **That was wrong**: the soak scores
# context_precision over the contexts the ANSWER cited
# (``_contexts_for`` reads ``result["citations"]``), so a terser answer cites
# fewer sources and moves it. Repeating the run with nothing changed put
# context_precision at 0.787 vs 0.790 — the real noise is an order of magnitude
# smaller, and the 0.056 was Phase 4 signal being mistaken for noise.
#
# Ratchet it upward as the product improves; never downward to make a run pass.
FAITHFULNESS_FLOOR = 0.70

# Kept as the documented destination so lowering the gate does not quietly
# lower the ambition. Recorded in docs/GA_CHECKLIST.md alongside why the
# product cannot simply be pushed at it — see ABSTENTION_CEILING.
FAITHFULNESS_ASPIRATION = 0.92

# The counter-metric, and the reason the floor is safe to lower.
#
# Faithfulness is entailed_claims / total_claims, so it rewards asserting less:
# a one-sentence answer makes one easily-entailed claim and scores 1.000 where
# a five-sentence overview makes five and scores 0.600 with ZERO contradictions.
# An abstention scores best of all by leaving the mean entirely. Measured A/B
# (2026-08-03): a richer compiled-summary answer mode raised mean answer length
# 427 → 640 chars and turned two refusals into substantive answers, and
# faithfulness FELL 0.835 → 0.763. Gating on the mean alone would therefore
# select for a worse product.
#
# 0.15 is set against observation: the shipped configuration reads 0.000, and
# the pre-fix polluted population read 0.250. It catches a product that starts
# refusing its own best-covered entities without failing honest refusals.
ABSTENTION_CEILING = 0.15


def metric_faithfulness(redis_client) -> dict[str, Any]:
    """RAGAS faithfulness on the compiled_summary intent class, LIVE only.

    Reads ``cerid:ragas:live_by_intent:compiled_summary``, written by the
    compiled-summary soak from answers the product actually generated.

    It deliberately does NOT read ``cerid:ragas:by_intent:*``, which the nightly
    ``ragas-eval`` job writes from ``golden_dataset.json`` — hand-authored
    fixtures whose ground truths were edited to match their own contexts, and
    which therefore score ~0.9 by construction. Both producers wrote the same
    key and both land ``compiled_summary`` at **n=30** (30 of the golden 50
    classify that way; the soak defaults to 30 entities), so the payload gave no
    way to tell them apart and this metric reported whichever job ran last.
    **That is the origin of the retracted 0.917** — the nightly's fixture number
    read as though it were the product's.

    There is no fallback to the fixtures key when the live one is empty. The
    fallback is the defect: "no soak has run" must read as no data, not as a
    number measured on something else.
    """
    if redis_client is None:
        return {"available": False, "reason": "redis_unavailable"}
    try:
        raw = redis_client.get("cerid:ragas:live_by_intent:compiled_summary")
        if not raw:
            return {
                "available": True,
                "target": 0.92,
                "actual": None,
                "denominator": 0,
                "note": (
                    "no LIVE compiled-summary faithfulness yet — run "
                    "tests/eval/compiled_summary_soak_eval.py in-container. The "
                    "nightly fixture number is NOT a substitute and is not read "
                    "here."
                ),
            }
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        faithfulness = data.get("faithfulness")
        n = data.get("n", 0)
        abstention = data.get("abstention_rate")
        score = float(faithfulness or 0)
        # Both conditions, because the mean alone is gameable downward — see
        # FAITHFULNESS_FLOOR. An unpublished abstention_rate (a soak run from
        # before the counter-metric landed) does not silently pass: it is
        # reported as None and treated as unmet.
        meets = score >= FAITHFULNESS_FLOOR and (
            abstention is not None and float(abstention) <= ABSTENTION_CEILING
        )
        return {
            "available": True,
            "target": FAITHFULNESS_FLOOR,
            "aspiration": FAITHFULNESS_ASPIRATION,
            "actual": round(float(faithfulness), 3) if faithfulness is not None else None,
            "denominator": n,
            "abstention_rate": abstention,
            "abstention_ceiling": ABSTENTION_CEILING,
            "meets_target": meets,
        }
    except Exception as exc:  # noqa: BLE001 — error surfaces in JSON "error" field
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 4 — chunks per answer reduction (compiled-summary class)
# ---------------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    """Median of a list, or None when empty. Even length → mean of the two
    middle samples."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _read_chunks_samples(redis_client, stream: str, bucket: str) -> list[float]:
    """Read one day's chunks-per-answer samples for a stream as floats."""
    key = f"cerid:metrics:chunks_per_answer:samples:{stream}:{bucket}"
    out: list[float] = []
    for v in redis_client.lrange(key, 0, -1) or []:
        try:
            out.append(float(v if isinstance(v, str) else v.decode()))
        except (TypeError, ValueError):
            continue
    return out


def metric_chunks_per_answer(redis_client, now: datetime | None = None) -> dict[str, Any]:
    """Median chunks fetched per answer for the compiled-summary class.

    Compares today's compiled-summary arm against the baseline arm a week
    ago — a stable denominator unaffected by today's traffic mix. Both arms
    are populated per-answer by ``core.utils.cache.record_chunks_per_answer``
    on the ``pkb_answer_with_citations`` path, keyed by
    ``cerid:metrics:chunks_per_answer:samples:<stream>:<bucket>``. ``now`` is
    injectable for tests.
    """
    if redis_client is None:
        return {"available": False, "reason": "redis_unavailable"}
    try:
        now = now or datetime.now(tz=timezone.utc)
        bucket = now.strftime("%Y-%m-%d")
        prev_week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        cur = _median(_read_chunks_samples(redis_client, "compiled_summary", bucket))
        baseline = _median(_read_chunks_samples(redis_client, "baseline", prev_week))
        if cur is None or baseline is None:
            return {
                "available": True,
                "target_reduction_pct": 30.0,
                "actual_reduction_pct": None,
                "note": "needs a week of data; daily buckets accumulate post-deploy",
            }
        reduction = 100.0 * (baseline - cur) / baseline if baseline else 0.0
        return {
            "available": True,
            "target_reduction_pct": 30.0,
            "actual_reduction_pct": round(reduction, 1),
            "current_median": round(cur, 2),
            "baseline_median": round(baseline, 2),
            "meets_target": reduction >= 30.0,
        }
    except Exception as exc:  # noqa: BLE001 — error surfaces in JSON "error" field
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Metric 5 — memory → entity linkage rate
# ---------------------------------------------------------------------------


def metric_memory_entity_linkage(driver) -> dict[str, Any]:
    """% of episodic memories with at least one MENTIONS edge to an Entity.

    Counts BOTH memory representations, because both are recalled:

    * ``(:Memory)`` — verified-claim promotion
      (``core.agents.verified_memory.promote_verified_facts``).
    * ``(:Artifact)`` whose ``filename`` starts with ``memory_`` — the
      conversational path (``core.agents.memory``), which is also what the
      ``/memories`` router serves.

    Both write their Chroma companion into the ``conversations`` collection —
    the one ``recall_memories`` queries — so a linkage figure that covers only
    one of them does not describe the memory surface.

    This previously matched ``(:Artifact)`` carrying a ``memory_type``
    property. **No node has ever had one** — ``memory_type`` lives in Chroma
    metadata, not on the Neo4j node — so the metric divided by an empty
    denominator and reported ``0.0% / meets_target: false``: a *failing gate*
    that actually meant "nothing measured". An empty denominator now reports
    ``available: false`` instead, so unmeasured never again reads as failed.
    """
    if driver is None:
        return {"available": False, "reason": "neo4j_unavailable"}
    try:
        with driver.session() as session:
            row = session.run(
                """
                CALL {
                    MATCH (m:Memory)
                    WHERE coalesce(m.archived, false) = false
                    RETURN count(m) AS total,
                           sum(CASE WHEN exists((m)-[:MENTIONS]->(:Entity))
                                    THEN 1 ELSE 0 END) AS linked
                    UNION ALL
                    MATCH (m:Artifact)
                    WHERE m.filename STARTS WITH 'memory_'
                      AND coalesce(m.archived, false) = false
                    RETURN count(m) AS total,
                           sum(CASE WHEN exists((m)-[:MENTIONS]->(:Entity))
                                    THEN 1 ELSE 0 END) AS linked
                }
                RETURN sum(total) AS total, sum(linked) AS linked
                """
            ).single()
            total = int(row["total"]) if row else 0
            linked = int(row["linked"]) if row else 0
        if not total:
            return {
                "available": False,
                "reason": "no_memories",
                "target_pct": 70.0,
            }
        return {
            "available": True,
            "target_pct": 70.0,
            "actual_pct": round(100.0 * linked / total, 2),
            "denominator": total,
            "numerator": linked,
            "meets_target": linked / total >= 0.70,
        }
    except Exception as exc:  # noqa: BLE001 — error surfaces in JSON "error" field
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
        if len(deltas_hours) < _MIN_P95_SAMPLES:
            # A p95 over one sample is not a p95. This reported
            # `1066h, meets_target: false` from n=1 — a confident-looking gate
            # failure manufactured from a single row. 13 of the 14 live
            # findings carry entity_slug="" because their source artifact was
            # deleted before the anchor lookup ran (one of them references
            # "probe-art-1", i.e. test data), so they never got the
            # HAS_CONTRADICTION edge this metric traverses.
            return {
                "available": False,
                "reason": "insufficient_samples",
                "target_hours": 24,
                "denominator": len(deltas_hours),
                "min_samples": _MIN_P95_SAMPLES,
                "note": (
                    "the summary_updated_at approximation cannot carry this gate; "
                    "closed as a GA gate 2026-08-02 (see closed_rationale)"
                ),
            }
        p95 = _p95(deltas_hours)
        return {
            "available": True,
            "target_hours": 24,
            "actual_hours": round(p95, 2),
            "denominator": len(deltas_hours),
            "meets_target": p95 <= 24,
            "note": "approximation via summary_updated_at; exact UI-view metric requires telemetry wiring",
        }
    except Exception as exc:  # noqa: BLE001 — error surfaces in JSON "error" field
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
    # Top-level meets_target summary. CLOSED_METRICS are collected and
    # reported but excluded from the gate arithmetic — see GA_CHECKLIST.
    targets_met = 0
    targets_eval = 0
    for name, m in snapshot["metrics"].items():
        if name in CLOSED_METRICS:
            m["gate_status"] = "closed"
            m["closed_rationale"] = CLOSED_METRICS[name]
            continue
        if m.get("available") and "meets_target" in m:
            targets_eval += 1
            if m["meets_target"]:
                targets_met += 1
    snapshot["targets_met"] = targets_met
    snapshot["targets_evaluated"] = targets_eval
    snapshot["gate_required"] = GATE_REQUIRED
    snapshot["gate_of"] = len(snapshot["metrics"]) - len(CLOSED_METRICS)
    snapshot["gate_passes"] = targets_met >= GATE_REQUIRED
    snapshot["closed_metrics"] = sorted(CLOSED_METRICS)
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
        def _fmt(value: Any) -> str:
            """Render a metric value for the markdown table — em-dash for
            unavailable or pre-data states so rows scan cleanly."""
            if value is None:
                return "—"
            return str(value)

        m = snapshot["metrics"]
        row = (
            f"| {snapshot['captured_at']} "
            f"| {_fmt(m['wiki_coverage'].get('actual_pct'))} "
            f"| {_fmt(m['wiki_staleness'].get('actual_hours'))} "
            f"| {_fmt(m['faithfulness_compiled'].get('actual'))} "
            f"| {_fmt(m['chunks_per_answer_reduction'].get('actual_reduction_pct'))} "
            f"| {_fmt(m['memory_entity_linkage'].get('actual_pct'))} "
            f"| {_fmt(m['contradiction_surfacing'].get('actual_hours'))} "
            f"| {snapshot.get('targets_met', 0)}/{snapshot.get('targets_evaluated', 0)} |\n"
        )
        f.write(row)
    return week_path


def main() -> None:
    # Load repo-root .env here (CLI/scheduler path) — NOT at import — so that
    # exec-loading this module in a test never mutates the global os.environ.
    _load_dotenv_into_environ()
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
