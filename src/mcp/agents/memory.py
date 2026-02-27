# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory Extraction Agent — extracts facts, decisions, and preferences from conversations."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import httpx

import config
from utils.llm_parsing import parse_llm_json
from utils.cache import log_event
from utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.memory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_RESPONSE_LENGTH = 100  # skip trivially short responses
MEMORY_TYPES = {"fact", "decision", "preference", "action_item"}


# ---------------------------------------------------------------------------
# Memory extraction via LLM
# ---------------------------------------------------------------------------

async def extract_memories(
    response_text: str,
    conversation_id: str,
    model: str = "",
) -> List[Dict[str, Any]]:
    """Use a lightweight LLM to extract memorable content from a response."""
    if len(response_text) < MIN_RESPONSE_LENGTH:
        return []

    prompt = (
        "Analyze this assistant response and extract any memorable content. "
        "For each item, classify it as one of: fact, decision, preference, action_item.\n\n"
        "Return ONLY a JSON array of objects with keys: content, memory_type, summary.\n"
        "- content: the full extractable text\n"
        "- memory_type: one of fact/decision/preference/action_item\n"
        "- summary: a one-line summary (max 100 chars)\n\n"
        "If nothing is worth extracting, return an empty array [].\n\n"
        f"Response:\n{response_text[:3000]}\n\n"
        "JSON array:"
    )

    try:
        async with httpx.AsyncClient(timeout=config.BIFROST_TIMEOUT) as client:
            resp = await client.post(
                f"{config.BIFROST_URL}/chat/completions",
                json={
                    "model": config.LLM_INTERNAL_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 1000,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            memories = parse_llm_json(content)
            if not isinstance(memories, list):
                return []

            valid = []
            for m in memories:
                if not isinstance(m, dict):
                    continue
                mem_type = m.get("memory_type", "fact")
                if mem_type not in MEMORY_TYPES:
                    mem_type = "fact"
                valid.append({
                    "content": str(m.get("content", ""))[:2000],
                    "memory_type": mem_type,
                    "summary": str(m.get("summary", ""))[:100],
                })
            return valid

    except Exception as e:
        logger.warning(f"Memory extraction LLM call failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Store extracted memories
# ---------------------------------------------------------------------------

async def extract_and_store_memories(
    response_text: str,
    conversation_id: str,
    model: str = "",
    chroma_client=None,
    neo4j_driver=None,
    redis_client=None,
) -> Dict[str, Any]:
    """Extract memories and store each as a KB artifact in the conversations domain."""
    if not config.ENABLE_MEMORY_EXTRACTION:
        return {"status": "skipped", "reason": "Memory extraction disabled"}

    memories = await extract_memories(response_text, conversation_id, model)

    if not memories:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "memories_extracted": 0,
            "memories_stored": 0,
            "results": [],
        }

    from services.ingestion import ingest_content

    results = []
    stored_count = 0

    for idx, mem in enumerate(memories):
        try:
            convo_prefix = conversation_id[:8] if conversation_id else "unknown"
            timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"memory_{mem['memory_type']}_{convo_prefix}_{timestamp}_{idx}"

            metadata = {
                "filename": filename,
                "conversation_id": conversation_id,
                "model": model,
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
            }

            result = ingest_content(mem["content"], "conversations", metadata=metadata)

            if result.get("status") == "success":
                stored_count += 1

                if redis_client:
                    try:
                        log_event(
                            redis_client,
                            event_type="memory_extraction",
                            artifact_id=result.get("artifact_id", ""),
                            domain="conversations",
                            filename=filename,
                            conversation_id=conversation_id,
                            extra={"memory_type": mem["memory_type"]},
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log memory extraction event: {e}")

                if neo4j_driver and result.get("artifact_id"):
                    try:
                        with neo4j_driver.session() as session:
                            session.run(
                                "MATCH (m:Artifact {id: $memory_id}) "
                                "MERGE (c:Conversation {id: $convo_id}) "
                                "MERGE (m)-[:EXTRACTED_FROM]->(c)",
                                memory_id=result["artifact_id"],
                                convo_id=conversation_id,
                            )
                    except Exception as e:
                        logger.warning(f"Failed to create EXTRACTED_FROM relationship: {e}")

            results.append({
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "status": result.get("status", "error"),
                "artifact_id": result.get("artifact_id", ""),
            })
        except Exception as e:
            logger.warning(f"Failed to store memory: {e}")
            results.append({
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "status": "error",
                "error": str(e),
            })

    return {
        "conversation_id": conversation_id,
        "timestamp": utcnow_iso(),
        "memories_extracted": len(memories),
        "memories_stored": stored_count,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Retention / archival
# ---------------------------------------------------------------------------

async def archive_old_memories(
    neo4j_driver,
    retention_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Mark old conversation memories as archived (deprioritized in search)."""
    if retention_days is None:
        retention_days = config.MEMORY_RETENTION_DAYS

    cutoff = (utcnow().replace(tzinfo=None) - timedelta(days=retention_days)).isoformat()

    try:
        with neo4j_driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact)-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
                "WHERE a.ingested_at < $cutoff AND NOT coalesce(a.archived, false) "
                "SET a.archived = true, a.archived_at = $now "
                "RETURN count(a) AS archived_count",
                cutoff=cutoff,
                now=utcnow_iso(),
            )
            record = result.single()
            archived_count = record["archived_count"] if record else 0

        return {
            "timestamp": utcnow_iso(),
            "retention_days": retention_days,
            "cutoff_date": cutoff,
            "archived_count": archived_count,
        }
    except Exception as e:
        logger.error(f"Memory archival failed: {e}")
        return {
            "timestamp": utcnow_iso(),
            "retention_days": retention_days,
            "error": str(e),
            "archived_count": 0,
        }