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

from app.services.wiki_pages import (
    EntitySummary,
    WikiEntityPage,
    get_entity_page,
    list_entities,
)
from core.utils.swallowed import log_swallowed_error

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
) -> list[EntitySummary]:
    from app.deps import get_neo4j

    driver = get_neo4j()
    try:
        return await list_entities(driver, limit=limit)
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
