# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Artifact listing and recategorization endpoints."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from db import neo4j as graph
from deps import get_chroma, get_neo4j, get_redis
from errors import RetrievalError
from models.artifacts import (
    ArtifactDetail,
    ArtifactFeedbackResponse,
    ArtifactSummary,
    RecategorizeResponse,
    RelatedArtifact,
)
from utils import cache
from utils.time import utcnow_iso

router = APIRouter()
logger = logging.getLogger("ai-companion")


def recategorize(
    artifact_id: str,
    new_domain: str,
    sub_category: str = "",
    tags: str = "",
) -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    if new_domain not in config.DOMAINS:
        raise ValueError(f"Invalid domain: {new_domain}. Valid: {config.DOMAINS}")

    driver = get_neo4j()
    chroma = get_chroma()

    artifact = graph.get_artifact(driver, artifact_id)
    if not artifact:
        raise ValueError(f"Artifact not found: {artifact_id}")

    old_domain = artifact["domain"]
    if old_domain == new_domain:
        raise ValueError(f"Artifact already in domain '{new_domain}'")

    chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
    if not chunk_ids:
        raise ValueError(f"No chunk IDs found for artifact {artifact_id}")

    source_collection = chroma.get_or_create_collection(
        name=config.collection_name(old_domain)
    )
    fetched = source_collection.get(ids=chunk_ids, include=["documents", "metadatas"])

    if not fetched["ids"]:
        raise ValueError(f"No chunks found in ChromaDB for artifact {artifact_id}")

    dest_collection = chroma.get_or_create_collection(
        name=config.collection_name(new_domain)
    )
    updated_metadatas = []
    for meta in fetched["metadatas"]:
        meta = dict(meta)
        meta["domain"] = new_domain
        meta["recategorized_at"] = utcnow_iso()
        if sub_category:
            meta["sub_category"] = sub_category
        if tags:
            meta["tags"] = tags
        updated_metadatas.append(meta)

    # Update Neo4j first — if this fails, ChromaDB remains consistent
    domains = graph.recategorize_artifact(driver, artifact_id, new_domain)

    # Update taxonomy if sub_category or tags provided
    if sub_category or tags:
        tags_json = None
        if tags:
            if tags.startswith("["):
                tags_json = tags
            else:
                tags_json = json.dumps([t.strip().lower() for t in tags.split(",") if t.strip()])
        graph.update_artifact_taxonomy(
            driver,
            artifact_id,
            sub_category=sub_category or config.DEFAULT_SUB_CATEGORY,
            tags_json=tags_json,
        )

    dest_collection.add(
        ids=fetched["ids"],
        documents=fetched["documents"],
        metadatas=updated_metadatas,
    )
    source_collection.delete(ids=chunk_ids)

    try:
        cache.log_event(
            get_redis(),
            event_type="recategorize",
            artifact_id=artifact_id,
            domain=new_domain,
            filename=artifact.get("filename", ""),
            extra={"old_domain": old_domain, "sub_category": sub_category},
        )
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Redis log failed: {e}")

    return {
        "status": "success",
        "artifact_id": artifact_id,
        "old_domain": domains["old_domain"],
        "new_domain": domains["new_domain"],
        "sub_category": sub_category or config.DEFAULT_SUB_CATEGORY,
        "chunks_moved": len(chunk_ids),
    }


class RecategorizeRequest(BaseModel):
    artifact_id: str
    new_domain: str
    sub_category: str = ""
    tags: str = ""


