# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memories API — browse, edit, and delete extracted conversation memories."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from deps import get_chroma, get_neo4j, get_redis
from errors import RetrievalError
from utils import cache

router = APIRouter()
logger = logging.getLogger("ai-companion")

CONVERSATIONS_COLLECTION = "domain_conversations"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MemoryUpdateRequest(BaseModel):
    summary: str


class MemoryExtractRequest(BaseModel):
    conversation_id: str
    messages: list[dict]


# ---------------------------------------------------------------------------
# GET /memories — list memories with filtering
# ---------------------------------------------------------------------------

@router.get("/memories")
async def list_memories(
    type: str | None = Query(None, description="Filter by memory type (facts/decisions/preferences/action-items)"),
    conversation_id: str | None = Query(None, description="Filter by conversation ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List extracted conversation memories with optional filtering."""
    try:
        driver = get_neo4j()

        # Build Cypher query with optional filters
        base_query = (
            "MATCH (a:Artifact)-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
        )
        conditions = []
        params: dict = {"limit": limit, "offset": offset}

        conditions.append("a.filename STARTS WITH 'memory_'")
        # Exclude non-GUI client sources (e.g. trading-agent ingested items)
        conditions.append(
            "(a.client_source IS NULL OR a.client_source = '' OR a.client_source = 'gui')"
        )
        memory_type = None

        if type:
            # Map plural/hyphenated API names to stored memory_type values
            type_map = {
                "facts": "fact",
                "fact": "fact",
                "decisions": "decision",
                "decision": "decision",
                "preferences": "preference",
                "preference": "preference",
                "action-items": "action_item",
                "action_items": "action_item",
                "action_item": "action_item",
            }
            memory_type = type_map.get(type.lower())
            if not memory_type:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid memory type: {type}. Valid: facts, decisions, preferences, action-items",
                )
            conditions.append("a.filename STARTS WITH $memory_type_prefix")
            params["memory_type_prefix"] = f"memory_{memory_type}_"

        if conversation_id:
            convo_prefix = conversation_id[:8]
            conditions.append("a.filename CONTAINS $convo_prefix")
            params["convo_prefix"] = convo_prefix

        if conditions:
            base_query += "WHERE " + " AND ".join(conditions) + " "

        base_query += (
            "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
            "a.summary AS summary, a.ingested_at AS created_at, "
            "a.chunk_ids AS chunk_ids "
            "ORDER BY a.ingested_at DESC "
            "SKIP $offset LIMIT $limit"
        )

        with driver.session() as session:
            result = session.run(base_query, **params)
            memories = []
            for record in result:
                # Extract memory_type and conversation_id from filename pattern:
                # memory_{type}_{convo_prefix}_{timestamp}_{idx}
                filename = record["filename"] or ""
                parts = filename.split("_")
                memory_type = parts[1] if len(parts) > 1 else "unknown"
                convo_id_part = parts[2] if len(parts) > 2 else ""

                memories.append({
                    "id": record["id"],
                    "type": memory_type,
                    "content": record["summary"] or "",
                    "conversation_id": convo_id_part,
                    "created_at": record["created_at"],
                    "source_filename": filename,
                })

        # Get total count for pagination
        count_conditions = [
            "a.filename STARTS WITH 'memory_'",
            "(a.client_source IS NULL OR a.client_source = '' OR a.client_source = 'gui')",
        ]
        if type and memory_type:
            count_conditions.append("a.filename STARTS WITH $memory_type_prefix")
        if conversation_id:
            count_conditions.append("a.filename CONTAINS $convo_prefix")
        count_query = (
            "MATCH (a:Artifact)-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
            "WHERE " + " AND ".join(count_conditions) + " "
            "RETURN count(a) AS total"
        )
        with driver.session() as count_session:
            total = count_session.run(count_query, **params).single()["total"]

        return {"memories": memories, "total": total, "limit": limit, "offset": offset}

    except HTTPException:
        raise
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"List memories error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# PATCH /memories/{memory_id} — edit a memory's summary
# ---------------------------------------------------------------------------

@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, req: MemoryUpdateRequest):
    """Update a memory's summary text."""
    try:
        driver = get_neo4j()

        with driver.session() as session:
            # Verify the memory exists and is in the conversations domain
            check = session.run(
                "MATCH (a:Artifact {id: $memory_id})-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
                "WHERE a.filename STARTS WITH 'memory_' "
                "RETURN a.id AS id, a.filename AS filename, a.summary AS summary, "
                "a.ingested_at AS created_at",
                memory_id=memory_id,
            )
            record = check.single()
            if not record:
                raise HTTPException(status_code=404, detail=f"Memory not found: {memory_id}")

            session.run(
                "MATCH (a:Artifact {id: $memory_id}) "
                "SET a.summary = $summary",
                memory_id=memory_id,
                summary=req.summary,
            )

        filename = record["filename"] or ""
        parts = filename.split("_")
        memory_type = parts[1] if len(parts) > 1 else "unknown"
        convo_id_part = parts[2] if len(parts) > 2 else ""

        return {
            "id": memory_id,
            "type": memory_type,
            "content": req.summary,
            "conversation_id": convo_id_part,
            "created_at": record["created_at"],
            "source_filename": filename,
        }

    except HTTPException:
        raise
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Update memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# DELETE /memories/{memory_id} — delete a memory
# ---------------------------------------------------------------------------

