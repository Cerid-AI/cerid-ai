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
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel

from app.db.neo4j import wiki as _neo4j_adapter
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.wiki_pages")

ConfidenceBand = Literal["high", "medium", "low", "unknown"]

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


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
    """A co-mentioned entity with its co-mention count."""

    canonical_id: str
    name: str
    entity_type: str
    co_mention_count: int


class SourceCitation(BaseModel):
    """A source artifact that mentions this entity, with chunk citations."""

    artifact_id: str
    title: str | None
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
        contradictions, last_updated_at, next_refresh_due, confidence_band.

    ``contradictions`` is a raw list of dicts (one per ContradictionFinding)
    so the router can serialise them without a hard import of
    ContradictionFinding here. The contradiction service is imported lazily.

    Phase K2.2 adds ``episodic_memories`` — user-recorded memories that
    mention this entity (decay-aware, capped at 5).
    """

    slug: str
    name: str
    entity_type: str
    summary: str | None = None
    related_entities: list[RelatedEntity] = []
    source_artifacts: list[SourceCitation] = []
    contradictions: list[dict[str, Any]] = []
    external_references: list[ExternalReference] = []
    episodic_memories: list[EpisodicMemoryItem] = []
    last_updated_at: str | None = None
    next_refresh_due: str | None = None
    confidence_band: ConfidenceBand = "unknown"


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
            result.append(
                EntitySummary(
                    canonical_id=r.get("canonical_id", ""),
                    name=r.get("name", ""),
                    entity_type=r.get("entity_type", "OTHER"),
                    mention_count=int(r.get("mention_count", 0)),
                    recent_activity_score=int(r.get("recent_activity_score", 0)),
                    summary=r.get("summary"),
                    summary_updated_at=r.get("summary_updated_at"),
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

    # --- 4. next_refresh_due -------------------------------------------------
    summary_updated_at: str | None = raw.get("summary_updated_at")
    next_refresh_due = _compute_next_refresh(summary_updated_at)

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
        )
        for r in raw.get("related", [])
    ]

    source_artifacts = [
        SourceCitation(
            artifact_id=s.get("artifact_id", ""),
            title=s.get("title"),
            chunk_ids=s.get("chunk_ids", []),
            confidence=float(s.get("confidence") or 0.0),
            updated_at=s.get("updated_at"),
        )
        for s in raw.get("source_artifacts", [])
    ]

    return WikiEntityPage(
        slug=slug,
        name=raw.get("name", ""),
        entity_type=raw.get("entity_type", "OTHER"),
        summary=raw.get("summary"),
        related_entities=related,
        source_artifacts=source_artifacts,
        contradictions=contradictions,
        external_references=external_references,
        episodic_memories=episodic_memories,
        last_updated_at=raw.get("updated_at"),
        next_refresh_due=next_refresh_due,
        confidence_band=confidence_band,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