@router.get("/artifacts/{artifact_id}/related", response_model=list[RelatedArtifact])
async def related_artifacts_endpoint(
    artifact_id: str,
    depth: int = Query(2, ge=1, le=4),
    max_results: int = Query(5, ge=1, le=20),
):
    """Get artifacts related to the given artifact via knowledge graph traversal."""
    try:
        driver = get_neo4j()
        return graph.find_related_artifacts(
            driver, artifact_ids=[artifact_id], depth=depth, max_results=max_results
        )
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Related artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDetail)
async def artifact_detail_endpoint(artifact_id: str):
    """Fetch full artifact content: Neo4j metadata + reassembled ChromaDB chunks."""
    try:
        driver = get_neo4j()
        chroma = get_chroma()

        # Handle external:* artifact IDs gracefully
        if artifact_id.startswith("external:"):
            return {
                "artifact_id": artifact_id,
                "title": artifact_id,
                "domain": "external",
                "filename": "",
                "source_type": "external",
                "chunk_count": 0,
                "total_content": "External source — no preview available",
                "chunks": [],
                "metadata": {},
            }

        artifact = graph.get_artifact(driver, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

        # Parse chunk_ids with error handling for malformed JSON
        try:
            chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
        except (json.JSONDecodeError, TypeError):
            chunk_ids = []

        chunks = []
        total_content = ""

        if chunk_ids:
            try:
                collection = chroma.get_or_create_collection(
                    name=config.collection_name(artifact["domain"])
                )
                fetched = collection.get(ids=chunk_ids, include=["documents", "metadatas"])

                # Build sorted chunks by chunk_index
                raw_chunks = []
                for i, doc_id in enumerate(fetched["ids"]):
                    meta = fetched["metadatas"][i] if fetched["metadatas"] else {}
                    text = fetched["documents"][i] if fetched["documents"] else ""
                    idx = int(meta.get("chunk_index", i))
                    raw_chunks.append({"index": idx, "text": text})

                raw_chunks.sort(key=lambda c: c["index"])
                chunks = raw_chunks
                total_content = "\n\n".join(str(c["text"]) for c in raw_chunks)
            except (ValueError, KeyError, RuntimeError) as chunk_err:
                logger.warning(f"Chunk reassembly failed for {artifact_id}: {chunk_err}")
                # Fallback: use summary as preview content
                total_content = artifact.get("summary", "Preview unavailable — chunk reassembly failed")

        # Fallback when no chunks and no content reassembled
        if not total_content:
            total_content = artifact.get("summary", "")

        return {
            "artifact_id": artifact["id"],
            "title": artifact.get("filename", ""),
            "domain": artifact["domain"],
            "filename": artifact.get("filename", ""),
            "source_type": artifact.get("source_type", ""),
            "chunk_count": artifact.get("chunk_count", len(chunks)),
            "total_content": total_content,
            "chunks": chunks,
            "metadata": {
                "sub_category": artifact.get("sub_category", ""),
                "tags": artifact.get("tags", "[]"),
                "keywords": artifact.get("keywords", ""),
                "summary": artifact.get("summary", ""),
                "ingested_at": artifact.get("ingested_at", ""),
                "recategorized_at": artifact.get("recategorized_at"),
                "starred": artifact.get("starred", False),
                "evergreen": artifact.get("evergreen", False),
                "retrieval_count": artifact.get("retrieval_count", 0),
            },
        }
    except HTTPException:
        raise
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Artifact detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ArtifactUpdateRequest(BaseModel):
    title: str | None = None
    summary: str | None = None


@router.patch("/artifacts/{artifact_id}")
async def update_artifact_fields(artifact_id: str, body: ArtifactUpdateRequest):
    """Update mutable fields on an artifact (title, summary)."""
    try:
        driver = get_neo4j()
        artifact = graph.get_artifact(driver, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if body.title is not None:
            graph.update_artifact(driver, artifact_id, {"filename": body.title})
        if body.summary is not None:
            graph.update_artifact_summary(driver, artifact_id, body.summary)
        return {"artifact_id": artifact_id, "updated": True}
    except HTTPException:
        raise
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Artifact update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/artifacts/{artifact_id}/star")
async def toggle_artifact_star(artifact_id: str):
    """Toggle the starred flag on an artifact."""
    try:
        driver = get_neo4j()
        new_val = graph.toggle_starred(driver, artifact_id)
        return {"artifact_id": artifact_id, "starred": new_val}
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Toggle star error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/artifacts/{artifact_id}/evergreen")
async def toggle_artifact_evergreen(artifact_id: str):
    """Toggle the evergreen flag on an artifact."""
    try:
        driver = get_neo4j()
        new_val = graph.toggle_evergreen(driver, artifact_id)
        return {"artifact_id": artifact_id, "evergreen": new_val}
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Toggle evergreen error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts", response_model=list[ArtifactSummary])
async def list_artifacts_endpoint(
    domain: str | None = Query(None, description="Filter by domain"),
    sub_category: str | None = Query(None, description="Filter by sub-category"),
    tag: str | None = Query(None, description="Filter by tag"),
    client_source: str | None = Query(None, description="Filter by ingestion client (e.g. 'gui', 'trading-agent')"),
    since: str | None = Query(None, description="ISO date — only return artifacts ingested after this date"),
    min_quality: float | None = Query(None, ge=0, le=1, description="Minimum quality score (0-1)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=500),
):
    try:
        driver = get_neo4j()
        return graph.list_artifacts(
            driver,
            domain=domain,
            sub_category=sub_category,
            tag=tag,
            client_source=client_source,
            since=since,
            min_quality=min_quality,
            offset=offset,
            limit=limit,
        )
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"List artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recategorize", response_model=RecategorizeResponse)
async def recategorize_endpoint(req: RecategorizeRequest):
    try:
        return recategorize(req.artifact_id, req.new_domain, req.sub_category, req.tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Recategorize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Adaptive quality feedback
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    signal: str  # "inject" or "dismiss"
    query: str = ""  # the query that surfaced this artifact


# Quality score adjustment amounts
_INJECT_BOOST = 0.05
_DISMISS_PENALTY = 0.03


@router.post("/artifacts/{artifact_id}/feedback", response_model=ArtifactFeedbackResponse)
async def artifact_feedback_endpoint(artifact_id: str, req: FeedbackRequest):
    """Record user inject/dismiss feedback and adjust artifact quality score.

    When a user injects a KB result into their chat, its quality score gets a
    small boost. When dismissed, it gets a small penalty. This creates an
    adaptive loop where frequently-useful artifacts rise in quality-weighted
    retrieval rankings.
    """
    if req.signal not in ("inject", "dismiss"):
        raise HTTPException(status_code=400, detail="signal must be 'inject' or 'dismiss'")

    try:
        driver = get_neo4j()
        artifact = graph.get_artifact(driver, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

        current_score = float(artifact.get("quality_score", 0.5))
        if req.signal == "inject":
            new_score = min(1.0, current_score + _INJECT_BOOST)
        else:
            new_score = max(0.0, current_score - _DISMISS_PENALTY)

        # Update quality_score on the Neo4j node
        with driver.session() as session:
            session.run(
                "MATCH (a:Artifact {id: $aid}) "
                "SET a.quality_score = $score, a.quality_scored_at = $now",
                aid=artifact_id, score=round(new_score, 4), now=utcnow_iso(),
            )

        # Log feedback to Redis for analytics
        try:
            cache.log_event(
                get_redis(),
                event_type="quality_feedback",
                artifact_id=artifact_id,
                domain=artifact.get("domain", ""),
                filename=artifact.get("filename", ""),
                extra={
                    "signal": req.signal,
                    "query": req.query[:200] if req.query else "",
                    "old_score": round(current_score, 4),
                    "new_score": round(new_score, 4),
                },
            )
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Feedback Redis log failed: {e}")

        return {
            "status": "ok",
            "artifact_id": artifact_id,
            "signal": req.signal,
            "old_score": round(current_score, 4),
            "new_score": round(new_score, 4),
        }
    except HTTPException:
        raise
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Artifact feedback error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/artifacts/{artifact_id}/regenerate-synopsis")
async def regenerate_synopsis(artifact_id: str):
    """Re-run metadata extraction for an artifact to regenerate its synopsis."""
    driver = get_neo4j()
    artifact = graph.get_artifact(driver, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Get content from ChromaDB
    try:
        chroma = get_chroma()
        domain = artifact.get("domain", config.DEFAULT_DOMAIN)
        coll = chroma.get_or_create_collection(name=config.collection_name(domain))
        results = coll.get(where={"artifact_id": artifact_id}, limit=1, include=["documents"])
        content = results["documents"][0] if results["documents"] else ""
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        content = ""

    if not content:
        raise HTTPException(status_code=400, detail="No content found for this artifact")

    from utils.metadata import extract_metadata
    filename = artifact.get("filename", "")
    meta = extract_metadata(content, filename, domain)
    summary = meta.get("summary", content[:200].strip())

    # Update Neo4j
    graph.update_artifact_summary(driver, artifact_id, summary)

    return {"artifact_id": artifact_id, "summary": summary, "method": "llm" if meta.get("summary") else "fallback"}
