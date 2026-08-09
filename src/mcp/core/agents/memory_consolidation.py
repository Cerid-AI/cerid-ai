# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Memory consolidation — ADD/UPDATE/NOOP classification for new memories.

Prevents duplicate storage and detects superseding information by
comparing new memories against existing ones via semantic similarity
(ChromaDB) and LLM-based classification. Inspired by Mem0's memory
management protocol.

Canonical location as of Sprint D (2026-04-19 consolidation program).
Previously at ``src/mcp/utils/memory_consolidation.py``; a thin
bridge remains there until Sprint E retires the bridge dir.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

import config
from core.context.identity import with_tenant_scope
from core.utils.circuit_breaker import CircuitOpenError
from core.utils.embeddings import l2_distance_to_relevance
from core.utils.internal_llm import call_internal_llm
from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso
from errors import RetrievalError

# Per-stage LLM budget (Workstream A Phase 1.2). Mirrors the bound in
# core.agents.memory; consolidation is the heaviest fan-out call inside
# extract_and_store_memories so this is the most-load-bearing budget.
CONSOLIDATION_LLM_BUDGET_S = 3.0

# ...and mirrors its provider problem too. On local inference the 3s ceiling
# timed out (6 swallowed `classify_timeout` events in one hour on this host),
# so ADD/UPDATE classification never returned and no memory was ever superseded
# — the KB had zero SUPERSEDES relationships across its entire history.
_DEFAULT_CONSOLIDATION_BUDGET_S = 3.0
_LOCAL_CONSOLIDATION_BUDGET_S = 45.0


def _resolve_consolidation_budget_s() -> float:
    """Consolidation budget for the active provider."""
    from core.agents.memory import resolve_llm_budget_s

    return resolve_llm_budget_s(
        CONSOLIDATION_LLM_BUDGET_S,
        _DEFAULT_CONSOLIDATION_BUDGET_S,
        _LOCAL_CONSOLIDATION_BUDGET_S,
    )

logger = logging.getLogger("ai-companion.memory_consolidation")

# Similarity threshold for candidate retrieval — below this, treat as new info
SIMILARITY_THRESHOLD = 0.85

# ---------------------------------------------------------------------------
# Failure callback (Phase O.2 — memory consolidation preservation gate)
#
# ``core/`` may never import ``app/``, so we expose a module-level callback
# that ``app/main.py`` lifespan binds after Redis is available.  When set,
# it is called with a short reason string whenever the pipeline swallows a
# consolidation failure. The callback must not raise.
#
# Usage (app/main.py lifespan):
#   import core.agents.memory_consolidation as _mc
#   _mc.consolidation_failure_callback = partial(record_consolidation_failure, redis_client)
# ---------------------------------------------------------------------------
from collections.abc import Callable  # noqa: E402 — intentionally after module-level constants

consolidation_failure_callback: Callable[[str], None] | None = None


def _fire_failure_callback(reason: str) -> None:
    """Call the registered failure callback defensively — never raises."""
    cb = consolidation_failure_callback
    if cb is not None:
        try:
            cb(reason)
        except Exception as _cb_exc:  # noqa: BLE001
            log_swallowed_error(
                "core.agents.memory_consolidation._fire_failure_callback", _cb_exc
            )


@dataclass
class MemoryAction:
    """Result of memory consolidation classification."""

    action: Literal["ADD", "UPDATE", "NOOP"]
    target_id: str | None = None
    reason: str = ""


async def classify_memory(
    new_content: str,
    chroma_client: Any | None = None,
    memory_type: str = "fact",
) -> MemoryAction:
    """Classify whether a new memory should be added, updates existing, or is a duplicate.

    1. Query ChromaDB for semantically similar existing memories.
    2. If no close matches, return ADD.
    3. If close matches found, ask LLM to classify: ADD/UPDATE/NOOP.
    """
    if chroma_client is None:
        return MemoryAction(action="ADD", reason="no ChromaDB client available")

    # Step 1: Find similar existing memories
    try:
        coll_name = config.collection_name("conversations")
        collection = chroma_client.get_collection(name=coll_name)
        results = collection.query(
            query_texts=[new_content],
            n_results=3,
            include=["documents", "metadatas", "distances"],
            where=with_tenant_scope({"memory_type": memory_type} if memory_type else None),
        )
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug("Consolidation similarity search failed: %s", e)
        return MemoryAction(action="ADD", reason=f"similarity search failed: {e}")

    # Step 2: Filter by similarity threshold
    candidates: list[dict[str, Any]] = []
    if results["ids"] and results["ids"][0]:
        for i, chunk_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results["distances"] else 1.0
            similarity = l2_distance_to_relevance(distance)
            if similarity >= SIMILARITY_THRESHOLD:
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                candidates.append({
                    "id": chunk_id,
                    "content": results["documents"][0][i],
                    "similarity": round(similarity, 4),
                    "artifact_id": metadata.get("artifact_id", chunk_id),
                    "metadata": metadata,
                })

    if not candidates:
        return MemoryAction(action="ADD", reason="no similar existing memories found")

    # Step 3: LLM classification
    return await _llm_classify(new_content, candidates)


