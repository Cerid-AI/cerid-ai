# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entity wiki page service (Phase W.1).

Assembles a full wiki entity page from:
- Neo4j entity node + co-mention edges + source artifacts
- Cached ``summary`` field on the entity node (written by WikiRefreshJob)
- Contradiction log (filtered by entity_slug via app.services.contradiction_log)

Layering
--------
* Lives in ``app/services/`` — may import from ``core.*`` and
  ``app/db/neo4j/*`` and ``app/services/contradiction_log``.
* MUST NOT be imported by anything in ``core/`` (import-linter contract).
* Neo4j driver obtained lazily at call time via ``app.deps.get_neo4j``
  so the module is importable without a live database (unit tests mock
  the adapter layer instead).
* All sync Neo4j calls are wrapped in ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel

from app.db.neo4j import wiki as _neo4j_adapter
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.wiki_pages")

ConfidenceBand = Literal["high", "medium", "low", "unknown"]
RefreshStatus = Literal["idle", "due", "running"]

# Redis key constants (must match processor_queue.py)
_PROC_RUNNING_KEY = "cerid:proc:running"
_PROC_JOB_KEY_FMT = "cerid:proc:job:{job_id}"

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


def _parse_top_tags(raw: Any) -> list[str] | None:
    """Parse an entity's top_tags (JSON string from Neo4j, or already a list)
    into a list of tag strings. Returns None on absent/malformed input."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    tags = [str(t) for t in parsed if t]
    return tags or None


class ExternalReference(BaseModel):
    """A single reference fetched from an external public API (Phase API.3).

    Structural invariant
    --------------------
    External references are ALWAYS visually and structurally distinct from
    internal-corpus claims.  They live in a dedicated ``external_references``
    field on ``WikiEntityPage`` and are rendered in their own UI section.
    They must never be blended into the ``source_artifacts`` list.

    Attributes
    ----------
    source:
        Stable adapter slug (e.g. ``"wikipedia"``).
    source_display:
        Human-readable label (e.g. ``"Wikipedia"``).
    title:
        The canonical title of the resource at the external source.
    snippet:
        Short excerpt from the external resource — 200 chars max.
    url:
        Link to the resource at the external source, or ``None`` when the
        adapter does not expose a stable page URL.
    fetched_at:
        ISO-8601 timestamp of when this reference was fetched.
    metadata:
        Adapter-specific extra fields (stars, subjects, coordinates, etc.).
        Not shown in the main UI — disclosed in a popover / detail view.
    """

    source: str
    source_display: str
    title: str
    snippet: str
    url: str | None = None
    fetched_at: str
    metadata: dict[str, Any] = {}


class RelatedEntity(BaseModel):
    """A co-mentioned entity with its co-mention count.

    Amendment #1+#2: ``has_summary`` and ``one_liner`` are projected
    directly from the related entity node so the frontend can render
    three-state wikilinks (normal / stub / no-link) and HoverCard
    previews without an extra fetch.
    """

    canonical_id: str
    name: str
    entity_type: str
    co_mention_count: int
    # Amendment #1: does the related entity have a summary?
    has_summary: bool = False
    # Amendment #2: first 160 chars of the related entity's summary (None
    # when has_summary is False).  Same derivation as /wiki/index one_liner.
    one_liner: str | None = None


class SourceCitation(BaseModel):
    """A source artifact that mentions this entity, with chunk citations.

    ``display_title`` is the human-readable label: ``coalesce(a.title, a.filename)``.
    ``title`` is retained for API compatibility but may be ``None`` for artifacts
    ingested before the title field was populated.  Callers should prefer
    ``display_title`` over ``title``.
    """

    artifact_id: str
    title: str | None
    # Resolved display label: coalesce(a.title, a.filename); always non-null when
    # the artifact has a filename (i.e. was ingested from a real file).
    display_title: str | None = None
    filename: str | None = None
    domain: str | None = None
    source_type: str | None = None
    chunk_ids: list[str]
    confidence: float
    updated_at: str | None


class EntitySummary(BaseModel):
    """Lightweight summary row for the entity list endpoint."""

    canonical_id: str
    name: str
    entity_type: str
    mention_count: int
    recent_activity_score: int
    summary: str | None = None
    summary_updated_at: str | None = None
    primary_domain: str | None = None
    # Top controlled-vocabulary tags (Slice 6.3) — salience-ordered, capped at
    # 5; null until DeriveDomainsJob runs. Surfaces tag sort/filter in lists.
    top_tags: list[str] | None = None
    # Present only when a search term was supplied (list_top_entities
    # conditional CASE); absent on the no-q browse path.
    match_rank: int | None = None


class EpisodicMemoryItem(BaseModel):
    """A memory the user has recorded that mentions this entity (Phase K2.2).

    Surfaced on entity wiki pages so the user sees "what *we* said about X"
    alongside the corpus-derived prose summary. Memories are kept distinct
    from ``source_artifacts`` (which represent ingested documents) because
    their provenance and decay characteristics differ.
    """

    memory_id: str
    memory_type: str
    summary: str
    valid_from: str | None = None
    access_count: int = 0


class WikiEntityPage(BaseModel):
    """Full wiki page for a single entity.

    Assembles the documented W.1 response shape:
        slug, name, summary, related_entities, source_artifacts,
        contradictions, last_updated_at, next_refresh_due, confidence_band,
        refresh_status.

    ``contradictions`` is a raw list of dicts (one per ContradictionFinding)
    so the router can serialise them without a hard import of
    ContradictionFinding here. The contradiction service is imported lazily.

    Phase K2.2 adds ``episodic_memories`` — user-recorded memories that
    mention this entity (decay-aware, capped at 5).

    ``refresh_status`` reflects actual job state:
        "running" — a WikiRefreshJob for this entity is currently executing
        "due"     — no running job but next_refresh_due is in the past
        "idle"    — summary is fresh (next_refresh_due is in the future)
    """

    slug: str
    name: str
    entity_type: str
    # Identity-capsule fields (Gazetteer): the same community hue and trust
    # vocabulary the entity wears in the graph views.
    community_id: str | None = None
    community_label: str | None = None
    mention_count: int = 0
    # Domain backbone fields (Cycle 1; Slice 6 adds salience + top_tags)
    primary_domain: str | None = None
    domain_mix: dict[str, int] | None = None
    # Salience-ordered domain weights (Slice 6.1) and top controlled-vocabulary
    # tags (Slice 6.3) — both derived by DeriveDomainsJob, null until it runs.
    domain_salience: dict[str, float] | None = None
    top_tags: list[str] | None = None
    primary_subcategory: str | None = None
    summary: str | None = None
    related_entities: list[RelatedEntity] = []
    source_artifacts: list[SourceCitation] = []
    contradictions: list[dict[str, Any]] = []
    external_references: list[ExternalReference] = []
    episodic_memories: list[EpisodicMemoryItem] = []
    last_updated_at: str | None = None
    next_refresh_due: str | None = None
    confidence_band: ConfidenceBand = "unknown"
    # Tri-state driven by actual job state, not timestamp heuristics.
    refresh_status: RefreshStatus = "idle"


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def list_entities(
    neo4j_driver: Any, *, limit: int = 30, search: str | None = None
) -> list[EntitySummary]:
    """Return up to ``limit`` entity summaries ordered by recent activity.

    When ``search`` is given it filters by name/canonical_id server-side
    (before the limit), so the palette can search the full corpus.

    Wraps the Neo4j adapter call in ``asyncio.to_thread`` so the sync
    driver does not block the event loop.
    """
    try:
        rows = await asyncio.to_thread(
            _neo4j_adapter.list_top_entities, neo4j_driver, limit=limit, search=search
        )
    except Exception as exc:
        log_swallowed_error("wiki.list_entities", exc)
        raise

    result = []
    for r in rows:
        try:
            # match_rank is only present when a search term was given;
            # the no-q browse path returns rows without this key.
            raw_rank = r.get("match_rank")
            result.append(
                EntitySummary(
                    canonical_id=r.get("canonical_id", ""),
                    name=r.get("name", ""),
                    entity_type=r.get("entity_type", "OTHER"),
                    mention_count=int(r.get("mention_count", 0)),
                    recent_activity_score=int(r.get("recent_activity_score", 0)),
                    summary=r.get("summary"),
                    summary_updated_at=r.get("summary_updated_at"),
                    primary_domain=r.get("primary_domain"),
                    top_tags=_parse_top_tags(r.get("top_tags")),
                    match_rank=int(raw_rank) if raw_rank is not None else None,
                )
            )
        except Exception as exc:
            log_swallowed_error("wiki.list_entities.row_parse", exc, context={"row": r})
            # Skip malformed rows rather than aborting the whole list
            continue
    return result


async def get_entity_page(neo4j_driver: Any, slug: str) -> WikiEntityPage | None:
    """Assemble the full wiki page for ``slug``.

    Returns ``None`` when no entity with that canonical_id exists.

    Assembly steps:
    1. Fetch entity + related + source_artifacts from Neo4j.
    2. Fetch confidence band via a separate Cypher query.
    3. Fetch contradiction log filtered by entity_slug.
    4. Compute next_refresh_due (summary_updated_at + 24 h, or "now" if
       no summary yet).
    """
    # --- 1. Core entity data -----------------------------------------------
    try:
        raw = await asyncio.to_thread(_neo4j_adapter.get_entity, neo4j_driver, slug)
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_page.fetch_entity", exc, context={"slug": slug})
        raise

    if raw is None:
        return None

    # --- 2. Confidence band --------------------------------------------------
    try:
        _band_raw: str = await asyncio.to_thread(
            _neo4j_adapter.get_confidence_band, neo4j_driver, slug
        )
        confidence_band: ConfidenceBand = _band_raw  # type: ignore[assignment]
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_page.confidence_band", exc, context={"slug": slug})
        confidence_band = "unknown"

    # --- 3. Contradiction log (entity-scoped) --------------------------------
    contradictions: list[dict[str, Any]] = []
    try:
        from app.services.contradiction_log import list_recent as _list_contradictions

        findings = await _list_contradictions(entity_slug=slug, limit=50)
        contradictions = [f.model_dump() for f in findings]
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_page.contradictions", exc, context={"slug": slug})
        # Non-fatal: contradictions section stays empty

    # --- 4. next_refresh_due + refresh_status --------------------------------
    summary_updated_at: str | None = raw.get("summary_updated_at")
    next_refresh_due = _compute_next_refresh(summary_updated_at)
    refresh_status: RefreshStatus = await asyncio.to_thread(
        _get_refresh_status, slug, next_refresh_due
    )

    # --- 4a. Episodic memories (Phase K2.2) -----------------------------------
    episodic_memories: list[EpisodicMemoryItem] = []
    try:
        mem_raw = await asyncio.to_thread(
            _neo4j_adapter.get_memories_for_entity, neo4j_driver, slug, limit=5,
        )
        episodic_memories = [
            EpisodicMemoryItem(
                memory_id=r.get("memory_id", ""),
                memory_type=r.get("memory_type", "general"),
                summary=r.get("summary", ""),
                valid_from=r.get("valid_from"),
                access_count=int(r.get("access_count", 0)),
            )
            for r in mem_raw
            if r.get("memory_id")
        ]
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_page.episodic_memories", exc, context={"slug": slug})
        # Non-fatal: episodic memories section stays empty

    # --- 4b. External references (written by WikiRefreshJob) -----------------
    external_references: list[ExternalReference] = []
    try:
        ext_raw = await asyncio.to_thread(
            _neo4j_adapter.get_external_references, neo4j_driver, slug
        )
        external_references = [
            ExternalReference(
                source=r.get("source", ""),
                source_display=r.get("source_display", ""),
                title=r.get("title", ""),
                snippet=r.get("snippet", ""),
                url=r.get("url"),
                fetched_at=r.get("fetched_at", ""),
                metadata=r.get("metadata", {}),
            )
            for r in ext_raw
        ]
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_page.external_references", exc, context={"slug": slug})
        # Non-fatal: external references section stays empty

    # --- 5. Assemble ---------------------------------------------------------
    related = [
        RelatedEntity(
            canonical_id=r.get("canonical_id", ""),
            name=r.get("name", ""),
            entity_type=r.get("entity_type", "OTHER"),
            co_mention_count=int(r.get("co_mention_count", 0)),
            has_summary=bool(r.get("has_summary", False)),
            one_liner=r.get("one_liner") or None,
        )
        for r in raw.get("related", [])
    ]

    source_artifacts = [
        SourceCitation(
            artifact_id=s.get("artifact_id", ""),
            title=s.get("title"),
            display_title=s.get("title") or s.get("filename") or None,
            filename=s.get("filename"),
            domain=s.get("domain"),
            source_type=s.get("source_type"),
            chunk_ids=s.get("chunk_ids", []),
            confidence=float(s.get("confidence") or 0.0),
            updated_at=s.get("updated_at"),
        )
        for s in raw.get("source_artifacts", [])
    ]

    community_label = await asyncio.to_thread(
        _resolve_community_label, neo4j_driver, raw.get("community_id")
    )

    # Domain backbone fields — parse domain_mix JSON string → dict
    primary_domain: str | None = raw.get("primary_domain")
    raw_domain_mix = raw.get("domain_mix")
    domain_mix: dict[str, int] | None = None
    if raw_domain_mix:
        try:
            parsed = json.loads(raw_domain_mix) if isinstance(raw_domain_mix, str) else raw_domain_mix
            if isinstance(parsed, dict):
                domain_mix = {str(k): int(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            domain_mix = None

    raw_domain_salience = raw.get("domain_salience")
    domain_salience: dict[str, float] | None = None
    if raw_domain_salience:
        try:
            parsed_sal = (
                json.loads(raw_domain_salience)
                if isinstance(raw_domain_salience, str)
                else raw_domain_salience
            )
            if isinstance(parsed_sal, dict):
                # Preserve the salience-desc order DeriveDomainsJob persisted.
                domain_salience = {str(k): float(v) for k, v in parsed_sal.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            domain_salience = None

    top_tags: list[str] | None = _parse_top_tags(raw.get("top_tags"))

    primary_subcategory: str | None = raw.get("primary_subcategory")

    return WikiEntityPage(
        slug=slug,
        name=raw.get("name", ""),
        entity_type=raw.get("entity_type", "OTHER"),
        community_id=raw.get("community_id"),
        community_label=community_label,
        mention_count=int(raw.get("mention_count") or 0),
        primary_domain=primary_domain,
        domain_mix=domain_mix,
        domain_salience=domain_salience,
        top_tags=top_tags,
        primary_subcategory=primary_subcategory,
        summary=raw.get("summary"),
        related_entities=related,
        source_artifacts=source_artifacts,
        contradictions=contradictions,
        external_references=external_references,
        episodic_memories=episodic_memories,
        last_updated_at=raw.get("updated_at"),
        next_refresh_due=next_refresh_due,
        confidence_band=confidence_band,
        refresh_status=refresh_status,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_community_label(neo4j_driver: Any, community_id: Any) -> str | None:
    """Human label for the entity's Leiden community.

    Primary source: the cartographic map artifact (same as /graph/map and
    /graph/timeline/strata — payload shape {"communities": [...]}, only
    hull-worthy communities present). Fallback: top-hub entity name from
    Neo4j so small communities still read humanely. Fail-open: any miss
    returns None and the capsule shows the raw id.
    """
    if not community_id:
        return None
    cid = str(community_id)
    try:
        import json as _json

        from app.deps import get_redis

        redis = get_redis()
        if redis is not None:
            raw = redis.get("cerid:graph:map:communities")
            if raw:
                payload = _json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                communities = payload.get("communities", []) if isinstance(payload, dict) else payload
                for c in communities:
                    if str(c.get("id")) == cid:
                        label = c.get("label")
                        if label:
                            return str(label)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "wiki.get_entity_page.community_label_artifact", exc,
            context={"community_id": cid},
        )
    try:
        with neo4j_driver.session() as session:
            row = session.run(
                "MATCH (e:Entity {community_id: $cid}) "
                "RETURN e.name AS name ORDER BY e.mention_count DESC LIMIT 1",
                cid=cid,
            ).single()
            if row and row.get("name"):
                return str(row["name"])
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "wiki.get_entity_page.community_label_hub", exc,
            context={"community_id": cid},
        )
    return None


def _compute_next_refresh(summary_updated_at: str | None) -> str:
    """Return an ISO timestamp 24 h after summary_updated_at.

    Falls back to the current time if summary_updated_at is absent or
    unparseable (meaning a refresh is overdue).
    """
    if summary_updated_at:
        try:
            dt = datetime.fromisoformat(summary_updated_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt + timedelta(hours=24)).isoformat()
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).isoformat()


def _get_refresh_status(slug: str, next_refresh_due: str) -> RefreshStatus:
    """Return the tri-state refresh status for an entity.

    Checks the processor's running-job Redis set (``cerid:proc:running``)
    for a live ``wiki_refresh`` job targeting this entity slug.  Failing
    that, compares ``next_refresh_due`` to now.

    States
    ------
    "running" — a WikiRefreshJob for this entity is currently executing.
    "due"     — no running job; next_refresh_due is in the past (stale).
    "idle"    — summary is fresh (next_refresh_due is in the future).

    Fail-open: any Redis error returns "due" when next_refresh_due is
    already past, else "idle" — never "running" on an uncertain read.
    """
    try:
        from app.deps import get_redis  # noqa: PLC0415

        redis = get_redis()
        if redis is not None:
            running_ids = redis.smembers(_PROC_RUNNING_KEY)
            for job_id_raw in running_ids:
                job_id = (
                    job_id_raw.decode() if isinstance(job_id_raw, bytes) else str(job_id_raw)
                )
                job_key = _PROC_JOB_KEY_FMT.format(job_id=job_id)
                job_type = redis.hget(job_key, "job_type")
                if job_type:
                    jt = job_type.decode() if isinstance(job_type, bytes) else str(job_type)
                    if jt == "wiki_refresh":
                        payload_raw = redis.hget(job_key, "payload")
                        if payload_raw:
                            try:
                                payload_str = (
                                    payload_raw.decode()
                                    if isinstance(payload_raw, bytes)
                                    else str(payload_raw)
                                )
                                payload = json.loads(payload_str)
                                if payload.get("entity_slug") == slug:
                                    return "running"
                            except (json.JSONDecodeError, TypeError):
                                pass
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "wiki.get_refresh_status", exc, context={"slug": slug}
        )

    # No running job found — fall back to schedule comparison.
    try:
        due_dt = datetime.fromisoformat(next_refresh_due)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if due_dt <= datetime.now(timezone.utc):
            return "due"
    except (ValueError, TypeError):
        return "due"
    return "idle"
