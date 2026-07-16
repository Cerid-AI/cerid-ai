# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""KB Administration endpoints — rebuild indexes, rescore, regenerate summaries, clear domains, delete artifacts."""
from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import config
from app.agents.curator import curate
from app.db.neo4j.artifacts import (
    delete_artifact,
    delete_artifacts_by_domain,
    domain_artifact_stats,
    get_artifact,
    list_artifacts,
    list_duplicate_artifacts,
)
from app.deps import get_chroma, get_neo4j, get_redis
from config.features import CERID_MULTI_USER
from core.retrieval.bm25 import rebuild_all as rebuild_bm25_all
from core.retrieval.semantic_cache import invalidate_cache as invalidate_semantic_cache
from core.utils.swallowed import log_swallowed_error
from utils.encryption import decrypt_field
from utils.query_cache import invalidate_cache_non_blocking


# --- Response models (generated: single-return dict-literal routes) ---
class ClearDomainResponse(BaseModel):
    domain: Any
    artifacts_deleted: Any
    chunks_removed: Any
    message: Any


class MergeDuplicatesResponse(BaseModel):
    status: str
    merged: Any


class DismissDuplicatesResponse(BaseModel):
    status: str
    dismissed: Any



logger = logging.getLogger("ai-companion.kb-admin")


def _require_admin(request: Request) -> None:
    """Block non-admin users in multi-user mode. No-op in single-user."""
    if not CERID_MULTI_USER:
        return
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


router = APIRouter(tags=["kb-admin"], dependencies=[Depends(_require_admin)])


def _invalidate_semantic_cache_safe(trigger: str) -> None:
    """Best-effort semantic-cache invalidation alongside the existing
    flat query-cache invalidation (Phase 2.2). Admin mutation endpoints
    already call synchronous store operations directly (no ``to_thread``),
    so this mirrors that style rather than the ingest path's daemon-thread
    wrapper. Never raises into the admin endpoint.
    """
    try:
        invalidate_semantic_cache(get_redis(), trigger=trigger)
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.routers.kb_admin.semantic_cache_invalidate", e)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RebuildIndexResponse(BaseModel):
    domains_rebuilt: int
    message: str


class RescoreRequest(BaseModel):
    domains: list[str] | None = Field(None, description="Domains to rescore (None = all)")
    max_artifacts: int = Field(200, ge=1, le=1000, description="Max artifacts per domain")


class RescoreResponse(BaseModel):
    artifacts_scored: int
    avg_quality_score: float
    message: str


class RegenerateSummariesRequest(BaseModel):
    domains: list[str] | None = Field(None, description="Domains to regenerate (None = all)")
    max_artifacts: int = Field(200, ge=1, le=1000, description="Max artifacts per domain")
    model: str | None = Field(None, description="Model override for synopsis generation")
    force: bool = Field(False, description="Force regenerate all synopses, not just truncated ones")


class RegenerateSummariesResponse(BaseModel):
    synopses_generated: int
    artifacts_scored: int
    message: str


class ClearDomainRequest(BaseModel):
    confirm: bool = Field(False, description="Must be true to proceed with clearing")


class DeleteArtifactResponse(BaseModel):
    deleted: bool
    artifact_id: str
    filename: str
    chunks_removed: int
    message: str


class KBStatsResponse(BaseModel):
    total_artifacts: int
    total_chunks: int
    domains: dict[str, Any]


class ParserCapability(BaseModel):
    extension: str
    parser: str
    tier: str = "community"
    available: bool = True


class ParserCapabilitiesResponse(BaseModel):
    capabilities: list[ParserCapability]
    tier: str


class ReingestResponse(BaseModel):
    status: str
    artifact_id: str
    domain: str
    chunks: int
    timestamp: str


class ReindexCorpusRequest(BaseModel):
    domain: str | None = Field(
        None, description="Restrict to one domain (None = whole corpus)"
    )
    offset: int = Field(0, ge=0, description="Pagination cursor for resumable batches")
    limit: int = Field(
        25, ge=1, le=200, description="Artifacts to process this call (bounds request time)"
    )


class ReindexCorpusResponse(BaseModel):
    requested: int
    reindexed: int
    skipped: int
    errors: int
    next_offset: int | None
    message: str


class ReembedRequest(BaseModel):
    domain: str | None = Field(
        None, description="Restrict to one domain (None = every domain)"
    )
    force: bool = Field(
        False,
        description="Re-embed every chunk regardless of its current version stamp",
    )


