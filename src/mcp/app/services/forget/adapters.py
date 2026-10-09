# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Per-store forget adapters.

Order is purge order: derived stores first, the conversation record last, so
a failure part-way never leaves derived data pointing at nothing. The engine
also orders subjects (artifacts and memories before the conversation). Each purge is
idempotent. Hiding is needed only where a store serves reads without consulting
the registry: transcripts are archived through the content-lifecycle
coordinator, which every retrieval arm already filters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import config
from core.forget.registry import Subject


@dataclass
class PurgeResult:
    removed: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


class ForgetAdapter(Protocol):
    name: str
    kinds: frozenset[str]

    def hide(self, subject: Subject, forget_id: str) -> None: ...
    def restore(self, subject: Subject, forget_id: str) -> None: ...
    def purge(self, subject: Subject) -> PurgeResult: ...


def _reason(forget_id: str) -> str:
    return f"forget:{forget_id}"


_FACT_SWEEP = (
    "MATCH (a:Artifact {id: $aid})-[:FACT]->(f:Fact) "
    "WHERE NOT EXISTS { MATCH (o:Artifact)-[:FACT]->(f) WHERE o.id <> $aid } "
    "WITH collect(f) AS fs, count(f) AS n FOREACH (f IN fs | DETACH DELETE f) RETURN n"
)
# Forgetting the newer of a superseded pair makes the older current again (spec §7).
_UNSUPERSEDE = (
    "MATCH (old:Artifact {superseded_by: $aid}) "
    "REMOVE old.superseded_by, old.valid_until RETURN count(old) AS n"
)


