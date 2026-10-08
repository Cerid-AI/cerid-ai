# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Per-store forget adapters for conversation subjects (phase 1).

Order is purge order: derived stores first, the conversation record last, so
a failure part-way never leaves derived data pointing at nothing. Each purge is
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


CONVERSATION_ADAPTERS: list[ForgetAdapter] = [
    TranscriptsAdapter(),
    VerificationReportGraphAdapter(),
    RedisKeysAdapter(),
    SyncFileAdapter(),
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
}

OUT_OF_REACH: dict[str, str] = {
    "chroma:conversations:memory_*": "derived memory; offered in the phase 2 delete dialog",
    "chroma:conversations:session_summary_*": "derived summary; offered in the phase 2 delete dialog",
    "neo4j:Conversation.id": "kept while extracted memories reference it; phase 2",
    "neo4j:Memory (verified)": "derived memory; phase 2",
    "redis:cerid:proc:job:{jobid}": "job payloads expire after 14 days; queued jobs skip a forgotten conversation (write barrier); phase 6 stores references",
    "redis:ingest:log / verify:*": "analytics lists expire after 30 days",
}