class ReembedResponse(BaseModel):
    status: str
    job_id: str | None
    domain: str | None
    message: str


class DomainVersionDistribution(BaseModel):
    total: int
    versions: dict[str, int]
    current_version: str
    mixed: bool


class EmbeddingVersionsResponse(BaseModel):
    domains: dict[str, DomainVersionDistribution]


class CollectionRepairRequest(BaseModel):
    collection_name: str = Field(..., description="Chroma collection name to repair")
    dry_run: bool = Field(
        True,
        description="When true (default), reports what would happen without touching state",
    )


class CollectionRepairResponse(BaseModel):
    status: str
    collection_name: str
    domain: str
    actual_dim: int | None
    expected_dim: int
    artifacts_found: int
    rebuilt_documents: int
    backup_path: str | None
    dry_run: bool
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/kb/capabilities", response_model=ParserCapabilitiesResponse)
async def get_parser_capabilities():
    """Return supported file types and which parser/plugin handles each."""
    import os

    from app.parsers.registry import PARSER_REGISTRY

    capabilities: list[dict[str, Any]] = []
    for ext, parser_fn in PARSER_REGISTRY.items():
        capabilities.append({
            "extension": ext,
            "parser": parser_fn.__module__.split(".")[-1],
            "tier": "community",
            "available": True,
        })

    # Pro-tier plugins (OCR, audio transcription)
    pro_extensions = {
        ".png": "ocr", ".jpg": "ocr", ".jpeg": "ocr", ".tiff": "ocr", ".bmp": "ocr",
        ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio", ".flac": "audio",
    }
    tier = os.getenv("CERID_TIER", "community")
    registered_exts = {c["extension"] for c in capabilities}
    for ext, plugin in pro_extensions.items():
        if ext not in registered_exts:
            capabilities.append({
                "extension": ext,
                "parser": plugin,
                "tier": "pro",
                "available": tier == "pro",
            })

    return ParserCapabilitiesResponse(
        capabilities=[ParserCapability(**c) for c in capabilities],
        tier=tier,
    )


@router.post("/admin/artifacts/{artifact_id}/reingest", response_model=ReingestResponse)
async def reingest_artifact(artifact_id: str):
    """Re-parse and re-embed an existing artifact from its source file."""
    from pathlib import Path

    from app.services.ingestion import ingest_file

    try:
        neo4j = get_neo4j()
        # AF-054: fetch the one artifact by id directly — was a full-domain
        # list_artifacts(limit=10000) + Python loop for a single-artifact op.
        target = get_artifact(neo4j, artifact_id)
        if not target:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

        filename = target.get("filename", "")
        domain = target.get("domain", "")
        if not filename:
            raise HTTPException(status_code=400, detail="Artifact has no filename — cannot reingest")

        # Locate source file in archive
        archive_root = Path(config.ARCHIVE_PATH).resolve()
        source_path = archive_root / filename
        if not source_path.exists():
            # Try domain subdirectory
            source_path = archive_root / domain / filename
        if not source_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Source file not found in archive: {filename}",
            )

        # Re-ingest (ingest_file handles dedup via content hash — same filename
        # with different content triggers _reingest_artifact internally)
        result = await ingest_file(
            file_path=str(source_path),
            domain=domain,
            sub_category=target.get("sub_category", ""),
        )

        await invalidate_cache_non_blocking()
        _invalidate_semantic_cache_safe("kb_admin.reingest_artifact")

        return ReingestResponse(
            status=result.get("status", "success"),
            artifact_id=result.get("artifact_id", artifact_id),
            domain=result.get("domain", domain),
            chunks=result.get("chunks", 0),
            timestamp=result.get("timestamp", ""),
        )
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to reingest artifact %s: %s", artifact_id[:8], e)
        raise HTTPException(status_code=500, detail=f"Failed to reingest: {e}")


def _resolve_archive_source(filename: str, domain: str) -> Path | None:
    """Locate an artifact's source file under the archive root, or None.

    Mirrors the lookup in ``reingest_artifact``: archive root first, then the
    per-domain subdirectory.
    """
    if not filename:
        return None
    archive_root = Path(config.ARCHIVE_PATH).resolve()
    candidate = archive_root / filename
    if candidate.exists():
        return candidate
    candidate = archive_root / domain / filename
    if candidate.exists():
        return candidate
    return None


