# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j persistence for the entity wiki layer (Phase W.1 + API.3).

Schema additions on top of the existing (:Entity) node
(from app.db.neo4j.entity):

    (:Entity {
        canonical_id, name, entity_type, created_at, updated_at,
        mention_count,
        summary: str | None,          # populated by WikiRefreshJob
        summary_updated_at: str | None,
    })

Phase API.3 adds ExternalReference nodes:

    (:Entity)-[:ENRICHED_FROM {source, fetched_at}]->(:ExternalReference {
        source, source_display, title, snippet, url, fetched_at,
        metadata_json
    })

``write_external_references`` is called by WikiRefreshJob after enrichment.
``get_external_references`` is called by the wiki_pages service at read time.

Callers: :mod:`app.services.wiki_pages` only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.graph.wiki")

# ---------------------------------------------------------------------------
# Value objects (lightweight dicts; Pydantic models live in the service)
# ---------------------------------------------------------------------------

# Keys returned by list_top_entities:
#   canonical_id, name, entity_type, mention_count, recent_activity_score
#   summary, summary_updated_at

# Keys returned by get_entity:
#   plus: related (list of dicts), source_artifacts (list of dicts)

_THIRTY_DAYS_ISO = None  # computed lazily


# ---------------------------------------------------------------------------
# Phase K2.2 — episodic memory for an entity
# ---------------------------------------------------------------------------


def get_memories_for_entity(
    driver: Any,
    entity_slug: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch episodic memories that mention ``entity_slug``.

    Returns up to ``limit`` rows ordered by recency (the conversation
    memories with the latest ``valid_from``). Each row carries the
    fields the wiki page renders directly — no further joins needed.

    Filters out archived memories so the wiki page reflects the live
    state. The decay-adjusted score is computed by the service layer
    on top of the raw rows; this query stays cheap so the page render
    keeps its ~10 ms budget.
    """
    if not driver or not entity_slug:
        return []

    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (m:Artifact)-[:MENTIONS]->(e:Entity {canonical_id: $slug})
                WHERE coalesce(m.archived, false) = false
                  AND m.memory_type IS NOT NULL
                RETURN m.id AS memory_id,
                       m.memory_type AS memory_type,
                       m.summary AS summary,
                       m.valid_from AS valid_from,
                       coalesce(m.access_count, 0) AS access_count
                ORDER BY coalesce(m.valid_from, m.created_at) DESC
                LIMIT $lim
                """,
                slug=entity_slug,
                lim=limit,
            )
            return [
                {
                    "memory_id": row["memory_id"],
                    "memory_type": row["memory_type"],
                    "summary": row["summary"] or "",
                    "valid_from": row["valid_from"],
                    "access_count": int(row["access_count"]),
                }
                for row in result
            ]
    except Exception as exc:
        log_swallowed_error(
            "wiki.get_memories_for_entity", exc, context={"slug": entity_slug}
        )
        return []



def _thirty_days_ago_iso() -> str:
    """ISO string for now-minus-30-days. Computed once per process call."""
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()


# ---------------------------------------------------------------------------
# list_top_entities
# ---------------------------------------------------------------------------


