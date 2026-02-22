"""Artifact listing and recategorization endpoints."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from deps import get_chroma, get_neo4j, get_redis
from utils import cache, graph

router = APIRouter()
logger = logging.getLogger("ai-companion")


def recategorize(artifact_id: str, new_domain: str, tags: str = "") -> Dict:
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
        name=f"domain_{old_domain.replace(' ', '_').lower()}"
    )
    fetched = source_collection.get(ids=chunk_ids, include=["documents", "metadatas"])

    if not fetched["ids"]:
        raise ValueError(f"No chunks found in ChromaDB for artifact {artifact_id}")

    dest_collection = chroma.get_or_create_collection(
        name=f"domain_{new_domain.replace(' ', '_').lower()}"
    )
    updated_metadatas = []
    for meta in fetched["metadatas"]:
        meta = dict(meta)
        meta["domain"] = new_domain
        meta["recategorized_at"] = datetime.utcnow().isoformat()
        if tags:
            meta["tags"] = tags
        updated_metadatas.append(meta)

    dest_collection.add(
        ids=fetched["ids"],
        documents=fetched["documents"],
        metadatas=updated_metadatas,
    )
    source_collection.delete(ids=chunk_ids)

    domains = graph.recategorize_artifact(driver, artifact_id, new_domain)

    try:
        cache.log_event(
            get_redis(),
            event_type="recategorize",
            artifact_id=artifact_id,
            domain=new_domain,
            filename=artifact.get("filename", ""),
            extra={"old_domain": old_domain},
        )
    except Exception as e:
        logger.error(f"Redis log failed: {e}")

    return {
        "status": "success",
        "artifact_id": artifact_id,
        "old_domain": domains["old_domain"],
        "new_domain": domains["new_domain"],
        "chunks_moved": len(chunk_ids),
    }


class RecategorizeRequest(BaseModel):
    artifact_id: str
    new_domain: str
    tags: str = ""


@router.get("/artifacts")
async def list_artifacts_endpoint(
    domain: Optional[str] = Query(None, description="Filter by domain"),
    limit: int = Query(50, ge=1, le=500),
):
    try:
        driver = get_neo4j()
        return graph.list_artifacts(driver, domain=domain, limit=limit)
    except Exception as e:
        logger.error(f"List artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recategorize")
async def recategorize_endpoint(req: RecategorizeRequest):
    try:
        return recategorize(req.artifact_id, req.new_domain, req.tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Recategorize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
