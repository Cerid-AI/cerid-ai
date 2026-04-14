# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j Memory node operations — create, update, archive, link, and query Memory nodes.

Phase 44 Part 2: Dedicated :Memory node schema for decay/reinforcement scoring,
conflict resolution chains, and context-aware recall.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph")


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def ensure_memory_schema(driver) -> None:
    """Create Memory constraints and indexes. Idempotent — safe to call on every startup."""
    with driver.session() as session:
        # Unique constraint on Memory.id
        session.run(
            "CREATE CONSTRAINT memory_id_unique IF NOT EXISTS "
            "FOR (m:Memory) REQUIRE m.id IS UNIQUE"
        )

        # Indexes for common query patterns
        session.run(
            "CREATE INDEX memory_status_idx IF NOT EXISTS "
            "FOR (m:Memory) ON (m.status)"
        )
        session.run(
            "CREATE INDEX memory_created_at_idx IF NOT EXISTS "
            "FOR (m:Memory) ON (m.created_at)"
        )
        session.run(
            "CREATE INDEX memory_source_idx IF NOT EXISTS "
            "FOR (m:Memory) ON (m.source)"
        )
        session.run(
            "CREATE INDEX memory_last_accessed_idx IF NOT EXISTS "
            "FOR (m:Memory) ON (m.last_accessed_at)"
        )

    logger.info("Memory schema ensured (constraint + 4 indexes)")


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


def create_memory_node(driver, memory_data: dict[str, Any]) -> str:
    """Create a :Memory node in Neo4j. Returns the memory ID.

    Expected keys in memory_data:
        text (str): The memory content (required)
        source (str): "conversation" | "extraction" | "manual" (default: "extraction")
        confidence (float): Extraction confidence 0-1 (default: 1.0)
        base_score (float): Original extraction score (default: 1.0)
        conversation_id (str): Optional — creates EXTRACTED_FROM relationship
        artifact_id (str): Optional — creates RELATES_TO relationship
    """
    memory_id = memory_data.get("id") or str(uuid.uuid4())
    text = memory_data.get("text", "")
    source = memory_data.get("source", "extraction")
    memory_type = memory_data.get("memory_type", "decision")
    confidence = float(memory_data.get("confidence", 1.0))
    base_score = float(memory_data.get("base_score", 1.0))
    now = utcnow_iso()

    with driver.session() as session:
        session.run(
            "CREATE (m:Memory {"
            "  id: $id,"
            "  text: $text,"
            "  source: $source,"
            "  memory_type: $memory_type,"
            "  confidence: $confidence,"
            "  access_count: 0,"
            "  base_score: $base_score,"
            "  created_at: $now,"
            "  last_accessed_at: $now,"
            "  decay_anchor: $now,"
            "  status: 'active'"
            "})",
            id=memory_id,
            text=text,
            source=source,
            memory_type=memory_type,
            confidence=confidence,
            base_score=base_score,
            now=now,
        )

        # Optional: link to conversation
        conversation_id = memory_data.get("conversation_id")
        if conversation_id:
            session.run(
                "MATCH (m:Memory {id: $mid}) "
                "MERGE (c:Conversation {id: $cid}) "
                "MERGE (m)-[:EXTRACTED_FROM]->(c)",
                mid=memory_id,
                cid=conversation_id,
            )

        # Optional: link to artifact
        artifact_id = memory_data.get("artifact_id")
        if artifact_id:
            session.run(
                "MATCH (m:Memory {id: $mid}) "
                "MATCH (a:Artifact {id: $aid}) "
                "MERGE (m)-[:RELATES_TO]->(a)",
                mid=memory_id,
                aid=artifact_id,
            )

    logger.debug("Created Memory node %s (source=%s)", memory_id, source)
    return memory_id


def update_memory_access(driver, memory_id: str) -> None:
    """Increment access_count, update last_accessed_at, and reset decay_anchor.

    The decay_anchor reset implements refresh-on-read: active retrieval of a
    memory resets its decay timer, preventing useful-but-old memories from
    decaying despite frequent use (Ebbinghaus rehearsal pattern).
    """
    now = utcnow_iso()
    with driver.session() as session:
        result = session.run(
            "MATCH (m:Memory {id: $mid}) "
            "SET m.access_count = coalesce(m.access_count, 0) + 1, "
            "    m.last_accessed_at = $now, "
            "    m.decay_anchor = $now "
            "RETURN m.access_count AS count",
            mid=memory_id,
            now=now,
        )
        record = result.single()
        if record is None:
            logger.warning("update_memory_access: Memory %s not found", memory_id)