async def _llm_classify(
    new_content: str,
    candidates: list[dict[str, Any]],
) -> MemoryAction:
    """Use LLM to decide ADD/UPDATE/NOOP given similar existing memories."""
    existing_text = "\n".join(
        f"[ID: {c['artifact_id']}] {c['content'][:500]}" for c in candidates
    )

    prompt = (
        "You are a memory consolidation agent. Compare the NEW memory against "
        "EXISTING memories and decide:\n"
        "- NOOP: The new memory is a duplicate (same information already exists)\n"
        "- UPDATE: The new memory supersedes/corrects an existing one (return target_id)\n"
        "- ADD: The new memory contains genuinely new information despite similar text\n\n"
        f"EXISTING MEMORIES:\n{existing_text}\n\n"
        f"NEW MEMORY:\n{new_content[:1000]}\n\n"
        'Return ONLY a JSON object: {"action": "ADD|UPDATE|NOOP", '
        '"target_id": "id_of_superseded_memory_or_null", "reason": "brief explanation"}'
    )

    try:
        content = await asyncio.wait_for(
            call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
                stage="memory_consolidation",
            ),
            timeout=_resolve_consolidation_budget_s(),
        )
        parsed = parse_llm_json(content)

        if not isinstance(parsed, dict):
            return MemoryAction(action="ADD", reason="LLM returned non-dict")

        action = parsed.get("action", "ADD").upper()
        if action not in ("ADD", "UPDATE", "NOOP"):
            action = "ADD"

        target_id = None
        if action == "UPDATE":
            target_id = parsed.get("target_id")
            # Validate target_id exists in candidates
            valid_ids = {c["artifact_id"] for c in candidates}
            if target_id not in valid_ids:
                # Fall back to most similar candidate
                target_id = candidates[0]["artifact_id"]

        return MemoryAction(
            action=action,
            target_id=target_id,
            reason=parsed.get("reason", ""),
        )
    except CircuitOpenError:
        logger.warning("LLM circuit open, defaulting to ADD")
        _fire_failure_callback("circuit_open")
        return MemoryAction(action="ADD", reason="circuit open")
    except asyncio.TimeoutError as exc:
        log_swallowed_error("core.agents.memory_consolidation.classify_timeout", exc)
        logger.warning(
            "Memory consolidation exceeded %.1fs budget — defaulting to ADD",
            _resolve_consolidation_budget_s(),
        )
        _fire_failure_callback("timeout")
        return MemoryAction(action="ADD", reason="timeout")
    except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
        logger.warning("Memory consolidation LLM call failed: %s", e)
        _fire_failure_callback(f"llm_call_failed:{type(e).__name__}")
        return MemoryAction(action="ADD", reason=f"LLM call failed: {e}")


def mark_superseded(
    neo4j_driver: Any,
    old_artifact_id: str,
    new_artifact_id: str,
) -> None:
    """Mark an existing memory artifact as superseded by a newer one."""
    try:
        with neo4j_driver.session() as session:
            session.run(
                "MATCH (old:Artifact {id: $old_id}) "
                "SET old.superseded_by = $new_id, "
                "    old.valid_until = $now "
                "WITH old "
                "MATCH (new:Artifact {id: $new_id}) "
                "MERGE (new)-[:SUPERSEDES]->(old)",
                old_id=old_artifact_id,
                new_id=new_artifact_id,
                now=utcnow_iso(),
            )
        logger.info(
            "Memory %s superseded by %s", old_artifact_id, new_artifact_id,
        )
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(
            "Failed to mark superseded: %s -> %s: %s",
            old_artifact_id, new_artifact_id, e,
        )
