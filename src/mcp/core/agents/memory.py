# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory Extraction Agent — extracts facts, decisions, and preferences from conversations.

Phase 44 additions: conflict detection, LLM conflict resolution, decay/reinforcement
scoring, and context-aware recall with access-count reinforcement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx

import config
from config.settings import MEMORY_TYPE_MIGRATION
from core.agents.fact_derivation import OPEN_INTERVAL, resolve_valid_from
from core.context.identity import with_tenant_scope
from core.utils.cache import log_event
from core.utils.circuit_breaker import CircuitOpenError
from core.utils.embeddings import l2_distance_to_relevance
from core.utils.internal_llm import call_internal_llm
from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.memory")

# ---------------------------------------------------------------------------
# Phase K2.1 — entity-extraction enqueue dependency injection slot.
#
# Set by the app layer at startup (app.startup or equivalent) so the
# core memory pipeline can fire entity extraction for new memories
# without crossing the core/ → app/ boundary.
# ---------------------------------------------------------------------------
_entity_extraction_enqueue: Callable[[str], None] | None = None


def set_entity_extraction_enqueue(fn: Callable[[str], None] | None) -> None:
    """Install (or clear) the callback invoked when a new memory is stored.

    The callback receives the artifact_id of the freshly-stored memory.
    Idempotent: callers re-register safely. Passing ``None`` clears the
    slot (used by tests to isolate side effects).
    """
    global _entity_extraction_enqueue
    _entity_extraction_enqueue = fn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_RESPONSE_LENGTH = 100

# Per-stage LLM call budgets (Workstream A Phase 1.2). Bound individual
# OpenRouter / Ollama calls so a single stalled request can't tie up the
# /sdk/v1/memory/extract endpoint past its 10s SLO. Sized empirically
# against current OpenRouter p95 generation latency:
#   - extract is the load-bearing call (max_tokens=1000); 12s catches
#     genuine hangs while letting typical 3-7s generations complete.
#   - conflict-resolution caps max_tokens=500; 8s is comfortable.
# Consolidation (max_tokens=200) gets the same 8s in memory_consolidation.py.
# Soak data: 5.7% ReadTimeouts at the httpx 20s default — these bounds
# replace that ceiling and surface the timeout branch via
# log_swallowed_error.
# Env-tunable so offline/batch callers (e.g. the LongMemEval eval, which calls
# a remote reader where a single extraction can exceed the live-chat budget)
# can raise the ceiling without relaxing the interactive SLO. Default 6.0s
# stays the live-chat bound.
MEMORY_LLM_BUDGET_S = float(os.getenv("MEMORY_LLM_BUDGET_S", "6.0"))
MEMORY_CONFLICT_LLM_BUDGET_S = float(os.getenv("MEMORY_CONFLICT_LLM_BUDGET_S", "3.0"))

MEMORY_TYPES = {
    "empirical", "decision", "preference", "project_context", "temporal", "conversational",
    "fact", "action_item",  # Legacy aliases
}


# ---------------------------------------------------------------------------
# Memory extraction via LLM
# ---------------------------------------------------------------------------

