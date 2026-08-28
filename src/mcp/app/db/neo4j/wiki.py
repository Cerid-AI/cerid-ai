# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
import os
from datetime import datetime, timezone
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.graph.wiki")

# ---------------------------------------------------------------------------
# WK2 — client/internal domains hidden from the default wiki browse view.
# ---------------------------------------------------------------------------
# These are the clearly-client data domains. The default wiki list excludes
# them; an "advanced/show internal" toggle (include_internal=True) reveals
# them. Overridable via the WIKI_HIDDEN_DOMAINS env var (comma-separated).
CLIENT_INTERNAL_DOMAINS: frozenset[str] = frozenset(
    {"boardroom_foundation", "canary_client_domain"}
)


def _hidden_domains() -> set[str]:
    """The set of primary_domain values hidden from the default browse view.

    Reads ``WIKI_HIDDEN_DOMAINS`` (comma-separated) when set; otherwise falls
    back to :data:`CLIENT_INTERNAL_DOMAINS`. An empty/whitespace-only env value
    is treated as unset (use the default), not as "hide nothing".
    """
    raw = os.environ.get("WIKI_HIDDEN_DOMAINS")
    if raw is None:
        return set(CLIENT_INTERNAL_DOMAINS)
    parsed = {part.strip() for part in raw.split(",") if part.strip()}
    return parsed or set(CLIENT_INTERNAL_DOMAINS)

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
    driver: Any,
    *,
    limit: int = 30,
    search: str | None = None,
    include_internal: bool = False,
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

    WK2: when ``include_internal`` is False (default) the client-data domains
    (:data:`CLIENT_INTERNAL_DOMAINS`, overridable via ``WIKI_HIDDEN_DOMAINS``)
    are excluded via a ``WHERE NOT e.primary_domain IN $hidden`` predicate.
    Passing ``include_internal=True`` drops the exclusion entirely.

    Returns a list of property dicts with keys:
        canonical_id, name, entity_type, mention_count,
        recent_activity_score, summary, summary_updated_at
        [match_rank only when search is non-empty]
    """
    effective_limit = min(limit, 200)
    since = _thirty_days_ago_iso()
    search_lc = (search or "").strip().lower()

    # WK2: hidden-domain predicate (default-on); dropped when include_internal.
    hidden: set[str] | None = None if include_internal else _hidden_domains()

    where_clauses: list[str] = []
    if search_lc:
        where_clauses.append(
            "(toLower(e.name) CONTAINS $search "
            "OR toLower(e.canonical_id) CONTAINS $search "
            "OR toLower(coalesce(e.summary, '')) CONTAINS $search)"
        )
    if hidden is not None:
        where_clauses.append("NOT e.primary_domain IN $hidden")
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    if search_lc:
        # rank_clause is injected as an extra WITH projection, preceded by a
        # comma so it separates cleanly from recent_activity_score.
        # Rank ordering (lower = better):
        #   0 = exact name or canonical_id match
        #   1 = name prefix match
        #   2 = name substring match
        #   3 = body-only match (summary CONTAINS, name/id did not match) — WK1
        #   4 = canonical_id-only match (name did not match, summary did not match)
        rank_with_clause = """,
                     CASE
                       WHEN toLower(e.name) = $search
                            OR toLower(e.canonical_id) = $search THEN 0
                       WHEN toLower(e.name) STARTS WITH $search THEN 1
                       WHEN toLower(e.name) CONTAINS $search THEN 2
                       WHEN toLower(coalesce(e.summary, '')) CONTAINS $search THEN 3
                       ELSE 4
                     END AS match_rank"""
        order_clause = "ORDER BY match_rank ASC, recent_activity_score DESC, mention_count DESC"
        rank_return = "match_rank,"
    else:
        # `where` already built above (may carry the hidden-domain predicate).
        rank_with_clause = ""
        order_clause = "ORDER BY recent_activity_score DESC, mention_count DESC"
        rank_return = ""

    params: dict[str, Any] = {
        "since": since,
        "limit": effective_limit,
        "search": search_lc,
    }
    if hidden is not None:
        params["hidden"] = sorted(hidden)

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
                    e.primary_domain      AS primary_domain,
                    e.top_tags            AS top_tags
                {order_clause}
                LIMIT $limit
                """,
                **params,
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
                    e.summary_edited_by   AS summary_edited_by,
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
    *,
    edited_by: str = "",
) -> None:
    """Persist a generated summary on the entity node.

    Creates ``summary`` and ``summary_updated_at`` properties.  Safe to
    call repeatedly — always overwrites.  Does NOT touch any other field.

    When ``edited_by="user"`` the write also sets ``summary_edited_by``
    to ``"user"``, marking this entity as human-edited and suppressing
    automatic re-summarisation from the stale-sweep and grew-trigger
    (except contradiction-forced refreshes which always run).

    When ``edited_by`` is empty (the default — job-generated summaries)
    ``summary_edited_by`` is cleared to ``None`` so the protection window
    does not persist after the next job refresh.

    Raises if the entity does not exist (MATCH returns 0 rows); the job
    caller is expected to validate before writing.
    """
    edited_by_val: str | None = edited_by if edited_by else None
    try:
        with driver.session() as session:
            session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})
                SET e.summary              = $summary,
                    e.summary_updated_at   = $summary_updated_at,
                    e.summary_edited_by    = $summary_edited_by,
                    // A successful summary clears any recorded skip. Without
                    // this an entity that skipped once, then succeeded via the
                    // ingest-triggered path, would keep the stale attempt
                    // stamp and stay blocked from the nightly sweep for the
                    // whole backoff window despite being healthy.
                    e.summary_attempted_at = NULL
                """,
                slug=slug,
                summary=summary,
                summary_updated_at=summary_updated_at,
                summary_edited_by=edited_by_val,
            )
    except Exception as exc:
        log_swallowed_error("wiki.write_entity_summary", exc, context={"slug": slug})
        raise


def mark_summary_attempt(driver: Any, slug: str, attempted_at: str) -> None:
    """Record that a refresh ran for this entity and wrote no summary.

    ``write_entity_summary`` stamps ``summary_updated_at``, which is what ages
    an entity out of the nightly stale sweep. Every skip path writes nothing,
    so before this existed a skipping entity stayed permanently overdue and the
    sweep re-picked it every night — ranked by ``mention_count DESC``, so the
    same high-mention entities held the whole budget indefinitely. Measured on
    2026-08-27: 77 of the 88 entities skipped that night were the same ones
    skipped the night before (88%), each costing a ``max_tokens=1024`` local
    LLM call, while 2,407 entities were sweep-eligible and roughly 2,300 of
    them never got a turn.

    This is the same defect the ``exists((:Artifact)-[:MENTIONS]->(e))`` guard
    in the sweep was added to fix — an entity that skips, writes nothing, and
    therefore re-qualifies forever — one gate further down the pipeline.

    Deliberately a SEPARATE property from ``summary_updated_at``: that one
    means "this entity has a summary as of", and the read path, freshness
    reporting and the human-edit protection window all key off it. Writing it
    on a skip would claim a summary that was never produced.

    The backoff this drives is time-based, not permanent. An entity becomes
    summarisable when new artifacts mention it, and that path
    (``subscribers.wiki_refresh.enqueue_refresh``) is triggered by ingest and
    does not consult this property at all — so genuinely improved entities
    still refresh immediately.
    """
    try:
        with driver.session() as session:
            session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})
                SET e.summary_attempted_at = $attempted_at
                """,
                slug=slug,
                attempted_at=attempted_at,
            )
    except Exception as exc:
        log_swallowed_error("wiki.mark_summary_attempt", exc, context={"slug": slug})
        raise