@router.post("/admin/kb/reindex", response_model=ReindexCorpusResponse)
async def reindex_corpus(req: ReindexCorpusRequest | None = None):
    """Re-embed + re-index existing artifacts so newly-enabled retrieval
    features (contextual chunking, sentence-window) apply retroactively.

    Idempotent + resumable: each artifact is force-reingested from its source
    file via ``ingest_file(force_reindex=True)`` — which deletes the stale
    Chroma/BM25/sparse rows, rewrites all three from the same content-addressed
    chunk ids, and invalidates the semantic cache — so re-running any page is
    safe. Drive to completion by looping ``offset`` until ``next_offset`` is
    null. Artifacts with no resolvable source file are skipped (their only copy
    is the chunk text, which cannot be cleanly re-chunked).

    NOTE: this does NOT flip any feature flag. Enable the flag first
    (``ENABLE_CONTEXTUAL_CHUNKS`` / ``ENABLE_SENTENCE_WINDOW``), then run this.
    """
    from app.services.ingestion import ingest_file

    # Mirror the other admin mutators: tolerate a bodyless POST by falling back
    # to the model defaults rather than constructing the model with no args.
    domain_filter = req.domain if req else None
    offset = req.offset if req else 0
    limit = req.limit if req else 25
    try:
        neo4j = get_neo4j()
        artifacts = list_artifacts(
            neo4j, domain=domain_filter, offset=offset, limit=limit,
        )
    except Exception as e:
        logger.error("Reindex: failed to list artifacts: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list artifacts: {e}")

    reindexed = 0
    skipped = 0
    errors = 0
    for a in artifacts:
        filename = a.get("filename", "")
        domain = a.get("domain", "") or config.DEFAULT_DOMAIN
        source = _resolve_archive_source(filename, domain)
        if source is None:
            skipped += 1
            continue
        try:
            result = await ingest_file(
                file_path=str(source),
                domain=domain,
                sub_category=a.get("sub_category", "") or "",
                force_reindex=True,
            )
            if result.get("status") in ("success", "updated"):
                reindexed += 1
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001 — per-artifact failure must not abort the batch
            errors += 1
            log_swallowed_error(
                "app.routers.kb_admin.reindex_corpus", e,
                # "filename" is a reserved LogRecord attribute — use a
                # non-colliding key (see _ingest_single_attachment).
                context={"artifact_id": a.get("id", ""), "source_filename": filename},
            )

    # Per-artifact re-ingest already invalidates the semantic cache; invalidate
    # the flat query cache once for the batch to match the other admin mutators.
    await invalidate_cache_non_blocking()
    _invalidate_semantic_cache_safe("kb_admin.reindex_corpus")

    next_offset = offset + limit if len(artifacts) == limit else None
    return ReindexCorpusResponse(
        requested=len(artifacts),
        reindexed=reindexed,
        skipped=skipped,
        errors=errors,
        next_offset=next_offset,
        message=(
            f"Re-indexed {reindexed}/{len(artifacts)} artifact(s) "
            f"(skipped {skipped}, errors {errors})."
            + (f" More remain — resume at offset={next_offset}." if next_offset else "")
        ),
    )


@router.post("/admin/kb/reembed", response_model=ReembedResponse)
async def reembed_corpus(req: ReembedRequest | None = None):
    """Enqueue the managed re-embed job (Phase 4.4).

    Promotes ``scripts/reembed_collection.py``'s manual dual-collection
    logic into a resumable background job: re-embeds, in place, every
    chunk in the target domain(s) whose ``embedding_model_version`` stamp
    does not match the domain's currently active version (or every
    chunk when ``force=true``). Chunk text is never touched, so BM25 /
    sparse indexes need no rebuild; the semantic cache is invalidated by
    the job on completion (vectors changed under the same text).

    Unlike ``/admin/kb/reindex`` (synchronous, one page per call), this
    returns immediately with a ``job_id``. Live queue/worker state is
    visible via ``GET /processor/status``; the job's ``progress`` and
    ``metadata`` are surfaced on ``GET /processor/recent`` as a terminal
    snapshot (live per-job progress is persisted on the record as the job
    runs, but ``/recent`` lists only terminal jobs). ``enqueue_if_absent``
    collapses a duplicate call against the same domain+force while one is
    already pending or running.
    """
    domain = req.domain if req else None
    force = req.force if req else False

    if domain is not None and domain not in config.DOMAINS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")

    from app.db.redis.processor_queue import RedisJobQueue
    from app.processor.jobs.reembed_chunks import ReembedChunksJob

    try:
        queue = RedisJobQueue(get_redis())
        job = ReembedChunksJob(domain=domain, force=force)
        record = job.new_record(payload={"domain": domain, "force": force})
        job_id = await queue.enqueue_if_absent(record)
    except Exception as e:
        logger.error("Failed to enqueue reembed job: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to enqueue reembed job: {e}")

    if job_id is None:
        return ReembedResponse(
            status="already_running",
            job_id=None,
            domain=domain,
            message="A matching re-embed job is already pending or running.",
        )

    scope = f"domain={domain}" if domain else "all domains"
    return ReembedResponse(
        status="enqueued",
        job_id=job_id,
        domain=domain,
        message=(
            f"Enqueued re-embed job {job_id} for {scope}"
            f"{' (force=true)' if force else ''}."
        ),
    )


