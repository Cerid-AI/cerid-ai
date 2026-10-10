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

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import config
from core.forget import registry as forget_registry
from core.forget.registry import Subject
from core.retrieval.chunk_ids import chunk_artifact_id


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


# A fact is one version per source memory and holds that memory's text, so it
# goes with its source whether or not the provenance edge survived; a fact from
# before versions, shared by several memories, goes with the last of them.
_FACT_SWEEP = (
    "CALL { MATCH (f:Fact {source_artifact_id: $aid}) RETURN f "
    "UNION MATCH (:Artifact {id: $aid})-[:FACT]->(f:Fact) "
    "WHERE NOT EXISTS { MATCH (o:Artifact)-[:FACT]->(f) WHERE o.id <> $aid } RETURN f } "
    "WITH collect(DISTINCT f) AS fs FOREACH (f IN fs | DETACH DELETE f) RETURN size(fs) AS n"
)
def settle(version_id: str, *, lineage_id: str = "", restoring: frozenset[str] = frozenset()) -> str:
    """Recompute the lineage of ``version_id`` after a forget, restore or purge
    (spec §7): forgetting only the current version makes the one before it
    current, and the rest follows from which versions are forgotten. Versions
    being restored by the same forget count as back already. Returns the
    lineage's current version, or "" when it has no lineage."""
    from app.deps import get_chroma, get_neo4j
    from core.lineage.writer import lineage_of, settle_lineage

    driver = get_neo4j()
    lineage = lineage_id or lineage_of(driver, version_id)
    if not lineage:
        return ""

    def forgotten(vid: str) -> bool:
        if vid in restoring:
            return False
        return forget_registry.is_forgotten("artifact", vid) or forget_registry.is_forgotten("memory", vid)

    return settle_lineage(driver, get_chroma(), lineage, forgotten=forgotten)


def _restoring(forget_id: str) -> frozenset[str]:
    reg = forget_registry.get_registry()
    return frozenset(e.subject.id for e in reg.forget_entries(forget_id) if e.state == "trashed")