# ---------------------------------------------------------------------------
# get_confidence_band (computed via Cypher)
# ---------------------------------------------------------------------------


def get_confidence_band(driver: Any, slug: str) -> str:
    """Return the entity's confidence band from its computed trust_state.

    `e.trust_state` is maintained nightly by ComputeTrustStateJob from
    VerificationReport evidence. Mapping: verified→high, partial→medium,
    unverified→low, null/absent→unknown.
    """
    try:
        with driver.session() as session:
            row = session.run(
                "MATCH (e:Entity {canonical_id: $slug}) RETURN e.trust_state AS trust_state",
                slug=slug,
            ).single()
        if row is None:
            return "unknown"
        return {
            "verified": "high",
            "partial": "medium",
            "unverified": "low",
        }.get(row["trust_state"], "unknown")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("wiki.get_confidence_band", exc)
        return "unknown"


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


def get_backlinks(
    driver: Any,
    slug: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return entities that reference the entity identified by ``slug``.

    Three ``via`` sources, in precedence order (wikilink > mention > related):

    - ``wikilink``: Entity nodes whose ``summary`` field contains a wikilink to
      the target.  Wikilinks in summaries are stored in ``[[name]]`` or
      ``[[slug]]`` form by the WikiRefreshJob.  We match
      ``CONTAINS '[[' + target.name``.  This is the most intentional signal.

    - ``mention``: Entities that are co-mentioned via a shared Artifact node
      (``(:Artifact)-[:MENTIONS]->(src)`` and ``(:Artifact)-[:MENTIONS]->(target)``
      on the *same* artifact).  Captures incidental co-occurrence.

    - ``related``: Direct ``CO_MENTIONED`` edges between entities (built by
      the community-detection job from high co-mention counts).

    All three sources are fetched in a single session, de-duplicated in Python
    (higher-priority ``via`` wins when a slug appears in multiple sources), and
    capped at ``limit``.  Returns ``[]`` when ``driver`` is ``None`` or an error
    occurs.

    Returns
    -------
    list[dict] — each with: ``slug``, ``name``, ``entity_type``, ``via``.
    """
    if not driver or not slug:
        return []

    _VIA_PRIORITY: dict[str, int] = {"wikilink": 0, "mention": 1, "related": 2}

    try:
        with driver.session() as session:
            # Resolve the target entity's name so we can build the wikilink
            # CONTAINS predicate.  If the entity doesn't exist we return [].
            target_row = session.run(
                "MATCH (e:Entity {canonical_id: $slug}) RETURN e.name AS name LIMIT 1",
                slug=slug,
            ).single()
            if target_row is None:
                return []
            target_name: str = target_row["name"] or ""

            # Guard: an empty name would produce the token "[[", which
            # CONTAINS-matches every entity summary that has any wikilink at
            # all.  Skip the wikilink branch entirely in that case.
            has_valid_name = bool(target_name)
            wikilink_token = f"[[{target_name}" if has_valid_name else ""

            if has_valid_name:
                result = session.run(
                    """
                    // via:wikilink — summaries that contain a [[target_name wikilink
                    MATCH (src:Entity)
                    WHERE src.canonical_id <> $slug
                      AND src.summary IS NOT NULL
                      AND src.summary CONTAINS $wikilink_token
                    RETURN src.canonical_id AS slug,
                           src.name         AS name,
                           src.entity_type  AS entity_type,
                           'wikilink'        AS via

                    UNION

                    // via:mention — entities co-mentioned in the same artifact
                    MATCH (a:Artifact)-[:MENTIONS]->(target:Entity {canonical_id: $slug})
                    MATCH (a)-[:MENTIONS]->(src:Entity)
                    WHERE src.canonical_id <> $slug
                    RETURN src.canonical_id AS slug,
                           src.name         AS name,
                           src.entity_type  AS entity_type,
                           'mention'         AS via

                    UNION

                    // via:related — direct CO_MENTIONED edges
                    MATCH (src:Entity)-[:CO_MENTIONED]-(target:Entity {canonical_id: $slug})
                    WHERE src.canonical_id <> $slug
                    RETURN src.canonical_id AS slug,
                           src.name         AS name,
                           src.entity_type  AS entity_type,
                           'related'         AS via
                    """,
                    slug=slug,
                    wikilink_token=wikilink_token,
                )
            else:
                # No valid name — run mention + related branches only.
                result = session.run(
                    """
                    // via:mention — entities co-mentioned in the same artifact
                    MATCH (a:Artifact)-[:MENTIONS]->(target:Entity {canonical_id: $slug})
                    MATCH (a)-[:MENTIONS]->(src:Entity)
                    WHERE src.canonical_id <> $slug
                    RETURN src.canonical_id AS slug,
                           src.name         AS name,
                           src.entity_type  AS entity_type,
                           'mention'         AS via

                    UNION

                    // via:related — direct CO_MENTIONED edges
                    MATCH (src:Entity)-[:CO_MENTIONED]-(target:Entity {canonical_id: $slug})
                    WHERE src.canonical_id <> $slug
                    RETURN src.canonical_id AS slug,
                           src.name         AS name,
                           src.entity_type  AS entity_type,
                           'related'         AS via
                    """,
                    slug=slug,
                )

            # De-duplicate: keep highest-priority (lowest rank) via per slug.
            seen: dict[str, dict[str, Any]] = {}
            for row in result:
                r = dict(row)
                src_slug = r.get("slug") or ""
                if not src_slug:
                    continue
                via = r.get("via", "related")
                if src_slug not in seen:
                    seen[src_slug] = r
                else:
                    current_priority = _VIA_PRIORITY.get(seen[src_slug]["via"], 99)
                    new_priority = _VIA_PRIORITY.get(via, 99)
                    if new_priority < current_priority:
                        seen[src_slug] = r

            # Sort wikilink first, then mention, then related; cap at limit.
            ordered = sorted(
                seen.values(),
                key=lambda r: _VIA_PRIORITY.get(r.get("via", "related"), 99),
            )
            return ordered[:limit]

    except Exception as exc:
        log_swallowed_error(
            "wiki.get_backlinks", exc, context={"slug": slug}
        )
        return []


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
