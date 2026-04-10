# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory Extraction Agent — extracts facts, decisions, and preferences from conversations.

Phase 44 additions: conflict detection, LLM conflict resolution, decay/reinforcement
scoring, and context-aware recall with access-count reinforcement.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx

import config
from config.settings import MEMORY_TYPE_MIGRATION
from core.utils.cache import log_event
from core.utils.circuit_breaker import CircuitOpenError
from core.utils.embeddings import l2_distance_to_relevance
from core.utils.internal_llm import call_internal_llm
from core.utils.llm_parsing import parse_llm_json
from core.utils.time import utcnow, utcnow_iso

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
        content = await call_internal_llm(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        memories = parse_llm_json(content)
        # LLM may return a single object instead of an array — normalize
        if isinstance(memories, dict):
            memories = [memories]
        if not isinstance(memories, list):
            return []

        valid = []
        for m in memories:
            if not isinstance(m, dict):
                continue
            mem_type = m.get("memory_type", "fact")
            # Normalize legacy types (e.g. "fact" -> "empirical")
            mem_type = MEMORY_TYPE_MIGRATION.get(mem_type, mem_type)
            if mem_type not in config.MEMORY_TYPES:
                mem_type = "empirical"
            valid.append({
                "content": str(m.get("content", ""))[:2000],
                "memory_type": mem_type,
                "summary": str(m.get("summary", ""))[:100],
            })
        return valid

    except CircuitOpenError:
        logger.warning("Bifrost memory circuit open, skipping memory extraction")
        return []
    except Exception as e:
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
    ingest_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract memories and store each as a KB artifact in the conversations domain.

    When memory consolidation is enabled (default), each extracted memory is
    compared against existing memories to avoid duplicates and track superseded
    information.

    Parameters
    ----------
    ingest_fn : Callable | None
        Callback for ingesting content into the KB. When ``None``, the ingest
        step is skipped and memories are only extracted, not stored.
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

    if ingest_fn is None:
        logger.warning("Memory extraction: ingest_fn not provided, skipping storage")
        return {
            "conversation_id": conversation_id,
            "timestamp": utcnow_iso(),
            "memories_extracted": len(memories),
            "memories_stored": 0,
            "skipped_duplicates": 0,
            "results": [
                {
                    "memory_type": m["memory_type"],
                    "summary": m["summary"],
                    "status": "skipped",
                    "reason": "no ingest function provided",
                }
                for m in memories
            ],
        }

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

            # Phase 44: Conflict detection and resolution
            conflict_resolutions: list[dict] = []
            effective_content = mem["content"]
            if action_label in ("ADD", "UPDATE") and chroma_client:
                try:
                    conflicts = await detect_memory_conflict(
                        mem["content"], chroma_client, neo4j_driver,
                    )
                    for conflict in conflicts:
                        resolution = await resolve_memory_conflict(
                            mem["content"], conflict,
                        )
                        conflict_resolutions.append({
                            "conflict_id": conflict["memory_id"],
                            "similarity": conflict["similarity"],
                            **resolution,
                        })
                        if resolution["action"] == "supersede":
                            supersede_target = conflict["memory_id"]
                            action_label = "UPDATE"
                        elif resolution["action"] == "merge" and resolution.get("merged_text"):
                            effective_content = resolution["merged_text"]
                except Exception as e:
                    logger.debug("Phase 44 conflict detection failed: %s", e)

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
                "access_count": "0",
            }

            result = ingest_fn(effective_content, "conversations", metadata=metadata)

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

            entry = {
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "status": result.get("status", "error"),
                "artifact_id": result.get("artifact_id", ""),
                "consolidation_action": action_label,
            }
            if conflict_resolutions:
                entry["conflict_resolutions"] = conflict_resolutions
            results.append(entry)
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
# Memory Conflict Detection (Phase 44)
# ---------------------------------------------------------------------------


async def detect_memory_conflict(
    new_memory_text: str,
    chroma_client: Any,
    neo4j_driver: Any,
    similarity_threshold: float | None = None,
) -> list[dict]:
    """Find existing memories that conflict with a new memory.

    Embeds the new memory text and searches existing memories at >threshold
    similarity. Returns conflicting memories with their similarity scores.

    Returns:
        [{"memory_id": str, "text": str, "similarity": float, "created_at": str}, ...]
    """
    if similarity_threshold is None:
        similarity_threshold = config.MEMORY_CONFLICT_THRESHOLD

    if chroma_client is None:
        return []

    try:
        coll_name = config.collection_name("conversations")
        collection = chroma_client.get_or_create_collection(name=coll_name)
        results = collection.query(
            query_texts=[new_memory_text],
            n_results=5,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.debug("Conflict detection similarity search failed: %s", e)
        return []

    conflicts: list[dict] = []
    if not results["ids"] or not results["ids"][0]:
        return conflicts

    for i, chunk_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i] if results["distances"] else 1.0
        similarity = l2_distance_to_relevance(distance)
        if similarity >= similarity_threshold:
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            conflicts.append({
                "memory_id": metadata.get("artifact_id", chunk_id),
                "text": results["documents"][0][i] if results["documents"] else "",
                "similarity": round(similarity, 4),
                "created_at": metadata.get("valid_from", metadata.get("ingested_at", "")),
            })

    return conflicts