def list_top_entities(
    driver: Any, *, limit: int = 30, search: str | None = None
) -> list[dict[str, Any]]:
    """Return up to ``limit`` entities ordered by recent activity score.

    ``recent_activity_score`` is the count of distinct Artifact nodes that
    mention this entity and were updated within the last 30 days. Entities
    with no recent activity sort last (score 0).

    When ``search`` is given, the name/canonical_id filter runs in Cypher
    *before* the LIMIT, so the match spans the whole entity set — not just
    the first ``limit`` rows the client happened to fetch (F5).

    When ``search`` is given, a conditional ``match_rank`` is computed:
        0 = exact name or canonical_id match
        1 = name prefix match
        2 = name substring match
        3 = canonical_id-only match (name did not match)
    This rank is absent (no WITH-stage CASE) on the no-search browse path
    so the browse ordering stays byte-identical.

    Returns a list of property dicts with keys:
        canonical_id, name, entity_type, mention_count,
        recent_activity_score, summary, summary_updated_at
        [match_rank only when search is non-empty]
    """
    effective_limit = min(limit, 200)
    since = _thirty_days_ago_iso()
    search_lc = (search or "").strip().lower()

    if search_lc:
        where = (
            "WHERE toLower(e.name) CONTAINS $search "
            "OR toLower(e.canonical_id) CONTAINS $search"
        )
        # rank_clause is injected as an extra WITH projection, preceded by a
        # comma so it separates cleanly from recent_activity_score.
        rank_with_clause = """,
                     CASE
                       WHEN toLower(e.name) = $search
                            OR toLower(e.canonical_id) = $search THEN 0
                       WHEN toLower(e.name) STARTS WITH $search THEN 1
                       WHEN toLower(e.name) CONTAINS $search THEN 2
                       ELSE 3
                     END AS match_rank"""
        order_clause = "ORDER BY match_rank ASC, recent_activity_score DESC, mention_count DESC"
        rank_return = "match_rank,"
    else:
        where = ""
        rank_with_clause = ""
        order_clause = "ORDER BY recent_activity_score DESC, mention_count DESC"
        rank_return = ""

    try:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (e:Entity)
                {where}
                OPTIONAL MATCH (a:Artifact)-[:MENTIONS]->(e)
                  WHERE a.updated_at >= $since
                WITH e,
                     count(DISTINCT a) AS recent_activity_score{rank_with_clause}
                RETURN
                    e.canonical_id        AS canonical_id,
                    e.name                AS name,
                    e.entity_type         AS entity_type,
                    coalesce(e.mention_count, 0) AS mention_count,
                    recent_activity_score,
                    {rank_return}
                    e.summary             AS summary,
                    e.summary_updated_at  AS summary_updated_at,
                    e.primary_domain      AS primary_domain
                {order_clause}
                LIMIT $limit
                """,
                since=since,
                limit=effective_limit,
                search=search_lc,
            )
            return [dict(r) for r in result]
    except Exception as exc:
        log_swallowed_error("wiki.list_top_entities", exc)
        raise


# ---------------------------------------------------------------------------
# get_entity
# ---------------------------------------------------------------------------


def get_entity(driver: Any, slug: str) -> dict[str, Any] | None:
    """Fetch the full entity node plus related entities and source artifacts.

    Returns a dict with:
        canonical_id, name, entity_type, mention_count,
        summary, summary_updated_at,
        related: list[{canonical_id, name, entity_type, co_mention_count}],
        source_artifacts: list[{artifact_id, title, chunk_ids: list[str],
                                confidence, updated_at}]

    Returns ``None`` when no entity with the given canonical_id exists.
    """
    try:
        with driver.session() as session:
            # --- 1. Core entity node ----------------------------------------
            entity_result = session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})
                RETURN
                    e.canonical_id        AS canonical_id,
                    e.name                AS name,
                    e.entity_type         AS entity_type,
                    coalesce(e.mention_count, 0) AS mention_count,
                    e.community_id        AS community_id,
                    e.summary             AS summary,
                    e.summary_updated_at  AS summary_updated_at,
                    e.updated_at          AS updated_at,
                    e.primary_domain      AS primary_domain,
                    e.domain_mix          AS domain_mix,
                    e.domain_salience     AS domain_salience,
                    e.top_tags            AS top_tags,
                    e.primary_subcategory AS primary_subcategory
                LIMIT 1
                """,
                slug=slug,
            )
            entity_row = entity_result.single()
            if entity_row is None:
                return None
            entity = dict(entity_row)

            # --- 2. Related entities (top-10 co-mentions) --------------------
            # Amendment #1+#2: project has_summary and one_liner per related
            # entity so frontend can render three-state wikilinks and HoverCard
            # previews without a second fetch.  The projection already touches
            # the other nodes so the extra RETURN aliases add negligible cost.
            related_result = session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})
                MATCH (a:Artifact)-[:MENTIONS]->(e)
                MATCH (a)-[:MENTIONS]->(other:Entity)
                  WHERE other.canonical_id <> $slug
                WITH other, count(DISTINCT a) AS co_mention_count
                ORDER BY co_mention_count DESC
                LIMIT 10
                RETURN
                    other.canonical_id  AS canonical_id,
                    other.name          AS name,
                    other.entity_type   AS entity_type,
                    co_mention_count,
                    other.summary IS NOT NULL          AS has_summary,
                    left(other.summary, 160)           AS one_liner
                """,
                slug=slug,
            )
            entity["related"] = [dict(r) for r in related_result]

            # --- 3. Source artifacts (with chunk-hash citations) --------------
            artifacts_result = session.run(
                """
                MATCH (a:Artifact)-[m:MENTIONS]->(e:Entity {canonical_id: $slug})
                RETURN
                    a.id                              AS artifact_id,
                    a.title                           AS title,
                    coalesce(a.title, a.filename)     AS display_title,
                    a.filename                        AS filename,
                    a.domain                          AS domain,
                    a.source_type                     AS source_type,
                    m.chunk_ids                       AS chunk_ids_json,
                    m.confidence                      AS confidence,
                    a.updated_at                      AS updated_at
                ORDER BY a.updated_at DESC
                LIMIT 50
                """,
                slug=slug,
            )
            source_artifacts = []
            for row in artifacts_result:
                raw = dict(row)
                chunk_ids_raw = raw.pop("chunk_ids_json", None) or "[]"
                try:
                    chunk_ids = json.loads(chunk_ids_raw) if isinstance(chunk_ids_raw, str) else list(chunk_ids_raw)
                except (json.JSONDecodeError, TypeError):
                    chunk_ids = []
                raw["chunk_ids"] = chunk_ids
                source_artifacts.append(raw)
            entity["source_artifacts"] = source_artifacts

        return entity

    except Exception as exc:
        log_swallowed_error("wiki.get_entity", exc, context={"slug": slug})
        raise


# ---------------------------------------------------------------------------
# write_entity_summary (called by WikiRefreshJob only)
# ---------------------------------------------------------------------------


def write_entity_summary(
    driver: Any,
    slug: str,
    summary: str,
    summary_updated_at: str,
) -> None:
    """Persist a generated summary on the entity node.

    Creates ``summary`` and ``summary_updated_at`` properties.  Safe to
    call repeatedly — always overwrites.  Does NOT touch any other field.

    Raises if the entity does not exist (MATCH returns 0 rows); the job
    caller is expected to validate before writing.
    """
    try:
        with driver.session() as session:
            session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})
                SET e.summary            = $summary,
                    e.summary_updated_at = $summary_updated_at
                """,
                slug=slug,
                summary=summary,
                summary_updated_at=summary_updated_at,
            )
    except Exception as exc:
        log_swallowed_error("wiki.write_entity_summary", exc, context={"slug": slug})
        raise