def _domain_version_distribution(chroma_client: Any, domain: str) -> dict[str, Any]:
    """Cheap per-domain ``embedding_model_version`` histogram.

    Pages through the domain's collection reading ONLY ``metadatas``
    (no document text, no vectors) — keeps a multi-million-chunk KB from
    turning this into a heavy scan. Chunks with no
    ``embedding_model_version`` field (pre-Phase-4.4 legacy ingest) are
    bucketed under ``"unstamped"``.
    """
    coll_name = config.collection_name(domain)
    try:
        collection = chroma_client.get_collection(name=coll_name)
    except Exception as exc:  # noqa: BLE001 — domain has no collection yet
        log_swallowed_error(
            "app.routers.kb_admin.domain_version_distribution.get_collection", exc,
            context={"domain": domain},
        )
        return {"total": 0, "versions": {}}

    versions: dict[str, int] = {}
    total = 0
    offset = 0
    page = 1000
    while True:
        try:
            batch = collection.get(limit=page, offset=offset, include=["metadatas"])
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.routers.kb_admin.domain_version_distribution.get_batch", exc,
                context={"domain": domain, "offset": offset},
            )
            break
        metadatas = batch.get("metadatas") or []
        if not metadatas:
            break
        for meta in metadatas:
            version = (meta or {}).get("embedding_model_version") or "unstamped"
            versions[version] = versions.get(version, 0) + 1
        total += len(metadatas)
        if len(metadatas) < page:
            break
        offset += page
    return {"total": total, "versions": versions}


@router.get("/admin/kb/embedding-versions", response_model=EmbeddingVersionsResponse)
async def embedding_versions(domain: str | None = None):
    """Per-domain ``embedding_model_version`` distribution.

    Detects a mixed-version corpus — multiple stamped versions, or a mix
    of stamped + unstamped legacy chunks — so an operator knows when
    ``POST /admin/kb/reembed`` (or the manual
    ``scripts/reembed_collection.py`` dual-collection path) has work to
    do. ``total - versions[current_version]`` is the count of chunks the
    re-embed job would touch on a non-force run for that domain.
    """
    import asyncio

    if domain is not None and domain not in config.DOMAINS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")

    chroma = get_chroma()
    target_domains = [domain] if domain else list(config.DOMAINS)

    out: dict[str, DomainVersionDistribution] = {}
    for d in target_domains:
        dist = await asyncio.to_thread(_domain_version_distribution, chroma, d)
        current = config.embedding_version_for_domain(d)
        dist_versions: dict[str, int] = dist["versions"]
        total = dist["total"]
        mixed = total > 0 and (len(dist_versions) > 1 or dist_versions.get(current, 0) != total)
        out[d] = DomainVersionDistribution(
            total=total,
            versions=dist_versions,
            current_version=current,
            mixed=mixed,
        )
    return EmbeddingVersionsResponse(domains=out)


@router.post("/admin/kb/rebuild-index", response_model=RebuildIndexResponse)
async def rebuild_indexes():
    """Rebuild BM25 indexes for all domains from disk."""
    try:
        rebuilt = rebuild_bm25_all()
        await invalidate_cache_non_blocking()
        _invalidate_semantic_cache_safe("kb_admin.rebuild_indexes")
        return RebuildIndexResponse(
            domains_rebuilt=rebuilt,
            message=f"Rebuilt BM25 indexes for {rebuilt} domains",
        )
    except Exception as e:
        logger.error("Failed to rebuild indexes: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to rebuild indexes: {e}")