# ---------------------------------------------------------------------------
# LLM Conflict Resolution (Phase 44)
# ---------------------------------------------------------------------------


async def resolve_memory_conflict(
    new_memory: str,
    existing_memory: dict,
    resolution_model: str | None = None,
) -> dict:
    """Use LLM to classify how to resolve a memory conflict.

    Classifications:
    - supersede: new memory replaces old (e.g., updated phone number)
    - coexist: both memories are valid (e.g., different contexts)
    - merge: combine information from both (e.g., partial overlaps)

    Returns:
        {"action": "supersede"|"coexist"|"merge", "reason": str, "merged_text": str | None}
    """
    existing_text = existing_memory.get("text", "")[:1000]

    prompt = (
        "You are a memory conflict resolver. Two memories have high semantic overlap.\n"
        "Decide how to handle them:\n"
        "- supersede: the NEW memory replaces the OLD (updated info, corrections)\n"
        "- coexist: both are valid simultaneously (different contexts or subjects)\n"
        "- merge: combine information from both into a single memory\n\n"
        f"OLD MEMORY (ID: {existing_memory.get('memory_id', 'unknown')}):\n"
        f"{existing_text}\n\n"
        f"NEW MEMORY:\n{new_memory[:1000]}\n\n"
        "Return ONLY a JSON object: "
        '{"action": "supersede|coexist|merge", "reason": "brief explanation", '
        '"merged_text": "combined text if action is merge, else null"}'
    )

    try:
        content = await call_internal_llm(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        parsed = parse_llm_json(content)

        if not isinstance(parsed, dict):
            return {"action": "coexist", "reason": "LLM returned non-dict", "merged_text": None}

        action = parsed.get("action", "coexist").lower()
        if action not in ("supersede", "coexist", "merge"):
            action = "coexist"

        merged_text = parsed.get("merged_text") if action == "merge" else None

        return {
            "action": action,
            "reason": parsed.get("reason", ""),
            "merged_text": merged_text,
        }
    except CircuitOpenError:
        logger.warning("Bifrost circuit open during conflict resolution, defaulting to coexist")
        return {"action": "coexist", "reason": "circuit open", "merged_text": None}
    except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
        logger.warning("Memory conflict resolution LLM call failed: %s", e)
        return {"action": "coexist", "reason": f"LLM call failed: {e}", "merged_text": None}


# ---------------------------------------------------------------------------
# Decay / Reinforcement Scoring (Phase 44)
# ---------------------------------------------------------------------------


def calculate_memory_score(
    base_score: float,
    access_count: int,
    age_days: float,
    stability_days: float | None = None,
    *,
    memory_type: str = "decision",
    source_authority: float = 1.0,
    access_ages: list[float] | None = None,
    half_life_days: float | None = None,
) -> float:
    """Calculate memory relevance score with per-type decay and reinforcement.

    Decay curves vary by ``memory_type``:

    - **empirical**: No decay (permanent facts).
    - **temporal**: Step function — full score before event (age <= 0),
      0.1 residual after.
    - **decision / preference**: Power-law decay ``(1 + t/(9*S))^(-0.5)``
      for long-tail preservation.
    - **project_context / conversational**: Exponential decay ``2^(-t/S)``.
    - Unknown types fall through to exponential.

    Reinforcement: ``min(1 + log2(1 + access_count), 5)`` — capped at 5x.
    When *access_ages* is provided, recent accesses contribute more via
    exponential weighting.

    *source_authority* scales the final result (default 1.0).

    Returns a non-negative float.
    """
    # Resolve stability: explicit param > config lookup > legacy half_life > 30
    if stability_days is None:
        stability_days = config.MEMORY_TYPE_STABILITY.get(memory_type)
    if stability_days is None:
        stability_days = half_life_days if half_life_days is not None else config.MEMORY_HALF_LIFE_DAYS
    # Guard against zero / negative stability for types that need it
    if memory_type not in ("empirical", "temporal") and stability_days <= 0:
        stability_days = 30.0

    age = max(0.0, age_days)

    # --- Decay ---
    if memory_type == "empirical":
        decay = 1.0
    elif memory_type == "temporal":
        decay = 1.0 if age_days <= 0.0 else 0.1
    elif memory_type in config.MEMORY_POWER_LAW_TYPES:
        # Power-law: (1 + t / (9 * S))^(-0.5)
        decay = (1.0 + age / (9.0 * stability_days)) ** (-0.5)
    else:
        # Exponential (project_context, conversational, unknown)
        if stability_days <= 0:
            stability_days = 30.0
        decay = 2.0 ** (-age / stability_days)

    # --- Reinforcement ---
    if memory_type == "empirical":
        reinforcement = 1.0
    elif access_ages is not None and len(access_ages) > 0:
        # Recency-weighted: recent accesses matter more
        weights = [2.0 ** (-a / 30.0) for a in access_ages]
        reinforcement = min(1.0 + math.log2(1.0 + sum(weights)), 5.0)
    else:
        reinforcement = min(1.0 + math.log2(1.0 + max(0, access_count)), 5.0)

    return max(0.0, base_score * reinforcement * decay * source_authority)


# ---------------------------------------------------------------------------
# Context-Aware Memory Recall (Phase 44)
# ---------------------------------------------------------------------------


async def recall_memories(
    query: str,
    chroma_client: Any,
    neo4j_driver: Any,
    top_k: int = 10,
    min_score: float | None = None,
) -> list[dict]:
    """Context-aware memory retrieval with decay scoring.

    1. Vector search for relevant memories
    2. Apply decay/reinforcement scoring to each result
    3. Update access_count on retrieved memories (reinforcement)
    4. Sort by adjusted score
    5. Return top_k above min_score
    """
    if min_score is None:
        min_score = config.MEMORY_MIN_RECALL_SCORE

    if chroma_client is None:
        return []

    # Step 1: Vector search
    try:
        coll_name = config.collection_name("conversations")
        collection = chroma_client.get_or_create_collection(name=coll_name)
        results = collection.query(
            query_texts=[query],
            n_results=top_k * 3,  # over-fetch to compensate for decay filtering
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.debug("Memory recall vector search failed: %s", e)
        return []

    if not results["ids"] or not results["ids"][0]:
        return []

    now = utcnow()
    scored_memories: list[dict] = []

    for i, chunk_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i] if results["distances"] else 1.0
        base_similarity = l2_distance_to_relevance(distance)
        metadata = results["metadatas"][0][i] if results["metadatas"] else {}

        # Compute age in days
        created_str = metadata.get("valid_from", metadata.get("ingested_at", ""))
        age_days = 0.0
        if created_str:
            try:
                from datetime import datetime, timezone

                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now_aware - created_dt).total_seconds() / 86400.0)
            except (ValueError, TypeError):
                pass

        access_count = int(metadata.get("access_count", 0))
        artifact_id = metadata.get("artifact_id", chunk_id)

        # Step 2: Apply decay/reinforcement
        adjusted_score = calculate_memory_score(
            base_score=base_similarity,
            access_count=access_count,
            age_days=age_days,
            memory_type=metadata.get("memory_type", "decision"),
        )

        # Per-type minimum recall threshold
        type_min = config.MEMORY_MIN_RECALL_BY_TYPE.get(
            metadata.get("memory_type", "decision"),
            config.MEMORY_MIN_RECALL_SCORE,
        )
        if adjusted_score < type_min:
            continue

        if adjusted_score >= min_score:
            scored_memories.append({
                "memory_id": artifact_id,
                "chunk_id": chunk_id,
                "text": results["documents"][0][i] if results["documents"] else "",
                "base_similarity": round(base_similarity, 4),
                "adjusted_score": round(adjusted_score, 4),
                "age_days": round(age_days, 1),
                "access_count": access_count,
                "memory_type": metadata.get("memory_type", "fact"),
                "summary": metadata.get("summary", ""),
            })

    # Step 4: Sort by adjusted score descending
    scored_memories.sort(key=lambda m: m["adjusted_score"], reverse=True)
    top_results = scored_memories[:top_k]

    # Step 3: Reinforce access counts for retrieved memories
    if neo4j_driver and top_results:
        retrieved_ids = [m["memory_id"] for m in top_results]
        try:
            with neo4j_driver.session() as session:
                session.run(
                    "UNWIND $ids AS aid "
                    "MATCH (a:Artifact {id: aid}) "
                    "SET a.access_count = coalesce(a.access_count, 0) + 1, "
                    "    a.last_accessed_at = $now",
                    ids=retrieved_ids,
                    now=utcnow_iso(),
                )
        except Exception as e:
            logger.debug("Failed to update memory access counts: %s", e)

    return top_results


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
