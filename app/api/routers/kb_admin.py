# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""KB Administration endpoints — rebuild indexes, rescore, regenerate summaries, clear domains, delete artifacts."""
from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import config
from agents.curator import _is_truncated_summary, curate
from config.features import CERID_MULTI_USER
from db.neo4j.artifacts import delete_artifact, list_artifacts
from deps import get_chroma, get_neo4j
from utils.bm25 import rebuild_all as rebuild_bm25_all
from utils.query_cache import invalidate_cache_non_blocking

logger = logging.getLogger("ai-companion.kb-admin")


def _require_admin(request: Request) -> None:
    """Block non-admin users in multi-user mode. No-op in single-user."""
    if not CERID_MULTI_USER:
        return
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


router = APIRouter(tags=["kb-admin"], dependencies=[Depends(_require_admin)])


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/admin/kb/rebuild-index", response_model=RebuildIndexResponse)
async def rebuild_indexes():
    """Rebuild BM25 indexes for all domains from disk."""
    try:
        rebuilt = rebuild_bm25_all()
        await invalidate_cache_non_blocking()
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
        return {
            "synopses_generated": result["synopses_generated"],
            "artifacts_scored": result["artifacts_scored"],
            "message": f"Generated {result['synopses_generated']} synopses, scored {result['artifacts_scored']} artifacts",
        }
    except Exception as e:
        logger.error("Failed to regenerate summaries: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to regenerate summaries: {e}")


@router.post("/admin/kb/clear-domain/{domain}")
async def clear_domain(domain: str, req: ClearDomainRequest):
    """Clear all artifacts in a specific domain. Requires confirm=true."""
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Must set confirm=true to clear domain")

    if domain not in config.DOMAINS:
        raise HTTPException(status_code=404, detail=f"Unknown domain: {domain}")

    try:
        neo4j = get_neo4j()
        chroma = get_chroma()
        artifacts = list_artifacts(neo4j, domain=domain, limit=10000)

        deleted_count = 0
        chunks_removed = 0

        # Delete Neo4j artifacts first — safer ordering avoids split-brain
        # if the process crashes between the two phases
        for artifact in artifacts:
            try:
                result = delete_artifact(neo4j, artifact["id"])
                if result.get("deleted"):
                    deleted_count += 1
                    chunks_removed += len(result.get("chunk_ids", []))
            except Exception as e:
                logger.warning("Failed to delete artifact %s: %s", artifact["id"][:8], e)

        # Delete ChromaDB collection for the domain
        coll_name = config.collection_name(domain)
        try:
            chroma.delete_collection(name=coll_name)
            logger.info("Deleted ChromaDB collection: %s", coll_name)
        except Exception as e:
            logger.warning("Failed to delete collection %s: %s", coll_name, e)

        await invalidate_cache_non_blocking()

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
                logger.warning("Failed to clean ChromaDB chunks: %s", e)

        await invalidate_cache_non_blocking()

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


@router.get("/admin/kb/stats", response_model=KBStatsResponse)
async def kb_stats():
    """Get KB statistics: artifact counts, chunk counts, per-domain breakdown."""
    try:
        neo4j = get_neo4j()
        chroma = get_chroma()

        total_artifacts = 0
        total_chunks = 0
        domain_stats: dict[str, Any] = {}

        for domain in config.DOMAINS:
            artifacts = list_artifacts(neo4j, domain=domain, limit=10000)
            artifact_count = len(artifacts)
            total_artifacts += artifact_count

            chunk_count = 0
            coll_name = config.collection_name(domain)
            try:
                collection = chroma.get_collection(name=coll_name)
                chunk_count = collection.count()
            except Exception:
                pass
            total_chunks += chunk_count

            # Count synopsis candidates
            synopsis_candidates = sum(
                1 for a in artifacts if _is_truncated_summary(a.get("summary", ""))
            )

            # Avg quality score
            scores = [a.get("quality_score", 0) for a in artifacts if a.get("quality_score") is not None]
            avg_quality = round(sum(scores) / len(scores), 4) if scores else 0.0

            domain_stats[domain] = {
                "artifacts": artifact_count,
                "chunks": chunk_count,
                "avg_quality": avg_quality,
                "synopsis_candidates": synopsis_candidates,
            }

        return KBStatsResponse(
            total_artifacts=total_artifacts,
            total_chunks=total_chunks,
            domains=domain_stats,
        )
    except Exception as e:
        logger.error("Failed to get KB stats: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get KB stats: {e}")
