# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""L4 ("Full ephemeral") session-wipe orchestrator.

``POST /settings/private-mode/session-wipe`` (``app/routers/settings.py``)
promises to erase whatever persisted for a conversation. L1 (task 1.1)
already blocks new conversation saves, memory extraction, and feedback
writes server-side, so during a true L4 session nothing reaches these
stores in the first place — this module exists for the window BEFORE a
conversation escalated to L4 (e.g. a conversation saved at L0/L1 that the
user later bumps to full-ephemeral). Four store-touching steps, each
best-effort — one step's failure must not block the others:

1. The conversation itself, via the sync-directory JSON store
   (:func:`app.sync.user_state.delete_conversation`).
2. Memory artifacts extracted from the conversation — ``(:Artifact)
   -[:EXTRACTED_FROM]->(:Conversation {id})`` in Neo4j (confirmed against
   the write path in ``core/agents/memory.py``'s ``_link_extracted_from``).
   Each artifact is deleted from BOTH Neo4j and Chroma by reusing
   :func:`app.services.retention.apply_retention_plan` — the same
   both-stores purge helper the nightly retention job uses — rather than
   hand-rolling a second Chroma delete path.
3. The ``:Conversation`` node itself and its ``:VerificationReport``.
4. Verified-memory ``:Memory`` nodes (``app/db/neo4j/memory.py``) created
   by the hallucination-verification pipeline's promotion path
   (``core.agents.verified_memory.promote_verified_facts``, injected via
   ``create_memory_fn`` in ``app/routers/agents.py``), found via the
   ``VERIFIED_BY`` relationship to this conversation's
   ``:VerificationReport``. These carry no Chroma ``chunk_ids`` fan-out
   (unlike ``:Artifact`` memories) — the promotion path instead writes one
   deterministic companion document per memory (``verified_memory_{id}``
   in the "conversations" collection), so they're purged by a dedicated
   helper rather than :func:`app.services.retention.apply_retention_plan`.
   Deleted *before* the ``:VerificationReport`` node so the ``VERIFIED_BY``
   correlator survives long enough to find them. As of task 1.2b,
   promotion is also blocked server-side at L1+ (private mode), so this
   step only matters for memories written before a conversation escalated
   to L4.

Redis mode-flag deletes stay in the router (``app/routers/settings.py``)
since they were already correct and don't need a store-touching helper.
"""
from __future__ import annotations

import logging
from typing import Any

import config
from app.services.retention import apply_retention_plan
from app.sync.user_state import delete_conversation
from core.ingest.retention import RetentionDecision
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.services.session_wipe")


def _find_extracted_memory_artifact_ids(driver: Any, conversation_id: str) -> list[str]:
    """Return ids of ``:Artifact`` memories linked ``EXTRACTED_FROM`` this conversation."""
    with driver.session() as session:
        rows = session.run(
            "MATCH (a:Artifact)-[:EXTRACTED_FROM]->(:Conversation {id: $cid}) "
            "RETURN a.id AS id",
            cid=conversation_id,
        )
        return [r["id"] for r in rows if r.get("id")]


def _delete_conversation_node(driver: Any, conversation_id: str) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (c:Conversation {id: $cid}) DETACH DELETE c",
            cid=conversation_id,
        )


def _delete_verification_report_node(driver: Any, conversation_id: str) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (r:VerificationReport {conversation_id: $cid}) DETACH DELETE r",
            cid=conversation_id,
        )


def _find_verified_memory_ids(driver: Any, conversation_id: str) -> list[str]:
    """Return ids of verified ``:Memory`` nodes linked to this conversation's
    ``:VerificationReport`` via ``VERIFIED_BY`` (see
    ``core.agents.verified_memory.promote_verified_facts``).

    Must be called — and its results deleted — before
    :func:`_delete_verification_report_node` runs, or the correlator this
    query depends on is gone.
    """
    with driver.session() as session:
        rows = session.run(
            "MATCH (m:Memory)-[:VERIFIED_BY]->(:VerificationReport {conversation_id: $cid}) "
            "RETURN m.id AS id",
            cid=conversation_id,
        )
        return [r["id"] for r in rows if r.get("id")]


def _delete_verified_memory(driver: Any, memory_id: str) -> None:
    """Delete one verified ``:Memory`` node and its Chroma companion doc.

    Unlike ``:Artifact`` memories, these carry no ``chunk_ids`` property to
    fan out from — ``promote_verified_facts`` writes exactly one Chroma
    document per memory, at the deterministic id ``verified_memory_{id}``
    in the "conversations" collection, computed independently of any
    Neo4j-node property. Both stores hold the raw claim text, so neither
    delete is "safer" to run first; the Chroma delete runs first here only
    because the id is already known and doesn't need to be read back from
    the Neo4j node before it disappears.
    """
    from app.deps import get_chroma

    collection = get_chroma().get_or_create_collection(
        name=config.collection_name("conversations")
    )
    collection.delete(ids=[f"verified_memory_{memory_id}"])

    with driver.session() as session:
        session.run(
            "MATCH (m:Memory {id: $mid}) DETACH DELETE m",
            mid=memory_id,
        )


def wipe_conversation_state(
    conversation_id: str,
    *,
    sync_dir: str | None,
    neo4j_driver: Any | None,
    redis_client: Any | None = None,
) -> dict[str, Any]:
    """Best-effort erase of whatever persisted for ``conversation_id``.

    Every step is independently wrapped in ``try/except`` — a failure in
    one store is logged via :func:`log_swallowed_error` and does not
    prevent the remaining steps from running. ``sync_dir`` /
    ``neo4j_driver`` being falsy/``None`` skips that store's steps
    entirely (e.g. no sync directory configured, or Neo4j unreachable).

    Returns a per-store summary dict for the caller's log line. This is
    never surfaced to the HTTP client — the wipe endpoint's response
    contract is a stable ``{wiped, level_after, conversation_id}`` shape
    regardless of how much was actually found to delete.
    """
    summary: dict[str, Any] = {
        "conversation_sync_deleted": False,
        "memory_artifacts_deleted": 0,
        "memory_artifacts_failed": 0,
        "conversation_node_deleted": False,
        "verified_memories_deleted": 0,
        "verified_memories_failed": 0,
        "verification_report_deleted": False,
        "hallucination_cache_deleted": False,
    }

    # E1 CR-012: the durable Redis hall:{cid} verification report — verbatim
    # claims + source snippets — survived a wipe for its 7-day TTL because only
    # the Neo4j :VerificationReport node was deleted below. Clear it too.
    if redis_client is not None:
        try:
            from core.agents.hallucination import delete_hallucination_report
            summary["hallucination_cache_deleted"] = delete_hallucination_report(
                redis_client, conversation_id
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
            log_swallowed_error(
                "session_wipe.hallucination_cache", exc,
                context={"conversation_id": conversation_id},
            )

    if sync_dir:
        try:
            delete_conversation(sync_dir, conversation_id)
            summary["conversation_sync_deleted"] = True
        except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
            log_swallowed_error(
                "session_wipe.conversation_sync", exc,
                context={"conversation_id": conversation_id},
            )

    if neo4j_driver is not None:
        try:
            artifact_ids = _find_extracted_memory_artifact_ids(neo4j_driver, conversation_id)
        except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
            log_swallowed_error(
                "session_wipe.memory_lookup", exc,
                context={"conversation_id": conversation_id},
            )
            artifact_ids = []

        for artifact_id in artifact_ids:
            try:
                decision = RetentionDecision(
                    source_id=conversation_id, purge=[artifact_id], keep_count=0,
                )
                apply_retention_plan(neo4j_driver, decision)
                summary["memory_artifacts_deleted"] += 1
            except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
                summary["memory_artifacts_failed"] += 1
                log_swallowed_error(
                    "session_wipe.memory_artifact_delete", exc,
                    context={"conversation_id": conversation_id, "artifact_id": artifact_id},
                )

        try:
            _delete_conversation_node(neo4j_driver, conversation_id)
            summary["conversation_node_deleted"] = True
        except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
            log_swallowed_error(
                "session_wipe.conversation_node", exc,
                context={"conversation_id": conversation_id},
            )

        # Verified-memory :Memory nodes must be found and deleted BEFORE the
        # :VerificationReport node below — the VERIFIED_BY lookup depends on
        # the report still existing (see module docstring, point 4).
        try:
            verified_memory_ids = _find_verified_memory_ids(neo4j_driver, conversation_id)
        except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
            log_swallowed_error(
                "session_wipe.verified_memory_lookup", exc,
                context={"conversation_id": conversation_id},
            )
            verified_memory_ids = []

        for memory_id in verified_memory_ids:
            try:
                _delete_verified_memory(neo4j_driver, memory_id)
                summary["verified_memories_deleted"] += 1
            except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
                summary["verified_memories_failed"] += 1
                log_swallowed_error(
                    "session_wipe.verified_memory_delete", exc,
                    context={"conversation_id": conversation_id, "memory_id": memory_id},
                )

        try:
            _delete_verification_report_node(neo4j_driver, conversation_id)
            summary["verification_report_deleted"] = True
        except Exception as exc:  # noqa: BLE001 — best-effort per store, see module docstring
            log_swallowed_error(
                "session_wipe.verification_report", exc,
                context={"conversation_id": conversation_id},
            )

    return summary