def archive_memory(driver, memory_id: str, reason: str = "") -> None:
    """Set status='archived' and record reason."""
    now = utcnow_iso()
    with driver.session() as session:
        result = session.run(
            "MATCH (m:Memory {id: $mid}) "
            "SET m.status = 'archived', "
            "    m.archived_at = $now, "
            "    m.archive_reason = $reason "
            "RETURN m.id AS id",
            mid=memory_id,
            now=now,
            reason=reason,
        )
        record = result.single()
        if record is None:
            logger.warning("archive_memory: Memory %s not found", memory_id)
        else:
            logger.debug("Archived Memory %s: %s", memory_id, reason)


def link_memory_to_artifact(driver, memory_id: str, artifact_id: str) -> None:
    """Create RELATES_TO relationship between a Memory and an Artifact."""
    with driver.session() as session:
        result = session.run(
            "MATCH (m:Memory {id: $mid}) "
            "MATCH (a:Artifact {id: $aid}) "
            "MERGE (m)-[:RELATES_TO]->(a) "
            "RETURN m.id AS mid, a.id AS aid",
            mid=memory_id,
            aid=artifact_id,
        )
        record = result.single()
        if record is None:
            logger.warning(
                "link_memory_to_artifact: Memory %s or Artifact %s not found",
                memory_id,
                artifact_id,
            )


def supersede_memory(driver, old_memory_id: str, new_memory_id: str) -> None:
    """Create SUPERSEDES relationship: new_memory supersedes old_memory."""
    with driver.session() as session:
        session.run(
            "MATCH (new:Memory {id: $new_id}) "
            "MATCH (old:Memory {id: $old_id}) "
            "MERGE (new)-[:SUPERSEDES]->(old)",
            new_id=new_memory_id,
            old_id=old_memory_id,
        )


def merge_memory(driver, source_memory_id: str, target_memory_id: str) -> None:
    """Create MERGED_INTO relationship: source_memory merged into target_memory."""
    now = utcnow_iso()
    with driver.session() as session:
        session.run(
            "MATCH (src:Memory {id: $src_id}) "
            "MATCH (tgt:Memory {id: $tgt_id}) "
            "MERGE (src)-[:MERGED_INTO]->(tgt) "
            "SET src.status = 'merged', src.merged_at = $now",
            src_id=source_memory_id,
            tgt_id=target_memory_id,
            now=now,
        )


# ---------------------------------------------------------------------------
# Graph queries
# ---------------------------------------------------------------------------


def get_memory_graph(driver, memory_id: str) -> dict[str, Any]:
    """Get a memory with its relationships (artifacts, conversations, supersedes chain).

    Returns a dict with:
        memory: dict of Memory node properties (or None if not found)
        artifacts: list of related Artifact summaries
        conversations: list of linked Conversation IDs
        supersedes: list of Memory IDs this memory supersedes
        superseded_by: list of Memory IDs that supersede this one
        merged_into: Memory ID this was merged into (or None)
    """
    with driver.session() as session:
        # Fetch the memory node
        mem_result = session.run(
            "MATCH (m:Memory {id: $mid}) "
            "RETURN m {.*} AS memory",
            mid=memory_id,
        )
        mem_record = mem_result.single()
        if mem_record is None:
            return {"memory": None}

        memory = dict(mem_record["memory"])

        # Related artifacts
        art_result = session.run(
            "MATCH (m:Memory {id: $mid})-[:RELATES_TO]->(a:Artifact) "
            "RETURN a.id AS id, a.filename AS filename, a.domain AS domain "
            "LIMIT 50",
            mid=memory_id,
        )
        artifacts = [dict(r) for r in art_result]

        # Linked conversations
        conv_result = session.run(
            "MATCH (m:Memory {id: $mid})-[:EXTRACTED_FROM]->(c:Conversation) "
            "RETURN c.id AS id",
            mid=memory_id,
        )
        conversations = [r["id"] for r in conv_result]

        # Supersedes chain (memories this one replaces)
        sup_result = session.run(
            "MATCH (m:Memory {id: $mid})-[:SUPERSEDES]->(old:Memory) "
            "RETURN old.id AS id",
            mid=memory_id,
        )
        supersedes = [r["id"] for r in sup_result]

        # Superseded by (memories that replace this one)
        sup_by_result = session.run(
            "MATCH (newer:Memory)-[:SUPERSEDES]->(m:Memory {id: $mid}) "
            "RETURN newer.id AS id",
            mid=memory_id,
        )
        superseded_by = [r["id"] for r in sup_by_result]

        # Merged into
        merge_result = session.run(
            "MATCH (m:Memory {id: $mid})-[:MERGED_INTO]->(tgt:Memory) "
            "RETURN tgt.id AS id",
            mid=memory_id,
        )
        merge_record = merge_result.single()
        merged_into = merge_record["id"] if merge_record else None

    return {
        "memory": memory,
        "artifacts": artifacts,
        "conversations": conversations,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "merged_into": merged_into,
    }
