# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory Extraction Agent — extracts facts, decisions, and preferences from conversations."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

import httpx

import config
from utils.bifrost import call_bifrost, extract_content
from utils.cache import log_event
from utils.circuit_breaker import CircuitOpenError
from utils.llm_parsing import parse_llm_json
from utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.memory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_RESPONSE_LENGTH = 100
MEMORY_TYPES = {"fact", "decision", "preference", "action_item"}


# ---------------------------------------------------------------------------
# Memory extraction via LLM
# ---------------------------------------------------------------------------

async def extract_memories(
    response_text: str,
    conversation_id: str,
    model: str = "",
) -> list[dict[str, Any]]:
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
        data = await call_bifrost(
            [{"role": "user", "content": prompt}],
            breaker_name="bifrost-memory",
            temperature=0.1,
            max_tokens=1000,
        )
        content = extract_content(data)
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

    except CircuitOpenError:
        logger.warning("Bifrost memory circuit open, skipping memory extraction")
        return []
    except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
        logger.warning("Memory extraction LLM call failed: %s", e)
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
) -> dict[str, Any]:
    """Extract memories and store each as a KB artifact in the conversations domain.

    When memory consolidation is enabled (default), each extracted memory is
    compared against existing memories to avoid duplicates and track superseded
    information.
    """
    if not config.ENABLE_MEMORY_EXTRACTION:
        return {"status": "skipped", "reason": "Memory extraction disabled"}

    memories = await extract_memories(response_text, conversation_id, model)

    if not memories:
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "memories_extracted": 0,
            "memories_stored": 0,
            "skipped_duplicates": 0,
            "results": [],
        }

    from services.ingestion import ingest_content

    # Import consolidation only when enabled (avoids import cost when disabled)
    consolidation_enabled = False
    classify_memory: Any = None
    mark_superseded: Any = None
    try:
        from config.features import FEATURE_TOGGLES
        consolidation_enabled = FEATURE_TOGGLES.get("enable_memory_consolidation", False)
        if consolidation_enabled:
            from utils.memory_consolidation import (
                classify_memory,
                mark_superseded,
            )
    except ImportError:
        pass

    results = []
    stored_count = 0
    skipped_count = 0

    for idx, mem in enumerate(memories):
        try:
            # Consolidation check: ADD / UPDATE / NOOP
            action_label = "ADD"
            supersede_target = None

            if consolidation_enabled and classify_memory is not None:
                action = await classify_memory(
                    mem["content"],
                    chroma_client=chroma_client,
                    memory_type=mem["memory_type"],
                )
                action_label = action.action

                if action_label == "NOOP":
                    logger.debug(
                        "Memory consolidation: NOOP — %s", action.reason,
                    )
                    skipped_count += 1
                    results.append({
                        "memory_type": mem["memory_type"],
                        "summary": mem["summary"],
                        "status": "skipped_duplicate",
                        "reason": action.reason,
                    })
                    continue

                if action_label == "UPDATE":
                    supersede_target = action.target_id

            convo_prefix = conversation_id[:8] if conversation_id else "unknown"
            timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"memory_{mem['memory_type']}_{convo_prefix}_{timestamp}_{idx}"

            metadata = {
                "filename": filename,
                "conversation_id": conversation_id,
                "model": model,
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "valid_from": utcnow_iso(),
            }

            result = ingest_content(mem["content"], "conversations", metadata=metadata)

            if result.get("status") == "success":
                stored_count += 1
                new_artifact_id = result.get("artifact_id", "")

                # Mark superseded memory if this was an UPDATE
                if (
                    action_label == "UPDATE"
                    and supersede_target
                    and neo4j_driver
                    and new_artifact_id
                    and mark_superseded is not None
                ):
                    mark_superseded(neo4j_driver, supersede_target, new_artifact_id)

                if redis_client:
                    try:
                        log_event(
                            redis_client,
                            event_type="memory_extraction",
                            artifact_id=new_artifact_id,
                            domain="conversations",
                            filename=filename,
                            conversation_id=conversation_id,
                            extra={
                                "memory_type": mem["memory_type"],
                                "consolidation_action": action_label,
                            },
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log memory extraction event: {e}")

                if neo4j_driver and new_artifact_id:
                    try:
                        with neo4j_driver.session() as session:
                            session.run(
                                "MATCH (m:Artifact {id: $memory_id}) "
                                "MERGE (c:Conversation {id: $convo_id}) "
                                "MERGE (m)-[:EXTRACTED_FROM]->(c)",
                                memory_id=new_artifact_id,
                                convo_id=conversation_id,
                            )
                    except Exception as e:  # Neo4j driver exceptions vary by version
                        logger.warning("Failed to create EXTRACTED_FROM relationship: %s", e)

            results.append({
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "status": result.get("status", "error"),
                "artifact_id": result.get("artifact_id", ""),
                "consolidation_action": action_label,
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
        "skipped_duplicates": skipped_count,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Retention / archival
# ---------------------------------------------------------------------------

async def archive_old_memories(
    neo4j_driver,
    retention_days: int | None = None,
) -> dict[str, Any]:
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
    except Exception as e:  # Neo4j driver exceptions vary by version
        logger.error("Memory archival failed: %s", e)
        return {
            "timestamp": utcnow_iso(),
            "retention_days": retention_days,
            "error": str(e),
            "archived_count": 0,
        }