@router.post("/admin/kb/rescore", response_model=RescoreResponse)
async def rescore_artifacts(req: RescoreRequest | None = None):
    """Recalculate quality scores for all artifacts."""
    domains = req.domains if req else None
    max_artifacts = req.max_artifacts if req else 200
    try:
        neo4j = get_neo4j()
        result = await curate(
            neo4j,
            mode="audit",
            domains=domains,
            max_artifacts=max_artifacts,
        )
        await invalidate_cache_non_blocking()
        _invalidate_semantic_cache_safe("kb_admin.rescore_artifacts")
        return RescoreResponse(
            artifacts_scored=result["artifacts_scored"],
            avg_quality_score=result["avg_quality_score"],
            message=f"Rescored {result['artifacts_scored']} artifacts (avg: {result['avg_quality_score']:.2f})",
        )
    except Exception as e:
        logger.error("Failed to rescore: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to rescore: {e}")


@router.post("/admin/kb/regenerate-summaries", response_model=RegenerateSummariesResponse)
async def regenerate_summaries(req: RegenerateSummariesRequest | None = None):
    """Regenerate AI synopses for artifacts with raw/truncated summaries.

    Set force=true to regenerate ALL synopses, not just truncated ones.
    """
    domains = req.domains if req else None
    max_artifacts = req.max_artifacts if req else 200
    model = req.model if req else None
    force = req.force if req else False
    try:
        neo4j = get_neo4j()
        chroma = get_chroma()
        result = await curate(
            neo4j,
            mode="audit",
            domains=domains,
            max_artifacts=max_artifacts,
            chroma_client=chroma,
            generate_synopses=True,
            synopsis_model=model,
            force_synopses=force,
        )
        await invalidate_cache_non_blocking()
        _invalidate_semantic_cache_safe("kb_admin.regenerate_summaries")
        return {
            "synopses_generated": result["synopses_generated"],
            "artifacts_scored": result["artifacts_scored"],
            "message": f"Generated {result['synopses_generated']} synopses, scored {result['artifacts_scored']} artifacts",
        }
    except Exception as e:
        logger.error("Failed to regenerate summaries: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to regenerate summaries: {e}")


@router.post("/admin/kb/clear-domain/{domain}", response_model=ClearDomainResponse)
async def clear_domain(domain: str, req: ClearDomainRequest):
    """Clear all artifacts in a specific domain. Requires confirm=true."""
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Must set confirm=true to clear domain")

    if domain not in config.DOMAINS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")

    try:
        neo4j = get_neo4j()
        chroma = get_chroma()

        # AF-093: delete every artifact in the domain in ONE domain-scoped
        # DETACH DELETE (no per-artifact loop, no 10k cap → no orphaned tail).
        # Neo4j first — safer ordering avoids split-brain if the process crashes
        # between the two phases; the Chroma collection is dropped wholesale next.
        _del = delete_artifacts_by_domain(neo4j, domain)
        deleted_count = _del["deleted"]
        chunks_removed = _del["chunks"]

        # Delete ChromaDB collection for the domain
        coll_name = config.collection_name(domain)
        try:
            chroma.delete_collection(name=coll_name)
            logger.info("Deleted ChromaDB collection: %s", coll_name)
        except Exception as e:
            log_swallowed_error('app.routers.kb_admin', e)
            logger.warning("Failed to delete collection %s: %s", coll_name, e)

        await invalidate_cache_non_blocking()
        _invalidate_semantic_cache_safe("kb_admin.clear_domain")

        return {
            "domain": domain,
            "artifacts_deleted": deleted_count,
            "chunks_removed": chunks_removed,
            "message": f"Cleared {deleted_count} artifacts from {domain}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to clear domain %s: %s", domain, e)
        raise HTTPException(status_code=500, detail=f"Failed to clear domain: {e}")