async def extract_memories(
    response_text: str,
    conversation_id: str,
    model: str = "",
    observation_date: str | None = None,
) -> list[dict[str, Any]]:
    """Use a lightweight LLM to extract memorable content from a response.

    Parameters
    ----------
    observation_date:
        When provided, the date the conversation occurred. The extractor
        grounds relative time references ("last week", "yesterday") to
        absolute dates against this anchor and captures fact transitions
        (old → new). A memory dated only "last week" is unretrievable
        months later; the absolute form stays meaningful. This materially
        helps temporal-reasoning and knowledge-update recall downstream.
    """
    if len(response_text) < MIN_RESPONSE_LENGTH:
        return []

    date_guidance = ""
    if observation_date:
        date_guidance = (
            f"This conversation occurred on {observation_date}. When the "
            "content references relative time ('yesterday', 'last week', "
            "'next month', 'recently'), resolve it to an ABSOLUTE date "
            f"relative to {observation_date} and state that absolute date in "
            "the extracted content. When the user describes CHANGING, "
            "switching, replacing, or updating something, capture the "
            "transition — both the new state AND what it replaced "
            "(e.g. 'switched from almond to oat milk').\n\n"
        )

    prompt = (
        "Analyze this assistant response and extract any memorable content. "
        "For each item, classify it as one of: fact, decision, preference, action_item.\n\n"
        + date_guidance
        + "Return ONLY a JSON array of objects with keys: content, memory_type, summary, event_date.\n"
        "- content: the full extractable text\n"
        "- memory_type: one of fact/decision/preference/action_item\n"
        "- summary: a concise summary (max 500 chars)\n"
        "- event_date: the absolute date the fact is about, as ISO YYYY-MM-DD, "
        "resolved from the conversation date above; use null if no date applies\n\n"
        "If nothing is worth extracting, return an empty array [].\n\n"
        f"Response:\n{response_text[:3000]}\n\n"
        "JSON array:"
    )

    try:
        content = await asyncio.wait_for(
            call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"},
                stage="memory_extract",
            ),
            timeout=MEMORY_LLM_BUDGET_S,
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
            # Structured event_date — the absolute date the fact is ABOUT (not
            # ingestion time). Falls back to the observation (session) date when
            # the LLM doesn't pin one, so every dated session yields a usable
            # event_date for downstream time-filtered retrieval + arithmetic.
            ev = m.get("event_date")
            event_date = str(ev) if ev and str(ev).lower() != "null" else (observation_date or "")
            valid.append({
                "content": str(m.get("content", ""))[:2000],
                "memory_type": mem_type,
                "summary": str(m.get("summary", ""))[:500],
                "event_date": event_date,
            })
        return valid

    except CircuitOpenError:
        logger.warning("Bifrost memory circuit open, skipping memory extraction")
        return []
    except asyncio.TimeoutError as exc:
        log_swallowed_error("core.agents.memory.extract_memories_timeout", exc)
        logger.warning(
            "Memory extraction LLM call exceeded %.1fs budget — returning []",
            MEMORY_LLM_BUDGET_S,
        )
        return []
    except Exception as e:
        log_swallowed_error('core.agents.memory', e)
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
    observation_date: str | None = None,
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
    observation_date : str | None
        The date the conversation occurred. Threaded into ``extract_memories``
        so relative time references are grounded to absolute dates and each
        memory gets a structured ``event_date``. Historically dropped here —
        the call site passed only ``model`` — leaving production extraction
        date-blind; that gap is the linchpin for temporal/knowledge-update.
    """
    if not config.ENABLE_MEMORY_EXTRACTION:
        return {"status": "skipped", "reason": "Memory extraction disabled"}

    memories = await extract_memories(
        response_text, conversation_id, model, observation_date=observation_date,
    )

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
            from core.agents.memory_consolidation import (
                classify_memory,
                mark_superseded,
            )
    except ImportError:
        pass

    # F-AUTO-03: parallelize per-memory consolidation+ingest. Each memory's
    # work is a chain of independent LLM (classify_memory, conflict resolve)
    # + Neo4j + KB ingest calls; serializing them blew the 10s SLO. asyncio.gather
    # lets the LLM calls overlap and the synchronous KB/Neo4j work runs on
    # the default executor via asyncio.to_thread inside the helper.
    async def _process_one_memory(idx: int, mem: dict) -> dict:
        """Run consolidation + conflict-detection + ingest for one memory.

        Returns a result dict that gets appended to ``results`` by the
        caller, plus carries internal status fields (``_stored`` /
        ``_skipped``) for counter aggregation. These underscore-prefixed
        fields are stripped before the final list returns to the caller.
        """
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
                    return {
                        "memory_type": mem["memory_type"],
                        "summary": mem["summary"],
                        "status": "skipped_duplicate",
                        "reason": action.reason,
                        "_skipped": True,
                    }

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
                except Exception as exc:  # noqa: BLE001 — conflict-detection failure must not lose the memory
                    # Phase 44 conflict detection/resolution spans several LLM
                    # + graph calls; many exception types are reachable.
                    # Propagating would cancel the per-memory task and lose
                    # the memory — strictly worse than the duplicate-
                    # accumulation problem we're swallowing.
                    log_swallowed_error(
                        "core.agents.memory.phase44_conflict_detection",
                        exc,
                        redis_client=redis_client,
                    )

            convo_prefix = conversation_id[:8] if conversation_id else "unknown"
            now_iso = utcnow_iso()
            timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"memory_{mem['memory_type']}_{convo_prefix}_{timestamp}_{idx}"

            # Bi-temporal contract (mirrors tests/eval/longmemeval/bitemporal.py
            # so the Chroma memory store and the Neo4j :Fact store resolve
            # identical validity intervals — the two must never diverge):
            #   created_at — system/transaction time (this ingestion instant).
            #   valid_from — world/valid-time start: event_date when known, else
            #                the observation date; "" when neither is known.
            #   valid_to   — OPEN_INTERVAL ("") = still true; Phase D closes it.
            # decay_anchor pins the Ebbinghaus age-anchor to ingestion time: it
            # holds the value valid_from used to carry, so moving valid_from onto
            # event_date (which can be far in the past) does NOT shift
            # calculate_memory_score's age — recall reads decay_anchor before
            # valid_from (see recall_memories). This keeps the i20b slow-decay
            # contract byte-identical while giving the store its valid-time.
            metadata = {
                "filename": filename,
                "conversation_id": conversation_id,
                "model": model,
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "created_at": now_iso,
                "valid_from": resolve_valid_from(
                    event_date=mem.get("event_date"),
                    observation_date=observation_date,
                ),
                "valid_to": OPEN_INTERVAL,
                "decay_anchor": now_iso,
                "access_count": "0",
            }
            # event_date = the absolute date the fact is ABOUT. Enables
            # time-filtered/-windowed retrieval and deterministic date arithmetic
            # downstream. Only set when known.
            if mem.get("event_date"):
                metadata["event_date"] = mem["event_date"]

            # ingest_fn is synchronous KB ingest — hand off to a worker
            # thread so we don't stall the event loop while parallel
            # memories' LLM calls are still in flight.
            result = await asyncio.to_thread(
                ingest_fn, effective_content, "conversations", metadata=metadata
            )

            stored = False
            if result.get("status") == "success":
                stored = True
                new_artifact_id = result.get("artifact_id", "")

                # Mark superseded memory if this was an UPDATE
                if (
                    action_label == "UPDATE"
                    and supersede_target
                    and neo4j_driver
                    and new_artifact_id
                    and mark_superseded is not None
                ):
                    await asyncio.to_thread(
                        mark_superseded, neo4j_driver, supersede_target, new_artifact_id
                    )

                    # Phase D — close the superseded memory's bi-temporal fact
                    # intervals in BOTH stores so the :Fact layer never diverges
                    # from the memory layer (plan R4/R7). chroma_client is
                    # already DI-threaded into this function (like neo4j_driver);
                    # resolve the conversations collection the same way recall /
                    # conflict-detection do — no app import (core↛app). Wholly
                    # best-effort: closure must never break the store path.
                    try:
                        from core.agents.fact_invalidation import (
                            close_superseded_memory_intervals,
                        )

                        fact_collection = None
                        if chroma_client is not None:
                            fact_collection = await asyncio.to_thread(
                                chroma_client.get_or_create_collection,
                                name=config.collection_name("conversations"),
                            )
                        await close_superseded_memory_intervals(
                            neo4j_driver,
                            fact_collection,
                            old_artifact_id=supersede_target,
                            new_artifact_id=new_artifact_id,
                            new_valid_from=metadata["valid_from"],
                            new_content=effective_content,
                        )
                    except Exception as exc:  # noqa: BLE001 — closure must not break the store path
                        log_swallowed_error(
                            "core.agents.memory.fact_invalidation",
                            exc,
                            redis_client=redis_client,
                        )

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
                    except Exception as exc:  # noqa: BLE001 — observability boundary
                        log_swallowed_error(
                            "core.agents.memory.log_extraction_event",
                            exc,
                            redis_client=redis_client,
                        )

                if neo4j_driver and new_artifact_id:
                    def _link_extracted_from() -> None:
                        with neo4j_driver.session() as session:
                            session.run(
                                "MATCH (m:Artifact {id: $memory_id}) "
                                "MERGE (c:Conversation {id: $convo_id}) "
                                "MERGE (m)-[:EXTRACTED_FROM]->(c)",
                                memory_id=new_artifact_id,
                                convo_id=conversation_id,
                            )
                    try:
                        await asyncio.to_thread(_link_extracted_from)
                    except Exception as exc:  # noqa: BLE001 — neo4j driver exceptions vary by version
                        log_swallowed_error(
                            "core.agents.memory.link_extracted_from",
                            exc,
                            redis_client=redis_client,
                        )

                # Phase K2.1 — entity extraction enqueue for the memory.
                if new_artifact_id and _entity_extraction_enqueue is not None:
                    try:
                        _entity_extraction_enqueue(new_artifact_id)
                    except Exception as exc:  # noqa: BLE001 — observability boundary
                        log_swallowed_error(
                            "core.agents.memory.entity_extraction_enqueue",
                            exc,
                            redis_client=redis_client,
                        )

            entry = {
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "status": result.get("status", "error"),
                "artifact_id": result.get("artifact_id", ""),
                "consolidation_action": action_label,
                "_stored": stored,
            }
            if conflict_resolutions:
                entry["conflict_resolutions"] = conflict_resolutions
            return entry
        except Exception as exc:  # noqa: BLE001 — top-level per-memory failure boundary
            log_swallowed_error(
                "core.agents.memory.process_one_memory",
                exc,
                redis_client=redis_client,
            )
            return {
                "memory_type": mem["memory_type"],
                "summary": mem["summary"],
                "status": "error",
                "error": str(exc),
            }

    raw_results = await asyncio.gather(
        *[_process_one_memory(idx, mem) for idx, mem in enumerate(memories)],
        return_exceptions=True,
    )

    results: list[dict] = []
    stored_count = 0
    skipped_count = 0
    for idx, r in enumerate(raw_results):
        if isinstance(r, BaseException):
            # _process_one_memory swallows its own exceptions; reaching
            # here means gather captured something exceptional (e.g.
            # CancelledError). Log and continue so one bad memory does
            # not poison the batch's response shape.
            log_swallowed_error(
                "core.agents.memory.gather_unexpected",
                r if isinstance(r, Exception) else Exception(repr(r)),
                redis_client=redis_client,
            )
            mem = memories[idx]
            results.append({
                "memory_type": mem.get("memory_type", "unknown"),
                "summary": mem.get("summary", ""),
                "status": "error",
                "error": str(r),
            })
            continue
        if r.pop("_stored", False):
            stored_count += 1
        if r.pop("_skipped", False):
            skipped_count += 1
        results.append(r)

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
            where=with_tenant_scope(None),
        )
    except Exception as e:
        log_swallowed_error('core.agents.memory', e)
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
        content = await asyncio.wait_for(
            call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=500,
                response_format={"type": "json_object"},
                stage="memory_conflict_resolve",
            ),
            timeout=MEMORY_CONFLICT_LLM_BUDGET_S,
        )
        parsed = parse_llm_json(content)

        if not isinstance(parsed, dict):
            return {"action": "coexist", "reason": "LLM returned non-dict", "merged_text": None}

        action = parsed.get("action", "coexist").lower()
        if action not in ("supersede", "coexist", "merge"):
            action = "coexist"

        merged_text = parsed.get("merged_text") if action == "merge" else None

        # NLI guard: verify merged text preserves both original facts.
        # Prevents semantic drift during consolidation (SSGM framework).
        if action == "merge" and merged_text:
            try:
                from core.utils.nli import nli_score

                nli_guard_threshold = getattr(config, "MEMORY_CONSOLIDATION_NLI_GUARD", 0.7)
                # Check: does each original fact entail the merged result?
                # Premise=original, hypothesis=merged — "is the original preserved in the merge?"
                nli_old = nli_score(existing_text[:512], merged_text[:512])
                nli_new = nli_score(new_memory[:512], merged_text[:512])
                if (
                    float(nli_old["entailment"]) < nli_guard_threshold
                    or float(nli_new["entailment"]) < nli_guard_threshold
                ):
                    logger.info(
                        "NLI guard: merge would lose info (old=%.2f, new=%.2f) — keeping both",
                        nli_old["entailment"],
                        nli_new["entailment"],
                    )
                    return {
                        "action": "coexist",
                        "reason": "NLI guard: merge would lose information",
                        "merged_text": None,
                    }
            except Exception as exc:
                # NLI unavailable (model not loaded, CUDA OOM, malformed input);
                # falling through merges without the entailment guard. Whether
                # that fallthrough is acceptable is a semantic question tracked
                # separately; the observability hook is the scope here.
                log_swallowed_error("core.agents.memory.resolve_conflict_nli_guard", exc)

        return {
            "action": action,
            "reason": parsed.get("reason", ""),
            "merged_text": merged_text,
        }
    except CircuitOpenError:
        logger.warning("Bifrost circuit open during conflict resolution, defaulting to coexist")
        return {"action": "coexist", "reason": "circuit open", "merged_text": None}
    except asyncio.TimeoutError as exc:
        log_swallowed_error("core.agents.memory.resolve_conflict_timeout", exc)
        logger.warning(
            "Memory conflict resolution exceeded %.1fs budget — defaulting to coexist",
            MEMORY_CONFLICT_LLM_BUDGET_S,
        )
        return {"action": "coexist", "reason": "timeout", "merged_text": None}
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
    # (empirical now decays via power-law too, so it needs the guard).
    if memory_type != "temporal" and stability_days <= 0:
        stability_days = 30.0

    age = max(0.0, age_days)

    # --- Decay ---
    if memory_type == "temporal":
        decay = 1.0 if age_days <= 0.0 else 0.1
    elif memory_type in config.MEMORY_POWER_LAW_TYPES:
        # Power-law: (1 + t / (9 * S))^(-0.5). Empirical memories decay this
        # way too (stability from MEMORY_TYPE_STABILITY, finite since the
        # 2026-07-13 trust-integrity fix) — the old decay=1.0 hardcode made
        # verification-promoted facts immortal in recall scoring.
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
            where=with_tenant_scope(None),
        )
    except Exception as e:
        log_swallowed_error('core.agents.memory', e)
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

        # Compute age in days — use decay_anchor (refresh-on-read) if available,
        # falling back to valid_from or ingested_at for older memories.
        created_str = metadata.get("decay_anchor", metadata.get("valid_from", metadata.get("ingested_at", "")))
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

        mem_type = metadata.get("memory_type", "decision")
        # Step 2a: Ranking score — reinforcement (access frequency, ≤5×) boosts
        # ORDER among recalled memories.
        adjusted_score = calculate_memory_score(
            base_score=base_similarity,
            access_count=access_count,
            age_days=age_days,
            memory_type=mem_type,
        )
        # Step 2b: Relevance score — base similarity × decay WITHOUT the
        # reinforcement multiplier. All recall/relevance GATING uses this so a
        # frequently-accessed but semantically-poor match cannot clear the
        # relevance floor on popularity alone (reinforcement, capped at 5×,
        # previously inflated a 0.2-similarity memory to 0.8 and slipped it past
        # both the per-type floor and the keyword-guard). Reinforcement affects
        # ranking order only, never whether a memory is recalled.
        relevance_score = calculate_memory_score(
            base_score=base_similarity,
            access_count=0,
            age_days=age_days,
            memory_type=mem_type,
        )

        # Per-type minimum recall threshold (gated on relevance, not reinforced)
        type_min = config.MEMORY_MIN_RECALL_BY_TYPE.get(
            mem_type,
            config.MEMORY_MIN_RECALL_SCORE,
        )
        if relevance_score < type_min:
            continue

        # NLI relevance check — ensure memory is semantically relevant, not just keyword match
        try:
            from core.utils.nli import nli_score
            doc = results["documents"][0][i] if results["documents"] else ""
            mem_nli = nli_score(doc[:512], query)
            if mem_nli["contradiction"] >= config.NLI_CONTRADICTION_THRESHOLD:
                continue  # Memory contradicts query context — skip
            if mem_nli["entailment"] < 0.3 and relevance_score < 0.5:
                continue  # Low entailment + low (decayed, non-reinforced) relevance = keyword match
        except Exception as exc:
            # NLI unavailable — fall back to adjusted_score alone.
            log_swallowed_error("core.agents.memory.recall_memories_nli_relevance", exc)

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
                # Bi-temporal valid-time end: "" (OPEN_INTERVAL) = still true;
                # a non-empty value marks a closed interval (Phase D). Captured
                # here so the read-side admission filter below can drop closed
                # intervals without a second Chroma round-trip.
                "valid_to": metadata.get("valid_to", OPEN_INTERVAL),
            })

    # Step 3.5: Supersession-at-read — drop candidates explicitly marked
    # superseded by a newer fact. The write path sets ``superseded_by`` (via
    # conflict resolution / mark_superseded), but recall previously ignored it
    # and could surface a stale value alongside its replacement — the
    # knowledge-update failure mode. We check Neo4j once for the whole candidate
    # set using the driver this function already holds (keeping the core↛app
    # boundary intact — no app.db import). Best-effort: a Neo4j hiccup leaves
    # recall unchanged rather than failing.
    from config.features import ENABLE_MEMORY_SUPERSESSION_FILTER

    if ENABLE_MEMORY_SUPERSESSION_FILTER and neo4j_driver and scored_memories:
        candidate_ids = [m["memory_id"] for m in scored_memories]
        try:
            with neo4j_driver.session() as session:
                rows = session.run(
                    "UNWIND $ids AS aid "
                    "MATCH (a:Artifact {id: aid}) "
                    "WHERE a.superseded_by IS NOT NULL "
                    "RETURN a.id AS id",
                    ids=candidate_ids,
                )
                superseded_ids = {r["id"] for r in rows}
        except Exception as exc:  # noqa: BLE001 — recall proceeds unfiltered
            log_swallowed_error(
                "core.agents.memory.recall_memories_supersession", exc,
            )
            superseded_ids = set()
        if superseded_ids:
            scored_memories = [
                m for m in scored_memories if m["memory_id"] not in superseded_ids
            ]

    # Step 3.6: Interval admission (bi-temporal :Fact layer, plan D3) — drop
    # candidates whose validity interval is CLOSED (non-empty valid_to). DARK by
    # default (ENABLE_FACT_INVALIDATION_FILTER, default off): with the flag off,
    # this block is skipped entirely and recall is byte-identical to before.
    # Missing / empty valid_to = open = admitted (back-compat with pre-Phase-C
    # memories that were never stamped). No extra Neo4j round-trip: the write
    # side keeps the two stores in lockstep (close_superseded_memory_intervals
    # mirror-closes the Chroma valid_to whenever it closes a :Fact), so the
    # Chroma metadata already surfaced here is authoritative for admission.
    #
    # PRECEDENCE (plan F2 — validity gates ADMISSIBILITY, boosts order the
    # SURVIVORS): this admission filter (and Step 3.5's supersession filter) run
    # BEFORE the Step 4 ordering below — they REMOVE candidates from
    # scored_memories, so nothing downstream can resurrect a closed interval. The
    # only ranking signal here (adjusted_score, computed in the Step 2a scoring
    # loop) is applied purely by the Step 4 `sort` over whatever survives
    # admission; no proximity/recency boost is added after this point, so a boost
    # can never re-admit a filtered candidate. The event-time proximity boost
    # (core/retrieval/temporal_filter.apply_proximity_boost) is additive-only and
    # lives on the retrieval-ranking path, never here — it reorders, never admits.
    from config.features import ENABLE_FACT_INVALIDATION_FILTER

    if ENABLE_FACT_INVALIDATION_FILTER and scored_memories:
        scored_memories = [
            m
            for m in scored_memories
            if not str(m.get("valid_to", OPEN_INTERVAL)).strip()
        ]

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
        except Exception as exc:
            # Neo4j driver exception hierarchy differs across v5/v6; narrowing
            # fragile, so log_swallowed_error is the contract here. Access-count
            # reinforcement failure does not break retrieval itself.
            log_swallowed_error("core.agents.memory.recall_memories_reinforce", exc)

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

        # Soft-hide without per-id hide_content (batch Cypher); still bust C1/C2
        # so archived memories are not served warm within the query-cache TTL.
        if archived_count:
            try:
                from utils.query_cache import invalidate_query_caches
                invalidate_query_caches(trigger="memory.archive_old_memories")
            except Exception as bust_exc:
                log_swallowed_error("core.agents.memory.archive_cache_bust", bust_exc)

        return {
            "timestamp": utcnow_iso(),
            "retention_days": retention_days,
            "cutoff_date": cutoff,
            "archived_count": archived_count,
        }
    except Exception as e:  # Neo4j driver exceptions vary by version
        log_swallowed_error('core.agents.memory', e)
        logger.error("Memory archival failed: %s", e)
        return {
            "timestamp": utcnow_iso(),
            "retention_days": retention_days,
            "error": str(e),
            "archived_count": 0,
        }
