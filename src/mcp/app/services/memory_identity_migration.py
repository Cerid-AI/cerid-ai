# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""One memory identity (forget spec §6.3), for memories written before it.

Every memory's recall rows are ``{id}_{h16}``, where ``id`` is its ``:Artifact``
or ``:Memory`` id, and every memory names its conversation on the node. This
re-keys the verified-memory documents still at ``verified_memory_{id}`` (an old
sync export brings them back, so it also runs after each import), writes their
conversation onto them, and copies each memory's conversation from its
EXTRACTED_FROM edge, or a verified memory's report link, onto the node. A
second run changes nothing.
"""
from __future__ import annotations

import logging
from typing import Any

import config
from core.forget import registry as forget_registry
from core.retrieval.artifact_rows import LEGACY_VERIFIED_MEMORY_PREFIX, verified_memory_row_id

logger = logging.getLogger("ai-companion.memory_identity_migration")

_ARTIFACT_CONVERSATIONS = """
MATCH (a:Artifact)-[:EXTRACTED_FROM]->(c:Conversation) WHERE a.conversation_id IS NULL
WITH a, head(collect(c.id)) AS cid
SET a.conversation_id = cid
RETURN count(a) AS n
"""

_VERIFIED_CONVERSATIONS = """
MATCH (m:Memory) WHERE m.conversation_id IS NULL
OPTIONAL MATCH (m)-[:EXTRACTED_FROM]->(c:Conversation)
OPTIONAL MATCH (m)-[:VERIFIED_BY]->(r:VerificationReport)
WITH m, coalesce(head(collect(DISTINCT c.id)), head(collect(DISTINCT r.conversation_id))) AS cid
WHERE cid IS NOT NULL AND cid <> ''
MERGE (conv:Conversation {id: cid})
MERGE (m)-[:EXTRACTED_FROM]->(conv)
SET m.conversation_id = cid
RETURN count(m) AS n
"""

_MEMORY_CONVERSATIONS = "UNWIND $ids AS i MATCH (m:Memory {id: i}) RETURN m.id AS id, m.conversation_id AS cid"


def _legacy_rows(collection: Any) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    got = collection.get(where={"memory_source_type": "verification"}, include=["documents", "metadatas"])
    rows = [
        (cid, doc or "", dict(meta or {}))
        for cid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or [])
        if str(cid).startswith(LEGACY_VERIFIED_MEMORY_PREFIX)
    ]
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


def migrate_memory_identity(*, chroma: Any = None, neo4j: Any = None) -> dict[str, int]:
    from app.deps import get_chroma, get_neo4j
    from app.services.chunk_id_migration import _rekey_chroma

    driver = neo4j or get_neo4j()
    chroma = chroma or get_chroma()
    counts = {"artifact_conversations": 0, "verified_conversations": 0, "verified_rows": 0}
    with driver.session() as session:
        counts["artifact_conversations"] = int(session.run(_ARTIFACT_CONVERSATIONS).single()["n"])
        counts["verified_conversations"] = int(session.run(_VERIFIED_CONVERSATIONS).single()["n"])

    collection = chroma.get_or_create_collection(name=config.collection_name("conversations"))
    ids, docs, metas = _legacy_rows(collection)
    if ids:
        memory_ids = [str(m.get("artifact_id") or i.removeprefix(LEGACY_VERIFIED_MEMORY_PREFIX)) for i, m in zip(ids, metas)]
        with driver.session() as session:
            conversation = {r["id"]: r["cid"] for r in session.run(_MEMORY_CONVERSATIONS, ids=memory_ids)}
        mapping = {old: verified_memory_row_id(mid, doc) for old, mid, doc in zip(ids, memory_ids, docs)}
        # A row already at its new id is the newer one (a restore, a later
        # import): the old row only goes. A memory forgotten meanwhile is not
        # written back; its old row goes with the rest.
        held = set(collection.get(ids=list(mapping.values()), include=[]).get("ids") or [])
        moving = {
            old: new for (old, new), mid in zip(mapping.items(), memory_ids)
            if new not in held and not forget_registry.is_forgotten("memory", mid)
        }
        counts["verified_rows"], _ = _rekey_chroma(collection, moving, set()) if moving else (0, "")
        stale = [old for old in ids if old not in moving]
        if stale:
            collection.delete(ids=stale)
        tagged = [
            (moving[old], {**meta, "artifact_id": mid, **({"conversation_id": conversation[mid]} if conversation.get(mid) else {})})
            for old, mid, meta in zip(ids, memory_ids, metas) if old in moving
        ]
        if tagged:
            collection.update(ids=[t[0] for t in tagged], metadatas=[t[1] for t in tagged])
    if any(counts.values()):
        logger.info("memory identity migration: %s", counts)
    return counts