@router.delete("/admin/artifacts/{artifact_id}", response_model=DeleteArtifactResponse)
async def delete_single_artifact(artifact_id: str):
    """Delete a single artifact from Neo4j and ChromaDB."""
    try:
        neo4j = get_neo4j()
        chroma = get_chroma()

        result = delete_artifact(neo4j, artifact_id)
        if not result.get("deleted"):
            raise HTTPException(status_code=404, detail="Artifact not found")

        # Clean up ChromaDB chunks
        chunk_ids = result.get("chunk_ids", [])
        domain = result.get("domain", "")
        chunks_removed = 0
        if chunk_ids and domain:
            coll_name = config.collection_name(domain)
            try:
                collection = chroma.get_collection(name=coll_name)
                collection.delete(ids=chunk_ids)
                chunks_removed = len(chunk_ids)
            except Exception as e:
                log_swallowed_error('app.routers.kb_admin', e)
                logger.warning("Failed to clean ChromaDB chunks: %s", e)

        await invalidate_cache_non_blocking()
        _invalidate_semantic_cache_safe("kb_admin.delete_single_artifact")

        return DeleteArtifactResponse(
            deleted=True,
            artifact_id=artifact_id,
            filename=result.get("filename", ""),
            chunks_removed=chunks_removed,
            message=f"Deleted artifact {result.get('filename', artifact_id[:8])}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete artifact %s: %s", artifact_id[:8], e)
        raise HTTPException(status_code=500, detail=f"Failed to delete artifact: {e}")


def _derive_domain_from_collection(collection_name: str) -> str | None:
    """Reverse ``config.collection_name()`` to recover the source domain.

    Handles both legacy ``domain_{slug}`` and namespaced ``kb_{ns}_{slug}``.
    Returns ``None`` if the collection name does not match either pattern
    or the recovered slug is not a known domain.
    """
    slug: str | None = None
    if collection_name.startswith("domain_"):
        slug = collection_name[len("domain_"):]
    elif collection_name.startswith("kb_"):
        # kb_{ns}_{slug} — slug is everything after the second underscore
        parts = collection_name.split("_", 2)
        if len(parts) == 3:
            slug = parts[2]
    if slug and slug in config.DOMAINS:
        return slug
    return None


