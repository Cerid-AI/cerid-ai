# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Artifact listing and recategorization endpoints."""
from __future__ import annotations

import json
import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from deps import get_chroma, get_neo4j, get_redis
from utils import cache, graph
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
    except Exception as e:
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


@router.get("/artifacts/{artifact_id}/related")
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
    except Exception as e:
        logger.error(f"Related artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts/{artifact_id}")
async def artifact_detail_endpoint(artifact_id: str):
    """Fetch full artifact content: Neo4j metadata + reassembled ChromaDB chunks."""
    try:
        driver = get_neo4j()
        chroma = get_chroma()

        artifact = graph.get_artifact(driver, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

        chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
        chunks = []
        total_content = ""

        if chunk_ids:
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
            total_content = "\n\n".join(c["text"] for c in raw_chunks)

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
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Artifact detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/artifacts")
async def list_artifacts_endpoint(
    domain: str | None = Query(None, description="Filter by domain"),
    sub_category: str | None = Query(None, description="Filter by sub-category"),
    tag: str | None = Query(None, description="Filter by tag"),
    limit: int = Query(50, ge=1, le=500),
):
    try:
        driver = get_neo4j()
        return graph.list_artifacts(
            driver, domain=domain, sub_category=sub_category, tag=tag, limit=limit,
        )
    except Exception as e:
        logger.error(f"List artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recategorize")
async def recategorize_endpoint(req: RecategorizeRequest):
    try:
        return recategorize(req.artifact_id, req.new_domain, req.sub_category, req.tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Recategorize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
