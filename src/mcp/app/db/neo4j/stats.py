# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Knowledge Stats — Cypher queries for the corpus-growth surface.

Powers ``/observability/knowledge-stats`` (current state) and
``/observability/knowledge-stats/history`` (7d / 30d sparklines).

The current-state query is a single Cypher round-trip with UNION ALL
sub-queries; the history query reads daily snapshots written by the
``knowledge_stats_snapshot`` cron job.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("ai-companion.db.stats")


# ---------------------------------------------------------------------------
# Current-state query
# ---------------------------------------------------------------------------


def fetch_current_stats(driver) -> dict[str, Any]:
    """Single Cypher round-trip returning the canonical corpus snapshot.

    Shape matches ``tasks/2026-05-24-ingestion-experience-plan.md`` §2.6.
    Returns a dict with ``nodes``, ``edges``, ``chunks``, ``diversity``,
    ``growth``, and ``since_last_visit`` keys.

    Defensive: every sub-count is wrapped in ``coalesce(_, 0)`` so an
    empty database returns zeros, not ``None``s.
    """
    cypher = """
    CALL {
        MATCH (a:Artifact) RETURN count(a) AS artifacts
    }
    CALL {
        MATCH (e:Entity) RETURN count(e) AS entities
    }
    CALL {
        // Both episodic-memory representations. The previous form matched
        // (:Artifact {memory_type: 'memory'}), of which there has never been a
        // single node — memory_type is Chroma metadata, not a Neo4j property —
        // so this counter reported 0 regardless of how many memories existed.
        // Nested + sum(), not a bare UNION ALL: a two-row CALL subquery would
        // duplicate every row of the enclosing stats query.
        CALL {
            MATCH (m:Memory) RETURN count(m) AS c
            UNION ALL
            MATCH (m:Artifact) WHERE m.filename STARTS WITH 'memory_'
            RETURN count(m) AS c
        }
        RETURN sum(c) AS memories
    }
    CALL {
        OPTIONAL MATCH (s:Source) RETURN count(s) AS sources
    }
    CALL {
        MATCH ()-[r:MENTIONS]->() RETURN count(r) AS mentions
    }
    CALL {
        OPTIONAL MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS relates_to
    }
    CALL {
        OPTIONAL MATCH ()-[r:WIKILINKS_TO]->() RETURN count(r) AS wikilinks
    }
    CALL {
        OPTIONAL MATCH ()-[r:FROM_SOURCE]->() RETURN count(r) AS from_source
    }
    CALL {
        OPTIONAL MATCH ()-[r:HAS_CONTRADICTION]->() RETURN count(r) AS has_contradiction
    }
    CALL {
        MATCH (a:Artifact)
        WITH coalesce(a.chunk_count, 0) AS c
        RETURN sum(c) AS chunks
    }
    CALL {
        OPTIONAL MATCH (s:Source)
        WITH collect(DISTINCT s.kind) AS source_kinds
        RETURN size(source_kinds) AS distinct_kinds
    }
    CALL {
        MATCH (a:Artifact)
        WITH collect(DISTINCT a.domain) AS domains
        RETURN size(domains) AS distinct_domains
    }
    CALL {
        MATCH (a:Artifact)
        WITH a.ingested_at AS ts
        WHERE ts IS NOT NULL
        RETURN min(ts) AS first_artifact_at
    }
    CALL {
        MATCH (a:Artifact)
        WHERE a.ingested_at >= $since_24h
        RETURN count(a) AS artifacts_24h
    }
    CALL {
        MATCH (a:Artifact)
        WHERE a.ingested_at >= $since_7d
        RETURN count(a) AS artifacts_7d
    }
    RETURN
        coalesce(artifacts, 0) AS artifacts,
        coalesce(entities, 0) AS entities,
        coalesce(memories, 0) AS memories,
        coalesce(sources, 0) AS sources,
        coalesce(mentions, 0) AS mentions,
        coalesce(relates_to, 0) AS relates_to,
        coalesce(wikilinks, 0) AS wikilinks,
        coalesce(from_source, 0) AS from_source,
        coalesce(has_contradiction, 0) AS has_contradiction,
        coalesce(chunks, 0) AS chunks,
        coalesce(distinct_kinds, 0) AS distinct_kinds,
        coalesce(distinct_domains, 0) AS distinct_domains,
        first_artifact_at,
        coalesce(artifacts_24h, 0) AS artifacts_24h,
        coalesce(artifacts_7d, 0) AS artifacts_7d
    """

    now = datetime.now(tz=timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()

    with driver.session() as session:
        row = session.run(cypher, since_24h=since_24h, since_7d=since_7d).single()

    if row is None:
        return _empty_snapshot(now)

    first_ts = row.get("first_artifact_at")
    age_days = 0
    if first_ts:
        try:
            first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            age_days = (now - first_dt).days
        except (ValueError, TypeError):
            age_days = 0

    return {
        "nodes": {
            "artifacts": row["artifacts"],
            "entities": row["entities"],
            "memories": row["memories"],
            "sources": row["sources"],
        },
        "edges": {
            "mentions": row["mentions"],
            "relates_to": row["relates_to"],
            "wikilinks": row["wikilinks"],
            "from_source": row["from_source"],
            "has_contradiction": row["has_contradiction"],
        },
        "chunks": row["chunks"],
        "diversity": {
            "source_kinds": row["distinct_kinds"],
            "domains": row["distinct_domains"],
        },
        "growth": {
            "artifacts_24h": row["artifacts_24h"],
            "artifacts_7d": row["artifacts_7d"],
            "first_artifact_at": first_ts,
            "corpus_age_days": age_days,
        },
        "captured_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# History — daily snapshots for sparklines
# ---------------------------------------------------------------------------


def write_stats_snapshot(driver, snapshot: dict[str, Any]) -> None:
    """Persist a daily snapshot for sparkline reads. Cron-driven from
    the scheduler. One :KnowledgeStatsSnapshot node per day; idempotent
    re-runs overwrite the same day's snapshot (using a date-keyed MERGE)."""
    import json as _json

    date_key = datetime.now(tz=timezone.utc).date().isoformat()
    payload = _json.dumps(snapshot, default=str)

    with driver.session() as session:
        session.run(
            """
            MERGE (k:KnowledgeStatsSnapshot {date: $date})
            SET k.payload = $payload,
                k.captured_at = $now
            """,
            date=date_key,
            payload=payload,
            now=datetime.now(tz=timezone.utc).isoformat(),
        )


def fetch_stats_history(driver, days: int = 30) -> list[dict[str, Any]]:
    """Return up to ``days`` daily snapshots, oldest first. The FE's
    sparkline component renders directly from this list."""
    import json as _json

    since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).date().isoformat()

    with driver.session() as session:
        rows = session.run(
            """
            MATCH (k:KnowledgeStatsSnapshot)
            WHERE k.date >= $since
            RETURN k.date AS date, k.payload AS payload
            ORDER BY k.date ASC
            """,
            since=since,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = _json.loads(r["payload"])
                payload["date"] = r["date"]
                out.append(payload)
            except (TypeError, ValueError):
                continue
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_snapshot(now: datetime) -> dict[str, Any]:
    """Shape-stable zero-state snapshot. Returned when Neo4j is
    unreachable or the database is fresh."""
    return {
        "nodes": {"artifacts": 0, "entities": 0, "memories": 0, "sources": 0},
        "edges": {
            "mentions": 0, "relates_to": 0, "wikilinks": 0,
            "from_source": 0, "has_contradiction": 0,
        },
        "chunks": 0,
        "diversity": {"source_kinds": 0, "domains": 0},
        "growth": {
            "artifacts_24h": 0, "artifacts_7d": 0,
            "first_artifact_at": None, "corpus_age_days": 0,
        },
        "captured_at": now.isoformat(),
    }