# ---------------------------------------------------------------------------
# get_confidence_band (computed via Cypher)
# ---------------------------------------------------------------------------


def get_confidence_band(driver: Any, slug: str) -> str:
    """Return the confidence band for an entity based on its claims.

    Band logic:
        >= 80% verified  → "high"
        50–79% verified  → "medium"
        < 50% verified   → "low"
        no claims        → "unknown"

    A (:Claim) node is considered associated with the entity when it has
    an ``entity_slug`` property matching ``slug``.
    """
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (c:Claim)
                  WHERE c.entity_slug = $slug
                WITH
                    count(c) AS total,
                    count(CASE WHEN c.status = 'verified' THEN 1 END) AS verified_count
                RETURN total, verified_count
                """,
                slug=slug,
            )
            row = result.single()
            if row is None:
                return "unknown"
            total = int(row["total"])
            verified = int(row["verified_count"])

        if total == 0:
            return "unknown"
        ratio = verified / total
        if ratio >= 0.80:
            return "high"
        if ratio >= 0.50:
            return "medium"
        return "low"

    except Exception as exc:
        log_swallowed_error("wiki.get_confidence_band", exc, context={"slug": slug})
        raise


# ---------------------------------------------------------------------------
# write_external_references (called by WikiRefreshJob only)
# ---------------------------------------------------------------------------


def write_external_references(
    driver: Any,
    entity_slug: str,
    refs: list[dict[str, Any]],
) -> None:
    """Persist external-API enrichment results for an entity (Phase API.3).

    Each reference is stored as an ``(:ExternalReference)`` node connected
    to the entity via a ``[:ENRICHED_FROM]`` relationship.

    Existing references for the same source are replaced (MERGE + SET) so
    repeated enrichment runs do not accumulate stale duplicates.

    Parameters
    ----------
    driver:
        Live Neo4j driver.
    entity_slug:
        Canonical ID of the entity (must exist in the graph).
    refs:
        List of ExternalReference dicts (Pydantic ``.model_dump()`` output).

    Schema
    ------
    (:Entity {canonical_id})-[:ENRICHED_FROM {source, fetched_at}]->
        (:ExternalReference {
            source, source_display, title, snippet, url, fetched_at,
            metadata_json   # JSON string of the metadata dict
        })
    """
    if not refs:
        return
    try:
        with driver.session() as session:
            for ref in refs:
                metadata_json = json.dumps(ref.get("metadata") or {})
                session.run(
                    """
                    MATCH (e:Entity {canonical_id: $slug})
                    MERGE (r:ExternalReference {source: $source, entity_slug: $slug})
                    SET
                        r.source_display  = $source_display,
                        r.title           = $title,
                        r.snippet         = $snippet,
                        r.url             = $url,
                        r.fetched_at      = $fetched_at,
                        r.metadata_json   = $metadata_json
                    MERGE (e)-[rel:ENRICHED_FROM {source: $source}]->(r)
                    SET rel.fetched_at = $fetched_at
                    """,
                    slug=entity_slug,
                    source=ref.get("source", ""),
                    source_display=ref.get("source_display", ""),
                    title=ref.get("title", ""),
                    snippet=ref.get("snippet", ""),
                    url=ref.get("url"),
                    fetched_at=ref.get("fetched_at", ""),
                    metadata_json=metadata_json,
                )
    except Exception as exc:
        log_swallowed_error(
            "wiki.write_external_references", exc, context={"slug": entity_slug}
        )
        raise


# ---------------------------------------------------------------------------
# get_external_references (called by wiki_pages service)
# ---------------------------------------------------------------------------


def get_external_references(driver: Any, entity_slug: str) -> list[dict[str, Any]]:
    """Return all external references for an entity, ordered by source.

    Parameters
    ----------
    driver:
        Live Neo4j driver.
    entity_slug:
        Canonical ID of the entity.

    Returns
    -------
    list[dict] — each with: ``source``, ``source_display``, ``title``,
        ``snippet``, ``url``, ``fetched_at``, ``metadata``.
    """
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})-[:ENRICHED_FROM]->(r:ExternalReference)
                RETURN
                    r.source         AS source,
                    r.source_display AS source_display,
                    r.title          AS title,
                    r.snippet        AS snippet,
                    r.url            AS url,
                    r.fetched_at     AS fetched_at,
                    r.metadata_json  AS metadata_json
                ORDER BY r.source
                """,
                slug=entity_slug,
            )
            rows = []
            for row in result:
                raw = dict(row)
                metadata_json_str = raw.pop("metadata_json", None) or "{}"
                try:
                    metadata = json.loads(metadata_json_str) if isinstance(metadata_json_str, str) else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
                raw["metadata"] = metadata
                rows.append(raw)
            return rows
    except Exception as exc:
        log_swallowed_error(
            "wiki.get_external_references", exc, context={"slug": entity_slug}
        )
        raise