class ArtifactAdapter:
    """Memory, summary and KB-document artifacts. Facts whose only source is
    the artifact go with it: deleting the artifact alone would leave them
    orphaned, since provenance is only the [:FACT] edge."""

    name, kinds = "artifacts", frozenset({"artifact"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import hide_content
        from app.services.derived import mark_derived_stale
        hide_content(subject.id, extra_props={"archived_reason": _reason(forget_id)}, only_if_visible=True)
        current = settle(subject.id)
        mark_derived_stale([subject.id, current], forget="trash")

    def restore(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import unhide_content
        from app.services.derived import mark_derived_stale
        unhide_content(subject.id, reason=_reason(forget_id))
        current = settle(subject.id, restoring=_restoring(forget_id))
        mark_derived_stale([subject.id, current])

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_neo4j
        from app.services.content_lifecycle import remove_content
        from app.services.derived import mark_derived_stale
        from core.lineage.writer import lineage_of
        lineage = lineage_of(get_neo4j(), subject.id)
        with get_neo4j().session() as session:
            facts = session.run(_FACT_SWEEP, aid=subject.id).single()
        mark_derived_stale([subject.id], forget="erase")  # before the purge takes its edges
        result = remove_content(subject.id)
        settle(subject.id, lineage_id=lineage)
        n_facts = int(facts["n"]) if facts else 0
        return PurgeResult(removed=(1 if result.found else 0) + n_facts, detail={"facts": n_facts})


def _remove_ids(raw: Any, gone: set[str]) -> list[str] | None:
    """``raw`` (a JSON list of chunk ids) without ``gone``, or None when unchanged."""
    try:
        ids = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    except (ValueError, TypeError):
        return None
    kept = [i for i in ids if i not in gone]
    return kept if len(kept) != len(ids) else None


def _feeds_pages(chunk_id: str) -> bool:
    """Whether forgetting a passage must take down what was written from it.
    Every forget a person makes does, earlier versions included: a passage
    closed by a later version may still be in a page that has not been
    rewritten since. Retention's purges of old versions do not: nobody asked
    for that text to go, and a page refresh would only cost a person's edits."""
    return chunk_id not in forget_registry.get_registry().forgotten_only_by("chunk", forget_registry.RETENTION)


class ChunkAdapter:
    """One passage of a document. Hiding is the read filter's job (every
    retrieval path drops a forgotten chunk and the children of a forgotten
    parent); hide and restore only bust the query caches so a cached answer
    does not outlive the change. Purging removes the chunk and, when it is a
    parent, its children, with their HyPE questions, from every store, and
    takes them out of the artifact's ``chunk_ids`` and its MENTIONS edges."""

    name, kinds = "chunks", frozenset({"chunk"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import invalidate_caches
        from app.services.derived import mark_derived_stale
        invalidate_caches(trigger=f"forget.hide_chunk:{subject.id}")
        if _feeds_pages(subject.id):
            mark_derived_stale([chunk_artifact_id(subject.id)], forget="trash")

    def restore(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import invalidate_caches
        from app.services.derived import mark_derived_stale
        invalidate_caches(trigger=f"forget.restore_chunk:{subject.id}")
        if _feeds_pages(subject.id):
            mark_derived_stale([chunk_artifact_id(subject.id)])

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_chroma, get_neo4j
        from app.services.content_lifecycle import remove_chunks

        artifact_id = chunk_artifact_id(subject.id)
        if not artifact_id:
            return PurgeResult()
        if _feeds_pages(subject.id):
            from app.services.derived import mark_derived_stale
            mark_derived_stale([artifact_id], forget="erase")  # before the purge takes its mentions
        driver = get_neo4j()
        with driver.session() as session:
            node = session.run(
                "MATCH (a:Artifact {id: $aid}) RETURN a.domain AS domain, a.chunk_ids AS ids", aid=artifact_id,
            ).single()
        domain = str(node["domain"] or "") if node else ""
        chroma = get_chroma()
        names = [config.collection_name(domain)] if domain else [
            str(getattr(c, "name", c)) for c in chroma.list_collections()
        ]
        rows: set[str] = set()
        for name in names:
            if name.endswith("_hype"):
                continue
            collection = chroma.get_or_create_collection(name=name)
            got = collection.get(ids=[subject.id], include=["metadatas"])
            if not got.get("ids"):
                continue
            rows.add(subject.id)
            domain = domain or str(((got.get("metadatas") or [{}])[0] or {}).get("domain") or "")
            children = collection.get(where={"parent_chunk_id": subject.id}, include=[])
            rows.update(c for c in children.get("ids") or [] if c.startswith(f"{artifact_id}_"))
        result = remove_chunks(artifact_id, sorted(rows), domain, chroma=chroma) if rows else None
        gone = rows | {subject.id}
        with driver.session() as session:
            ids = _remove_ids(node["ids"], gone) if node else None
            if ids is not None:
                session.run(
                    "MATCH (a:Artifact {id: $aid}) SET a.chunk_ids = $ids, a.chunk_count = $n",
                    aid=artifact_id, ids=json.dumps(ids), n=len(ids),
                )
            for row in list(session.run(
                "MATCH (:Artifact {id: $aid})-[m:MENTIONS]->() RETURN elementId(m) AS rid, m.chunk_ids AS ids",
                aid=artifact_id,
            )):
                kept = _remove_ids(row["ids"], gone)
                if kept is not None:
                    session.run(
                        "MATCH ()-[m:MENTIONS]->() WHERE elementId(m) = $rid SET m.chunk_ids = $ids",
                        rid=row["rid"], ids=json.dumps(kept),
                    )
        return PurgeResult(removed=len(rows), detail={"stores": result.removed if result else {}})


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
        settle(subject.id)
        invalidate_caches(trigger=f"forget.hide_verified_memory:{subject.id}")

    def restore(self, subject: Subject, forget_id: str) -> None:
        from datetime import datetime, timezone

        from app.deps import get_neo4j
        from app.services.content_lifecycle import invalidate_caches
        with get_neo4j().session() as session:
            rec = session.run(
                "MATCH (m:Memory {id: $mid, forget_id: $fid}) RETURN m.text AS text, "
                "m.lineage_id AS lineage_id, m.version AS version, m.valid_to AS valid_to, "
                "m.superseded_by AS superseded_by",
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
                **({"lineage_id": rec.get("lineage_id"), "version": int(rec.get("version") or 1),
                    "valid_to": rec.get("valid_to") or "", "superseded_by": rec.get("superseded_by") or ""}
                   if rec.get("lineage_id") else {}),
            }],
        )
        with get_neo4j().session() as session:
            session.run(
                "MATCH (m:Memory {id: $mid, forget_id: $fid}) SET m.status = 'active' REMOVE m.forget_id",
                mid=subject.id, fid=forget_id,
            )
        settle(subject.id, restoring=_restoring(forget_id))
        invalidate_caches(trigger=f"forget.restore_verified_memory:{subject.id}")

    def purge(self, subject: Subject) -> PurgeResult:
        from app.deps import get_neo4j
        from core.lineage.writer import lineage_of
        lineage = lineage_of(get_neo4j(), subject.id)
        removed = delete_verified_memory(subject.id)
        settle(subject.id, lineage_id=lineage)
        return PurgeResult(removed=1 if removed else 0)


class TranscriptsAdapter:
    name, kinds = "transcripts", frozenset({"conversation"})

    def hide(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import conversation_transcript_artifact_ids, hide_content
        from app.services.derived import mark_derived_stale
        transcripts = conversation_transcript_artifact_ids(subject.id)
        for aid in transcripts:
            hide_content(aid, extra_props={"archived_reason": _reason(forget_id)}, only_if_visible=True)
        # Hidden first, so a marking failure (raised, retried) never leaves them readable;
        # hiding keeps the edges that lead to their pages.
        mark_derived_stale(transcripts, forget="trash")

    def restore(self, subject: Subject, forget_id: str) -> None:
        from app.services.content_lifecycle import conversation_transcript_artifact_ids, unhide_content
        for aid in conversation_transcript_artifact_ids(subject.id):
            unhide_content(aid, reason=_reason(forget_id))

    def purge(self, subject: Subject) -> PurgeResult:
        from app.services.content_lifecycle import conversation_transcript_artifact_ids, remove_conversation_transcripts
        from app.services.derived import mark_derived_stale
        mark_derived_stale(conversation_transcript_artifact_ids(subject.id), forget="erase")
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
    ChunkAdapter(),
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
    "chroma:*:{artifact_id}_{h16}": "chunks",
    "chroma:conversations:session_summary_*": "artifacts",
    "chroma:conversations:verified_memory_*": "verified_memories",
    "neo4j:Conversation.id": "conversation_node",
    "neo4j:Memory (verified)": "verified_memories",
}

OUT_OF_REACH: dict[str, str] = {
    "redis:cerid:proc:job:{jobid}": "job payloads expire after 14 days; queued jobs skip a forgotten conversation (write barrier); phase 6 stores references",
    "redis:ingest:log / verify:*": "analytics lists expire after 30 days",
}
