# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entity wiki API endpoints (Phase W.1) + two-way vault write (RAG C3.3).

Routes
------
GET /wiki/entities?limit=30
    Paginated list of entity summaries, ordered by recent activity.

GET /wiki/entities/{slug}
    Full WikiEntityPage for a single entity. Returns 404 if not found.

POST /wiki/write_note
    Write a markdown note to a registered vault and re-ingest it as an
    Artifact tagged ``source_type='cerid-synthesis'``.  Two-way loop
    between Cerid's outputs and the user's local vault (RAG C3.3).

Path collision note: the contradictions router already mounts routes under
``/wiki/contradictions/*``.  Entity pages live at ``/wiki/entities/*``;
the writeback endpoint lives at ``/wiki/write_note`` — no overlap.  All
three share the ``prefix="/wiki"`` convention so the OpenAPI tags render
under the same "wiki" group.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db.neo4j.knowledge_log import append_log_entry
from app.db.neo4j.wiki import get_backlinks, write_entity_summary
from app.processor.subscribers.wiki_refresh import enqueue_refresh
from app.services.wiki_pages import (
    EntitySummary,
    WikiEntityPage,
    compute_completeness,
    get_entity_page,
    list_entities,
)
from core.utils.swallowed import log_swallowed_error


# --- Response models (generated: single-return dict-literal routes) ---
class GetConceptWikiPageResponse(BaseModel):
    slug: Any
    name: Any
    entity_type: str
    summary: Any
    members: list
    member_count: Any
    level: Any
    last_updated_at: Any



logger = logging.getLogger("ai-companion.wiki")

router = APIRouter(prefix="/wiki", tags=["wiki"])


# ---------------------------------------------------------------------------
# Response models are the service models — re-exported from here for
# OpenAPI schema generation.
# ---------------------------------------------------------------------------

__all__ = ["router"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/entities",
    response_model=list[EntitySummary],
    summary="List entity wiki pages",
    description=(
        "Returns up to ``limit`` entity summaries ordered by recent activity "
        "(count of source artifacts mentioning the entity in the last 30 days). "
        "Entities with no recent activity sort last."
    ),
)
async def list_entity_pages(
    limit: int = Query(
        default=30,
        ge=1,
        le=200,
        description="Maximum number of entities to return (1–200, default 30).",
    ),
    q: str | None = Query(
        default=None,
        description="Optional name/canonical_id search, applied server-side "
        "before the limit so it spans the whole entity set.",
    ),
    include_internal: bool = Query(
        default=False,
        description="WK2 advanced toggle. When False (default) the client-data "
        "domains (boardroom_foundation, canary_client_domain) are hidden. Set "
        "True to reveal them.",
    ),
) -> list[EntitySummary]:
    from app.deps import get_neo4j

    driver = get_neo4j()
    try:
        return await list_entities(
            driver, limit=limit, search=q, include_internal=include_internal
        )
    except Exception as exc:
        log_swallowed_error("wiki.list_entity_pages", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve entity list") from exc


@router.get(
    "/entities/{slug}",
    response_model=WikiEntityPage,
    summary="Get entity wiki page",
    description=(
        "Returns the full wiki page for the entity identified by ``slug`` "
        "(its ``canonical_id``). Includes summary, related entities, source "
        "artifact citations, contradictions, and confidence band. Returns 404 "
        "if no entity with this slug exists."
    ),
)
async def get_entity_wiki_page(slug: str) -> WikiEntityPage:
    from app.deps import get_neo4j

    driver = get_neo4j()
    try:
        page = await get_entity_page(driver, slug)
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_wiki_page", exc, context={"slug": slug})
        raise HTTPException(status_code=500, detail="Failed to retrieve entity wiki page") from exc

    if page is None:
        raise HTTPException(status_code=404, detail=f"Entity {slug!r} not found")

    return page


# ---------------------------------------------------------------------------
# WK1 — "What links here" backlinks
# ---------------------------------------------------------------------------


class BacklinkItem(BaseModel):
    """One entry in the backlinks list."""

    slug: str = Field(description="Canonical ID of the linking entity.")
    name: str = Field(description="Display name of the linking entity.")
    entity_type: str = Field(description="Entity type (PERSON, ORG, …).")
    via: Literal["wikilink", "mention", "related"] = Field(
        description=(
            "How this entity links to the target. "
            "``wikilink`` — the entity's summary contains a ``[[...]]`` wikilink; "
            "``mention`` — both entities appear in the same source artifact; "
            "``related`` — a direct CO_MENTIONED edge exists between them."
        )
    )


class BacklinksResponse(BaseModel):
    """Response body for ``GET /wiki/entities/{slug}/backlinks``."""

    backlinks: list[BacklinkItem] = Field(
        description=(
            "Entities that reference this entity, de-duplicated and ordered by "
            "via-source precedence (wikilink > mention > related), capped at 50."
        )
    )


@router.get(
    "/entities/{slug}/backlinks",
    response_model=BacklinksResponse,
    summary="What links here — entity backlinks (WK1)",
    description=(
        "Returns up to 50 entities that reference the entity identified by "
        "``slug``, grouped by how they link (``via``). "
        "``wikilink`` — the entity's generated summary contains a ``[[...]]`` "
        "wikilink to this entity; "
        "``mention`` — both appear in the same source artifact; "
        "``related`` — a direct CO_MENTIONED graph edge connects them. "
        "De-duplicated by slug; precedence wikilink > mention > related."
    ),
)
async def get_entity_backlinks(
    slug: str,
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of backlinks to return (1–200, default 50).",
    ),
) -> BacklinksResponse:
    import asyncio as _asyncio

    from app.deps import get_neo4j

    driver = get_neo4j()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")
    try:
        rows = await _asyncio.to_thread(get_backlinks, driver, slug, limit)
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_backlinks", exc, context={"slug": slug})
        raise HTTPException(
            status_code=500, detail="Failed to retrieve backlinks"
        ) from exc

    items = [
        BacklinkItem(
            slug=r.get("slug", ""),
            name=r.get("name", ""),
            entity_type=r.get("entity_type", "OTHER"),
            via=r.get("via", "related"),  # type: ignore[arg-type]
        )
        for r in rows
    ]
    return BacklinksResponse(backlinks=items)


# ---------------------------------------------------------------------------
# WK4 — manual refresh + summary edit
# ---------------------------------------------------------------------------


class ManualRefreshResponse(BaseModel):
    """Response body for ``POST /wiki/entities/{slug}/refresh``."""

    slug: str = Field(description="Canonical ID of the entity.")
    enqueued: bool = Field(description="True if the refresh job was enqueued.")


class EntitySummaryEditRequest(BaseModel):
    """Request body for ``PATCH /wiki/entities/{slug}``."""

    summary: str = Field(
        ...,
        description=(
            "New human-authored summary for the entity.  Replaces the "
            "generated summary and marks the entity as human-edited, "
            "suppressing automatic re-summarisation for the protection window."
        ),
    )


@router.post(
    "/entities/{slug}/refresh",
    response_model=ManualRefreshResponse,
    status_code=202,
    summary="Manually trigger a wiki refresh for an entity (WK4)",
    description=(
        "Enqueues a forced ``WikiRefreshJob`` for the entity identified by "
        "``slug``.  The ``force=True`` flag bypasses the per-entity debounce "
        "and the human-edit protection window so a user can always trigger a "
        "fresh summary on demand.  Returns 202 Accepted when enqueued, 404 if "
        "the entity does not exist."
    ),
)
async def manual_refresh_entity(slug: str) -> ManualRefreshResponse:
    from app.deps import get_neo4j

    driver = get_neo4j()
    page = await get_entity_page(driver, slug)
    if page is None:
        raise HTTPException(status_code=404, detail=f"Entity {slug!r} not found")

    try:
        enqueued = await asyncio.to_thread(enqueue_refresh, slug, force=True)
    except Exception as exc:
        log_swallowed_error(
            "wiki.manual_refresh_entity",
            exc,
            context={"slug": slug},
        )
        raise HTTPException(status_code=500, detail="Failed to enqueue refresh") from exc

    return ManualRefreshResponse(slug=slug, enqueued=enqueued)


@router.patch(
    "/entities/{slug}",
    response_model=WikiEntityPage,
    summary="Manually edit the entity wiki summary (WK4)",
    description=(
        "Persists a human-authored summary on the entity identified by ``slug``. "
        "Sets ``summary_edited_by='user'`` on the node, which suppresses automatic "
        "re-summarisation from the stale-sweep and grew-trigger for the protection "
        "window (7 days).  Contradiction-forced refreshes always bypass the "
        "protection.  Appends a ``manual_edit`` log entry to the knowledge log. "
        "Returns the updated ``WikiEntityPage``.  404 if the entity does not exist."
    ),
)
async def edit_entity_summary(slug: str, body: EntitySummaryEditRequest) -> WikiEntityPage:
    from datetime import datetime, timezone

    from app.deps import get_neo4j

    driver = get_neo4j()

    # Existence check
    page = await get_entity_page(driver, slug)
    if page is None:
        raise HTTPException(status_code=404, detail=f"Entity {slug!r} not found")

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    try:
        await asyncio.to_thread(
            write_entity_summary,
            driver,
            slug,
            body.summary,
            now_iso,
            edited_by="user",
        )
    except Exception as exc:
        log_swallowed_error(
            "wiki.edit_entity_summary.write",
            exc,
            context={"slug": slug},
        )
        raise HTTPException(status_code=500, detail="Failed to persist summary edit") from exc

    try:
        await asyncio.to_thread(
            append_log_entry,
            driver,
            action="manual_edit",
            entity_slug=slug,
            summary=body.summary[:200] if body.summary else None,
        )
    except Exception as exc:  # noqa: BLE001 — log failure must not fail the edit
        log_swallowed_error(
            "wiki.edit_entity_summary.log",
            exc,
            context={"slug": slug},
        )

    # Re-fetch the updated page so the response reflects the written state.
    try:
        updated = await get_entity_page(driver, slug)
    except Exception as exc:
        log_swallowed_error(
            "wiki.edit_entity_summary.refetch",
            exc,
            context={"slug": slug},
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve updated page") from exc

    if updated is None:
        raise HTTPException(status_code=404, detail=f"Entity {slug!r} not found after edit")

    return updated


@router.get(
    "/concepts/{community_id:path}",
    summary="Get concept (Leiden community) wiki page (Phase K5)",
    description=(
        "Returns a concept page derived from a Leiden community: prose "
        "summary + member entity list. Accepts both ``concept:{level}:"
        "{native_id}`` (Karpathy slug form) and bare ``{level}:{native_id}`` "
        "(matches the legacy ``/communities/{id}`` paths). Returns 404 if "
        "no community matches."
    ),
 response_model=GetConceptWikiPageResponse)
async def get_concept_wiki_page(community_id: str) -> dict[str, Any]:
    import asyncio as _asyncio

    from app.deps import get_neo4j
    from app.services.community_pages import get_community_page
    from app.services.wiki_pages import _resolve_community_label

    # Strip optional concept: prefix
    cid = community_id[len("concept:"):] if community_id.startswith("concept:") else community_id

    driver = get_neo4j()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")

    try:
        community = await get_community_page(driver, cid)
    except Exception as exc:
        log_swallowed_error("wiki.get_concept_wiki_page", exc, context={"community_id": cid})
        raise HTTPException(status_code=500, detail="Failed to retrieve concept page") from exc

    if community is None:
        raise HTTPException(status_code=404, detail=f"Concept {cid!r} not found")

    page_dict = community.model_dump() if hasattr(community, "model_dump") else dict(community)

    # Resolve the human-readable community label via the umap artifact
    # (same mechanism as the entity-page community_label).  Falls back to
    # the raw "Concept {cid}" placeholder only when the artifact is absent.
    resolved_name = await _asyncio.to_thread(_resolve_community_label, driver, cid)
    name = resolved_name or f"Concept {cid}"

    # last_updated_at: CommunityFull carries last_summarized_at (mapped from
    # summary_generated_at on the Community node); expose it here.
    last_updated_at = page_dict.get("last_summarized_at") or page_dict.get("last_updated_at")

    return {
        "slug": f"concept:{cid}",
        "name": name,
        "entity_type": "CONCEPT",
        "summary": page_dict.get("summary"),
        "members": [
            m.model_dump() if hasattr(m, "model_dump") else dict(m)
            for m in community.members
        ],
        "member_count": page_dict.get("member_count", 0),
        "level": page_dict.get("level", 0),
        "last_updated_at": last_updated_at,
        # confidence_band intentionally omitted — CONCEPT pages have no
        # claim-based confidence calculation; emitting "unknown" would be
        # a phantom class.  Consumers should treat absence as not-applicable.
    }


# ---------------------------------------------------------------------------
# Phase K4.2 + K4.3 — knowledge log + index
# ---------------------------------------------------------------------------


class KnowledgeLogEntry(BaseModel):
    """One row from the ``(:KnowledgeLog)`` table."""

    log_id: str
    ts: str
    action: str
    entity_slug: str | None = None
    summary: str | None = None
    source_artifact_id: str | None = None


class KnowledgeLogResponse(BaseModel):
    entries: list[KnowledgeLogEntry]
    total: int


class KnowledgeIndexEntry(BaseModel):
    """Catalog row for the Karpathy-shaped wiki index."""

    slug: str
    name: str
    entity_type: str
    one_liner: str | None = None
    last_updated_at: str | None = None
    activity_score: int = 0
    has_summary: bool = False
    # WK3 — article completeness class: "stub" | "start" | "full"
    completeness: Literal["stub", "start", "full"] = "stub"


class KnowledgeIndexResponse(BaseModel):
    entries: list[KnowledgeIndexEntry]
    total: int


@router.get(
    "/log",
    response_model=KnowledgeLogResponse,
    summary="List knowledge-log entries (Phase K4.2)",
    description=(
        "Karpathy-style chronological ledger of wiki refreshes, "
        "enrichments, and contradiction-triggered updates. "
        "Filterable by entity slug; paginated newest-first."
    ),
)
async def list_knowledge_log(
    entity_slug: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> KnowledgeLogResponse:
    from app.db.neo4j.knowledge_log import list_log_entries
    from app.deps import get_neo4j

    driver = get_neo4j()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")
    try:
        rows = list_log_entries(
            driver, entity_slug=entity_slug, since=since, limit=limit,
        )
    except Exception as exc:
        log_swallowed_error("wiki.knowledge_log.list", exc)
        raise HTTPException(status_code=500, detail="Failed to list log") from exc

    entries = [
        KnowledgeLogEntry(
            log_id=r.get("log_id", ""),
            ts=r.get("ts", ""),
            action=r.get("action", "refresh"),
            entity_slug=r.get("entity_slug") or None,
            summary=r.get("summary") or None,
            source_artifact_id=r.get("source_artifact_id") or None,
        )
        for r in rows
    ]
    return KnowledgeLogResponse(entries=entries, total=len(entries))


@router.get(
    "/index",
    response_model=KnowledgeIndexResponse,
    summary="Karpathy-shaped wiki index (Phase K4.3)",
    description=(
        "LLM-readable catalog of entity pages — one row per "
        "entity with slug, name, one-line summary, last updated, "
        "and activity score. The surface router uses this to "
        "discover slugs when a fuzzy name doesn't match directly. "
        "``q`` filters server-side before the limit (whole-entity-set "
        "search). ``order=name`` sorts alphabetically for the A-Z view; "
        "default order is recent-activity descending."
    ),
)
async def list_knowledge_index(
    limit: int = 100,
    q: str | None = None,
    order: str | None = Query(
        default=None,
        description="Sort order: 'name' for A-Z; omit for activity-score descending.",
    ),
    include_internal: bool = Query(
        default=False,
        description="WK2 advanced toggle. When False (default) the client-data "
        "domains (boardroom_foundation, canary_client_domain) are excluded from "
        "the index. Set True to include them.",
    ),
) -> KnowledgeIndexResponse:
    from app.deps import get_neo4j

    driver = get_neo4j()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")

    # Reuse list_entities then project to the K4.3 shape.
    # q is passed pre-limit into list_entities so filtering spans the whole
    # entity set rather than a post-limit slice.
    from app.services.wiki_pages import list_entities  # noqa: PLC0415

    try:
        summaries = await list_entities(
            driver, limit=limit, search=q or None, include_internal=include_internal
        )
    except Exception as exc:
        log_swallowed_error("wiki.knowledge_index.list", exc)
        raise HTTPException(status_code=500, detail="Failed to load index") from exc

    if order == "name":
        summaries = sorted(summaries, key=lambda s: s.name.lower())

    entries = [
        KnowledgeIndexEntry(
            slug=s.canonical_id,
            name=s.name,
            entity_type=s.entity_type,
            one_liner=(s.summary[:160] if s.summary else None),
            last_updated_at=s.summary_updated_at,
            activity_score=int(s.recent_activity_score),
            has_summary=bool(s.summary),
            completeness=compute_completeness(s.summary, s.mention_count, "unknown"),
        )
        for s in summaries
    ]
    return KnowledgeIndexResponse(entries=entries, total=len(entries))


# ---------------------------------------------------------------------------
# RAG C3.3 — two-way vault writeback
# ---------------------------------------------------------------------------


class WriteNoteRequestModel(BaseModel):
    """Request body for ``POST /wiki/write_note``."""

    vault_id: str = Field(
        ...,
        description=(
            "Watched-folder ID for a folder registered with ``is_vault=True``. "
            "See ``POST /watched-folders``."
        ),
    )
    path: str = Field(
        ...,
        description=(
            "Path relative to the vault root.  ``.md`` is appended if absent. "
            "Must not escape the vault root via ``..`` and must not resolve "
            "into a templates/ or attachments/ folder."
        ),
    )
    content: str = Field(
        ...,
        description=(
            "Markdown body.  May contain ``[[wikilinks]]`` and other "
            "Obsidian-flavoured markdown features."
        ),
    )
    frontmatter: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional caller-supplied frontmatter that merges over the "
            "Cerid stamps (``source``, ``cerid:created``).  Only allowlisted "
            "keys (reserved + ``cerid:*``) flow through."
        ),
    )
    mode: Literal["create", "append", "overwrite"] = Field(
        default="create",
        description=(
            "``create`` rejects an existing file; ``append`` adds content "
            "to the end of an existing file preserving its frontmatter; "
            "``overwrite`` atomically replaces the file."
        ),
    )
    allow_synthesis_input: bool = Field(
        default=False,
        description=(
            "When True, stamps ``cerid:reanalyze=true`` so the synthesis-job "
            "input filter re-includes this note.  Default False — "
            "Cerid-authored notes are excluded from synthesis to prevent "
            "recursive amplification."
        ),
    )


class WriteNoteResponse(BaseModel):
    """Response body for ``POST /wiki/write_note``."""

    file_path: str = Field(description="Absolute on-disk path of the written file.")
    artifact_id: str | None = Field(
        default=None,
        description=(
            "Neo4j Artifact ID assigned by the re-ingestion step.  ``null`` "
            "if the write succeeded but ingestion failed (file still on disk)."
        ),
    )
    ingested: bool = Field(
        description=(
            "True when the post-write ``ingest_content`` call produced an "
            "Artifact node.  False indicates the file was written but the "
            "Cerid graph does not yet know about it — see ``reingest_error``."
        ),
    )
    frontmatter_written: dict[str, Any] = Field(
        description=(
            "The exact frontmatter dict that landed in the file header — "
            "Cerid stamps + caller-supplied allowlisted keys."
        ),
    )
    mode: str = Field(description="Echoes the request mode.")
    reingest_error: str | None = Field(
        default=None,
        description=(
            "Stringified exception from the re-ingestion step when "
            "``ingested=False``.  ``null`` on the happy path."
        ),
    )


@router.post(
    "/write_note",
    response_model=WriteNoteResponse,
    summary="Write a markdown note back to a registered vault (RAG C3.3)",
    description=(
        "Two-way vault write — Cerid's outputs persist back to the user's "
        "vault as markdown notes.  The file is written atomically, then "
        "re-ingested as an ``Artifact`` node tagged with "
        "``source_type='cerid-synthesis'`` so synthesis jobs exclude it "
        "from their input set by default (set ``allow_synthesis_input=true`` "
        "to opt back in).  Path safety: rejects ``..`` escapes and "
        "templates/ / attachments/ folder writes."
    ),
)
async def write_note_endpoint(payload: WriteNoteRequestModel) -> WriteNoteResponse:
    from app.deps import get_redis
    from app.services.vault_write import (
        VaultWriteError,
        WriteNoteRequest,
        write_note,
    )

    req = WriteNoteRequest(
        vault_id=payload.vault_id,
        path=payload.path,
        content=payload.content,
        frontmatter=payload.frontmatter,
        mode=payload.mode,
        allow_synthesis_input=payload.allow_synthesis_input,
    )

    redis_client = get_redis()
    try:
        result = await asyncio.to_thread(write_note, req, redis_client)
    except VaultWriteError as exc:
        # Pre-disk validation failures — surface as HTTP 400 so the
        # caller can correct the request without inspecting logs.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_swallowed_error(
            "wiki.write_note_endpoint",
            exc,
            context={"vault_id": payload.vault_id, "path": payload.path},
        )
        raise HTTPException(
            status_code=500, detail="Failed to write note to vault",
        ) from exc

    return WriteNoteResponse(
        file_path=result.file_path,
        artifact_id=result.artifact_id,
        ingested=result.ingested,
        frontmatter_written=result.frontmatter_written,
        mode=result.mode,
        reingest_error=result.reingest_error,
    )