class ArtifactAdapter:
    """Memory, summary and KB-document artifacts. Facts whose only source is
    the artifact go with it: deleting the artifact alone would leave them
    orphaned, since provenance is only the [:FACT] edge."""

    name, kinds = "artifacts", frozenset({"artifact"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import hide_content
        hide_content(subject.id, extra_props={"archived_reason": _reason(forget_id)}, only_if_visible=True)

    def restore(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import unhide_content
        unhide_content(subject.id, reason=_reason(forget_id))

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_neo4j
        from app.services.content_lifecycle import remove_content
        with get_neo4j().session() as session:
            facts = session.run(_FACT_SWEEP, aid=subject.id).single()
            session.run(_UNSUPERSEDE, aid=subject.id)
        result = remove_content(subject.id)
        n_facts = int(facts["n"]) if facts else 0
        return PurgeResult(removed=(1 if result.found else 0) + n_facts, detail={"facts": n_facts})


def verified_memory_ids_for_conversation(conversation_id: str) -> list[str]:
    """Verified memories linked to the conversation's report. A memory promoted
    before its report was saved has no link and cannot be found this way."""
    from app.deps import get_neo4j
    with get_neo4j().session() as session:
        rows = session.run(
            "MATCH (m:Memory)-[:VERIFIED_BY]->(:VerificationReport {conversation_id: $cid}) "
            "RETURN DISTINCT m.id AS id",
            cid=conversation_id,
        )
        return [r["id"] for r in rows if r["id"]]


def _verified_collection() -> Any:
    from app.deps import get_chroma
    return get_chroma().get_or_create_collection(name=config.collection_name("conversations"))


def delete_verified_memory(memory_id: str) -> bool:
    """Delete one verified memory's recall document and node. These have no
    chunk ids to fan out from, so they bypass remove_content and bust the
    query caches here."""
    from app.deps import get_neo4j
    from app.services.content_lifecycle import invalidate_caches
    _verified_collection().delete(ids=[f"verified_memory_{memory_id}"])
    with get_neo4j().session() as session:
        rec = session.run(
            "MATCH (m:Memory {id: $mid}) WITH collect(m) AS ms, count(m) AS n "
            "FOREACH (m IN ms | DETACH DELETE m) RETURN n",
            mid=memory_id,
        ).single()
    invalidate_caches(trigger=f"forget.verified_memory:{memory_id}")
    return bool(rec and rec["n"])


class VerifiedMemoryAdapter:
    """Verified-claim memories: a :Memory node plus one Chroma document.
    Hiding removes the document recall reads and marks the node; the node
    keeps the text, so restore rebuilds the document."""

    name, kinds = "verified_memories", frozenset({"memory"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        from app.deps import get_neo4j
        from app.services.content_lifecycle import invalidate_caches
        with get_neo4j().session() as session:
            session.run(
                "MATCH (m:Memory {id: $mid}) WHERE coalesce(m.status, 'active') = 'active' "
                "SET m.status = 'forgotten', m.forget_id = $fid",
                mid=subject.id, fid=forget_id,
            )
        _verified_collection().delete(ids=[f"verified_memory_{subject.id}"])
        invalidate_caches(trigger=f"forget.hide_verified_memory:{subject.id}")

    def restore(self, subject: Subject, forget_id: str) -> None:
        from datetime import datetime, timezone

        from app.deps import get_neo4j
        from app.services.content_lifecycle import invalidate_caches
        with get_neo4j().session() as session:
            rec = session.run(
                "MATCH (m:Memory {id: $mid, forget_id: $fid}) RETURN m.text AS text",
                mid=subject.id, fid=forget_id,
            ).single()
        if not rec or not rec["text"]:
            return
        # The document first: if it fails, the node is still marked with this
        # forget, so a retried restore finds it again.
        now_iso = datetime.now(timezone.utc).isoformat()
        _verified_collection().upsert(
            ids=[f"verified_memory_{subject.id}"],
            documents=[rec["text"]],
            metadatas=[{
                "artifact_id": subject.id,
                "memory_type": "empirical",
                "memory_source_type": "verification",
                "domain": "conversations",
                "filename": f"verified_fact_{subject.id[:8]}",
                "ingested_at": now_iso,
                "decay_anchor": now_iso,
            }],
        )
        with get_neo4j().session() as session:
            session.run(
                "MATCH (m:Memory {id: $mid, forget_id: $fid}) SET m.status = 'active' REMOVE m.forget_id",
                mid=subject.id, fid=forget_id,
            )
        invalidate_caches(trigger=f"forget.restore_verified_memory:{subject.id}")

    def purge(self, subject: Subject) -> PurgeResult:
        return PurgeResult(removed=1 if delete_verified_memory(subject.id) else 0)


class TranscriptsAdapter:
    name, kinds = "transcripts", frozenset({"conversation"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import conversation_transcript_artifact_ids, hide_content
        for aid in conversation_transcript_artifact_ids(subject.id):
            hide_content(aid, extra_props={"archived_reason": _reason(forget_id)}, only_if_visible=True)

    def restore(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import conversation_transcript_artifact_ids, unhide_content
        for aid in conversation_transcript_artifact_ids(subject.id):
            unhide_content(aid, reason=_reason(forget_id))

    def purge(self, subject: Subject) -> PurgeResult:
        from app.services.content_lifecycle import remove_conversation_transcripts
        results = remove_conversation_transcripts(subject.id)
        return PurgeResult(removed=sum(1 for r in results if r.found))


class VerificationReportGraphAdapter:
    name, kinds = "verification_report_graph", frozenset({"conversation"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        return None

    def restore(self, subject: Subject, forget_id: str) -> None:
        return None

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_neo4j
        with get_neo4j().session() as session:
            record = session.run(
                "MATCH (r:VerificationReport {conversation_id: $cid}) "
                "WITH collect(r) AS rs, count(r) AS n "
                "FOREACH (r IN rs | DETACH DELETE r) RETURN n",
                cid=subject.id,
            ).single()
        return PurgeResult(removed=int(record["n"]) if record else 0)


class RedisKeysAdapter:
    """Conversation-keyed Redis keys: the hall: report, turn metrics, sentiment,
    and the private-mode session flag (plus its membership in the L4 set)."""

    name, kinds = "redis_conversation_keys", frozenset({"conversation"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        return None

    def restore(self, subject: Subject, forget_id: str) -> None:
        return None

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_redis
        from core.agents.hallucination.persistence import REDIS_HALLUCINATION_PREFIX
        from core.utils.cache import REDIS_CONV_METRICS_PREFIX

        cid = subject.id
        redis = get_redis()
        keys = [
            f"{REDIS_HALLUCINATION_PREFIX}{cid}",
            f"{REDIS_CONV_METRICS_PREFIX}{cid}:metrics",
            f"{REDIS_CONV_METRICS_PREFIX}{cid}:sentiment",
            f"cerid:private_mode:session:{cid}",
        ]
        removed = int(redis.delete(*keys) or 0)
        removed += int(redis.zrem("cerid:private_mode:l4_sessions", cid) or 0)
        return PurgeResult(removed=removed)


class SyncFileAdapter:
    name, kinds = "sync_file", frozenset({"conversation"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        return None

    def restore(self, subject: Subject, forget_id: str) -> None:
        return None

    def purge(self, subject: Subject) -> PurgeResult:
        from app.sync.user_state import delete_conversation, read_conversation
        if not config.SYNC_DIR:
            return PurgeResult()
        existed = bool(read_conversation(config.SYNC_DIR, subject.id))
        delete_conversation(config.SYNC_DIR, subject.id)
        return PurgeResult(removed=1 if existed else 0)


class ConversationNodeAdapter:
    """The bare :Conversation node, kept while any memory the user chose to
    keep still hangs off it."""

    name, kinds = "conversation_node", frozenset({"conversation"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        return None

    def restore(self, subject: Subject, forget_id: str) -> None:
        return None

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_neo4j
        with get_neo4j().session() as session:
            rec = session.run(
                "MATCH (c:Conversation {id: $cid}) WHERE NOT ()-[:EXTRACTED_FROM]->(c) "
                "WITH collect(c) AS cs, count(c) AS n FOREACH (c IN cs | DETACH DELETE c) RETURN n",
                cid=subject.id,
            ).single()
        return PurgeResult(removed=int(rec["n"]) if rec else 0)


ADAPTERS: list[ForgetAdapter] = [
    ArtifactAdapter(),
    VerifiedMemoryAdapter(),
    TranscriptsAdapter(),
    VerificationReportGraphAdapter(),
    RedisKeysAdapter(),
    SyncFileAdapter(),
    ConversationNodeAdapter(),
]

# Store patterns that hold conversation-keyed data, and the adapter that owns
# each. scripts/tests/test_forget_coverage.py fails when the source tree writes
# a conversation-keyed pattern that is neither claimed here nor in OUT_OF_REACH.
CLAIMS: dict[str, str] = {
    "chroma:conversations:chat_*": "transcripts",
    "neo4j:VerificationReport.conversation_id": "verification_report_graph",
    "redis:hall:{cid}": "redis_conversation_keys",
    "redis:conv:{cid}:metrics": "redis_conversation_keys",
    "redis:conv:{cid}:sentiment": "redis_conversation_keys",
    "redis:cerid:private_mode:session:{cid}": "redis_conversation_keys",
    "sync:user/conversations/{cid}.json": "sync_file",
    "chroma:conversations:memory_*": "artifacts",
    "chroma:conversations:session_summary_*": "artifacts",
    "chroma:conversations:verified_memory_*": "verified_memories",
    "neo4j:Conversation.id": "conversation_node",
    "neo4j:Memory (verified)": "verified_memories",
}

OUT_OF_REACH: dict[str, str] = {
    "redis:cerid:proc:job:{jobid}": "job payloads expire after 14 days; queued jobs skip a forgotten conversation (write barrier); phase 6 stores references",
    "redis:ingest:log / verify:*": "analytics lists expire after 30 days",
}