@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory from Neo4j and its chunks from ChromaDB."""
    try:
        driver = get_neo4j()
        chroma = get_chroma()

        # Fetch the memory artifact and its chunk IDs
        with driver.session() as session:
            check = session.run(
                "MATCH (a:Artifact {id: $memory_id})-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
                "WHERE a.filename STARTS WITH 'memory_' "
                "RETURN a.id AS id, a.chunk_ids AS chunk_ids, a.filename AS filename",
                memory_id=memory_id,
            )
            record = check.single()
            if not record:
                raise HTTPException(status_code=404, detail=f"Memory not found: {memory_id}")

        # Delete chunks from ChromaDB
        chunk_ids_raw = record["chunk_ids"]
        if chunk_ids_raw:
            try:
                chunk_ids = json.loads(chunk_ids_raw)
                if chunk_ids:
                    collection = chroma.get_or_create_collection(name=CONVERSATIONS_COLLECTION)
                    collection.delete(ids=chunk_ids)
                    logger.info(f"Deleted {len(chunk_ids)} chunks from ChromaDB for memory {memory_id[:8]}")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse chunk_ids for memory {memory_id[:8]}: {e}")

        with driver.session() as session:
            session.run(
                "MATCH (a:Artifact {id: $memory_id}) DETACH DELETE a",
                memory_id=memory_id,
            )

        try:
            cache.log_event(
                get_redis(),
                event_type="memory_delete",
                artifact_id=memory_id,
                domain="conversations",
                filename=record["filename"] or "",
            )
        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning(f"Redis audit log failed for memory deletion: {e}")

        return {"status": "deleted", "memory_id": memory_id}

    except HTTPException:
        raise
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Delete memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /memories/extract — trigger memory extraction for a conversation
# ---------------------------------------------------------------------------

@router.post("/memories/extract")
async def extract_memories_endpoint(req: MemoryExtractRequest, request: Request):
    """Trigger memory extraction from conversation messages.

    Private mode (level >= 1): skips extraction entirely to prevent memory persistence.
    """
    # Private mode guard: skip memory extraction
    client_id = request.headers.get("X-Client-ID", "unknown")
    try:
        from utils.private_mode import get_private_mode_level
        if get_private_mode_level(client_id) >= 1:
            return {
                "conversation_id": req.conversation_id,
                "memories_extracted": 0,
                "memories_stored": 0,
                "results": [],
                "note": "Private mode active — memory extraction skipped",
                "private_mode": True,
            }
    except (ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as _pm_err:
        logger.debug("Private mode check skipped: %s", _pm_err)

    try:
        # Build the response text from messages (assistant messages are the
        # primary source for memory extraction)
        response_parts = []
        for msg in req.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "assistant" and content:
                response_parts.append(content)

        if not response_parts:
            return {
                "conversation_id": req.conversation_id,
                "memories_extracted": 0,
                "memories_stored": 0,
                "results": [],
                "note": "No assistant messages found to extract from",
            }

        response_text = "\n\n".join(response_parts)

        from agents.memory import extract_and_store_memories

        return await extract_and_store_memories(
            response_text=response_text,
            conversation_id=req.conversation_id,
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
        )

    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.error(f"Memory extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