def _backup_collection(chroma_client: Any, coll_name: str) -> tuple[str, int]:
    """Export a collection's documents + metadata to JSONL under ``data/backups/``.

    Returns ``(backup_path, document_count)``. Always creates the backup
    directory; writes a single JSONL file timestamped to the second.
    """
    import json as _json
    from datetime import datetime, timezone
    from pathlib import Path

    backup_dir = Path(config.DATA_DIR if hasattr(config, "DATA_DIR") else "data") / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{coll_name}_{ts}.jsonl"

    try:
        collection = chroma_client.get_collection(name=coll_name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Collection not found: {coll_name} ({e})")

    # Dump all documents. get() with no ids returns everything.
    try:
        dump = collection.get(include=["documents", "metadatas"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export collection: {e}")

    ids = dump.get("ids", []) or []
    documents = dump.get("documents", []) or []
    metadatas = dump.get("metadatas", []) or []

    with backup_path.open("w", encoding="utf-8") as fh:
        for i, _id in enumerate(ids):
            record = {
                "id": _id,
                "document": documents[i] if i < len(documents) else None,
                "metadata": metadatas[i] if i < len(metadatas) else None,
            }
            fh.write(_json.dumps(record, default=str) + "\n")

    return str(backup_path), len(ids)


@router.post("/admin/collections/repair", response_model=CollectionRepairResponse)
async def repair_collection(req: CollectionRepairRequest):
    """Repair a dim-mismatched Chroma collection.

    Pipeline (non-dry-run):
      1. Backup docs + metadata to ``data/backups/<collection>_<ts>.jsonl``
      2. Delete the collection (drops the dim lock)
      3. Recreate via ``get_or_create_collection`` (picks up current ef dim)
      4. Re-ingest every ``:Artifact`` that BELONGS_TO the collection's domain
         using the existing ingestion pipeline — no chunking reimplementation.

    Dry-run reports what would happen without touching anything.
    """
    from app.services.ingestion import ingest_content
    from core.utils.embeddings import get_embedding_dim

    coll_name = req.collection_name
    domain = _derive_domain_from_collection(coll_name)
    if domain is None:
        raise HTTPException(
            status_code=400,
            detail=f"Collection {coll_name!r} does not map to a known domain",
        )

    chroma = get_chroma()
    neo4j = get_neo4j()

    try:
        expected_dim = get_embedding_dim()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not determine expected dim: {e}")

    # Probe current dim (for reporting)
    from app.startup import _probe_collection_dim
    try:
        existing = chroma.get_collection(name=coll_name)
        actual_dim = _probe_collection_dim(existing)
    except Exception as exc:
        log_swallowed_error('app.routers.kb_admin', exc)
        actual_dim = None

    # Find artifacts that would be re-ingested
    artifacts = list_artifacts(neo4j, domain=domain, limit=10000)

    if req.dry_run:
        return CollectionRepairResponse(
            status="dry_run",
            collection_name=coll_name,
            domain=domain,
            actual_dim=actual_dim,
            expected_dim=expected_dim,
            artifacts_found=len(artifacts),
            rebuilt_documents=0,
            backup_path=None,
            dry_run=True,
            message=(
                f"Dry run: would back up collection {coll_name!r}, delete it, recreate "
                f"with dim={expected_dim}, and re-ingest {len(artifacts)} artifact(s)."
            ),
        )

    # --- APPLY ---
    backup_path, backed_up = _backup_collection(chroma, coll_name)
    logger.warning(
        "Repair: backed up %d docs from %s to %s", backed_up, coll_name, backup_path,
    )

    try:
        chroma.delete_collection(name=coll_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete collection: {e}")

    # Recreating will pick up the current embedder dim via _EmbeddingAwareClient.
    chroma.get_or_create_collection(name=coll_name)

    # Artifact nodes in Neo4j (``artifacts`` above) carry the canonical
    # filename/domain/sub_category metadata; the raw chunk text lives only
    # in the deleted ChromaDB collection, so we rehydrate via the JSONL
    # backup rather than needing the source file on disk.
    rebuilt = 0

    # Replay the backup into the fresh collection via the public ingest path.
    import asyncio
    import json as _json
    from pathlib import Path

    replayed = 0
    try:
        with Path(backup_path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception as exc:
                    log_swallowed_error('app.routers.kb_admin', exc)
                    continue
                doc = rec.get("document")
                meta = rec.get("metadata") or {}
                if not doc:
                    continue
                try:
                    replay_meta = {
                        k: v for k, v in meta.items()
                        if k in (
                            "filename", "sub_category", "tags_json",
                            "keywords_json", "summary", "client_source",
                            "file_type",
                        )
                    }
                    if replay_meta.get("summary"):
                        # meta is the raw Chroma dump — "summary" may carry
                        # the enc:v1: Chroma-only ciphertext (see
                        # CHROMA_ENCRYPTED_FIELDS). decrypt_field no-ops on
                        # plaintext; ingest_content re-encrypts the Chroma
                        # copy and keeps Neo4j's summary cleartext.
                        replay_meta["summary"] = decrypt_field(str(replay_meta["summary"]))
                    # AF-092: ingest_content is fully synchronous (re-chunk +
                    # re-embed + relationship discovery). Calling it directly here
                    # blocked the event loop for the ENTIRE replay, stalling health
                    # probes until the mcp-watchdog force-exited the container
                    # mid-repair. Offload each record to a worker thread so the
                    # loop keeps serving.
                    await asyncio.to_thread(
                        ingest_content,
                        content=doc,
                        domain=domain,
                        metadata=replay_meta,
                    )
                    replayed += 1
                except Exception as e:
                    log_swallowed_error('app.routers.kb_admin', e)
                    logger.warning("Repair replay failed for one doc: %s", e)
        rebuilt = replayed
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Backup replay failed (collection was dropped — restore from {backup_path}): {e}",
        )

    await invalidate_cache_non_blocking()
    _invalidate_semantic_cache_safe("kb_admin.repair_collection")

    return CollectionRepairResponse(
        status="repaired",
        collection_name=coll_name,
        domain=domain,
        actual_dim=actual_dim,
        expected_dim=expected_dim,
        artifacts_found=len(artifacts),
        rebuilt_documents=rebuilt,
        backup_path=backup_path,
        dry_run=False,
        message=(
            f"Repaired {coll_name!r}: backed up {backed_up} doc(s) to {backup_path}, "
            f"recreated at dim={expected_dim}, replayed {rebuilt} doc(s)."
        ),
    )


# ---------------------------------------------------------------------------
# Near-duplicate detection (Sprint 2 stub: groups by exact content_hash)
# ---------------------------------------------------------------------------


class DuplicateArtifact(BaseModel):
    id: str
    filename: str
    domain: str
    summary: str = ""
    quality_score: float | None = None
    ingested_at: str = ""
    chunk_count: int = 0


class DuplicateGroup(BaseModel):
    content_hash_prefix: str
    similarity: float
    artifacts: list[DuplicateArtifact]


class DuplicatesResponse(BaseModel):
    groups: list[DuplicateGroup]
    total_groups: int


class MergeDuplicatesRequest(BaseModel):
    keep_id: str
    remove_ids: list[str]


class DismissDuplicatesRequest(BaseModel):
    artifact_ids: list[str]


@router.get("/admin/kb/duplicates", response_model=DuplicatesResponse)
async def list_duplicates(min_similarity: float = 0.85):
    """Group artifacts by exact ``content_hash``. Sprint 2 will add fuzzy similarity."""
    import asyncio
    from collections import defaultdict

    neo4j = get_neo4j()
    # Single grouped aggregation off the event loop — returns only duplicate
    # members, not every artifact per domain.
    rows = await asyncio.to_thread(list_duplicate_artifacts, neo4j)
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for art in rows:
        ch = art.get("content_hash") or ""
        if ch:
            by_hash[ch].append(art)

    groups: list[DuplicateGroup] = []
    for ch, arts in by_hash.items():
        if len(arts) < 2:
            continue
        groups.append(DuplicateGroup(
            content_hash_prefix=ch[:12],
            similarity=1.0,
            artifacts=[
                DuplicateArtifact(
                    id=a.get("id", ""),
                    filename=a.get("filename", ""),
                    domain=a.get("domain", ""),
                    summary=a.get("summary", "") or "",
                    quality_score=a.get("quality_score"),
                    ingested_at=a.get("ingested_at", "") or "",
                    chunk_count=int(a.get("chunk_count", 0) or 0),
                )
                for a in arts
            ],
        ))
    return DuplicatesResponse(groups=groups, total_groups=len(groups))


@router.post("/admin/kb/duplicates/merge", response_model=MergeDuplicatesResponse)
async def merge_duplicates(req: MergeDuplicatesRequest):
    """Delete the ``remove_ids`` artifacts, keeping ``keep_id``."""
    from app.services.content_lifecycle import remove_content

    neo4j = get_neo4j()
    merged = 0
    for art_id in req.remove_ids:
        if art_id == req.keep_id:
            continue
        try:
            # HARD delete through the coordinator: fans chunk removal across
            # Chroma + BM25 + SPLADE (was Neo4j-only, discarding chunk_ids) and
            # busts the query caches.
            remove_content(art_id, neo4j=neo4j)
            merged += 1
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("app.routers.kb_admin.merge_duplicates", exc)
    return {"status": "ok", "merged": merged}


@router.post("/admin/kb/duplicates/dismiss", response_model=DismissDuplicatesResponse)
async def dismiss_duplicates(req: DismissDuplicatesRequest):
    """Mark a duplicate group as dismissed (Sprint 2: persist to filter future fetches).

    No-op for now; treated as acknowledged so the UI can hide the group locally.
    """
    return {"status": "ok", "dismissed": len(req.artifact_ids)}


@router.get("/admin/kb/stats", response_model=KBStatsResponse)
async def kb_stats():
    """Get KB statistics: artifact counts, chunk counts, per-domain breakdown."""
    import asyncio

    try:
        neo4j = get_neo4j()
        chroma = get_chroma()

        total_artifacts = 0
        total_chunks = 0
        domain_stats: dict[str, Any] = {}

        # Per-domain artifact count / avg-quality / synopsis-candidate count
        # via a single grouped Cypher aggregation off the event loop, instead
        # of pulling up to 10k full records per domain into memory.
        dom_stats = await asyncio.to_thread(domain_artifact_stats, neo4j)

        for domain in config.DOMAINS:
            ds = dom_stats.get(
                domain,
                {"artifacts": 0, "avg_quality": 0.0, "synopsis_candidates": 0},
            )
            artifact_count = ds["artifacts"]
            total_artifacts += artifact_count

            chunk_count = 0
            coll_name = config.collection_name(domain)
            try:
                collection = chroma.get_collection(name=coll_name)
                chunk_count = collection.count()
            except Exception as exc:
                log_swallowed_error(
                    "app.routers.kb_admin.stats_chroma_collection_count", exc
                )
            total_chunks += chunk_count

            domain_stats[domain] = {
                "artifacts": artifact_count,
                "chunks": chunk_count,
                "avg_quality": ds["avg_quality"],
                "synopsis_candidates": ds["synopsis_candidates"],
            }

        return KBStatsResponse(
            total_artifacts=total_artifacts,
            total_chunks=total_chunks,
            domains=domain_stats,
        )
    except Exception as e:
        logger.error("Failed to get KB stats: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get KB stats: {e}")
