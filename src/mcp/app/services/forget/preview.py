# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""What forgetting a conversation would remove, grouped for the delete dialog.

Derived items (memories, the summary, verified memories) default to checked;
documents the conversation cited default to unchecked, and a memory another
live conversation also produced defaults to unchecked. Labels are read live and
returned to the caller only; nothing here is stored.
"""
from __future__ import annotations

from typing import Any

import config
from app.services.forget.adapters import verified_memory_ids_for_conversation
from app.services.forget.engine import OUT_OF_REACH_NOTES
from core.forget import registry as forget_registry

_LABEL_MAX = 160


def _live(kind: str, sid: str) -> bool:
    return not forget_registry.is_forgotten(kind, sid)


def _sharer(cid: str, forgetting: frozenset[str]) -> bool:
    """Another conversation that still holds a claim on a memory. A trashed one
    counts (it can be restored); a purged one, or one being forgotten in the
    same request, does not."""
    return cid not in forgetting and forget_registry.get_registry().state_of("conversation", cid) != "purged"


def _label(text: Any, fallback: str) -> str:
    s = str(text or "").strip() or fallback
    return s if len(s) <= _LABEL_MAX else s[: _LABEL_MAX - 1] + "…"


def _derived(session: Any, cid: str) -> list[dict[str, Any]]:
    rows = session.run(
        "MATCH (a:Artifact)-[:EXTRACTED_FROM]->(:Conversation {id: $cid}) "
        "OPTIONAL MATCH (a)-[:EXTRACTED_FROM]->(o:Conversation) WHERE o.id <> $cid "
        "RETURN a.id AS id, a.filename AS filename, a.summary AS summary, "
        "coalesce(a.memory_scope, '') AS scope, collect(DISTINCT o.id) AS others",
        cid=cid,
    )
    return [dict(r) for r in rows]


def _verified(session: Any, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = session.run("MATCH (m:Memory) WHERE m.id IN $ids RETURN m.id AS id, m.text AS text", ids=ids)
    return [dict(r) for r in rows]


def _fact_count(session: Any, aids: list[str]) -> int:
    if not aids:
        return 0
    rec = session.run(
        "MATCH (a:Artifact)-[:FACT]->(f:Fact) WHERE a.id IN $aids "
        "AND NOT EXISTS { MATCH (o:Artifact)-[:FACT]->(f) WHERE NOT o.id IN $aids } "
        "RETURN count(DISTINCT f) AS n",
        aids=aids,
    ).single()
    return int(rec["n"]) if rec else 0


def _cited(conversation: dict[str, Any]) -> dict[str, str]:
    """KB artifacts the conversation's answers used, with a display name."""
    cited: dict[str, str] = {}
    for msg in conversation.get("messages") or []:
        for ref in (msg or {}).get("sourcesUsed") or []:
            if not isinstance(ref, dict) or ref.get("source_type", "kb") != "kb":
                continue
            aid = ref.get("artifact_id")
            if aid:
                cited.setdefault(str(aid), str(ref.get("filename") or aid))
    return cited


def preview_conversation(cid: str, forgetting: frozenset[str] = frozenset()) -> dict[str, Any]:
    from app.deps import get_neo4j
    from app.services.content_lifecycle import conversation_transcript_artifact_ids
    from app.sync.user_state import read_conversation, read_conversations

    conversation = read_conversation(config.SYNC_DIR, cid) if config.SYNC_DIR else {}
    transcripts = [a for a in conversation_transcript_artifact_ids(cid) if _live("artifact", a)]
    with get_neo4j().session() as session:
        derived = [d for d in _derived(session, cid) if d.get("id") and _live("artifact", d["id"])]
        for d in derived:
            d["others"] = [o for o in d.get("others") or [] if o and _sharer(o, forgetting | {cid})]
        verified = [
            v for v in _verified(session, verified_memory_ids_for_conversation(cid)) if _live("memory", v["id"])
        ]
        facts = _fact_count(session, [d["id"] for d in derived if not d["others"]])

    def memory_item(d: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "kind": "artifact", "id": d["id"],
            "label": _label(d.get("summary"), d.get("filename") or d["id"]),
            "shared_with": len(d["others"]),
        }
        if d["others"]:
            item["default"] = "unchecked"
        return item

    cited = {a: f for a, f in _cited(conversation).items() if _live("artifact", a)}
    used_by = dict.fromkeys(cited, 0)
    if cited and config.SYNC_DIR:
        for other in read_conversations(config.SYNC_DIR):
            oid = str(other.get("id") or "")
            if not oid or oid == cid or not _live("conversation", oid):
                continue
            for aid in _cited(other).keys() & cited.keys():
                used_by[aid] += 1

    return {
        "subject": {"kind": "conversation", "id": cid},
        "title": str(conversation.get("title") or ""),
        "groups": [
            {"key": "transcripts", "default": "always",
             "items": [{"kind": "artifact", "id": a, "label": "Chat transcript"} for a in transcripts]},
            {"key": "memories", "default": "checked",
             "items": [memory_item(d) for d in derived if d.get("scope") != "session_summary"]},
            {"key": "summary", "default": "checked",
             "items": [memory_item(d) for d in derived if d.get("scope") == "session_summary"]},
            {"key": "verified_memories", "default": "checked",
             "items": [{"kind": "memory", "id": v["id"], "label": _label(v.get("text"), v["id"])} for v in verified]},
            {"key": "cited_documents", "default": "unchecked",
             "items": [{"kind": "artifact", "id": a, "label": _label(f, a), "used_by": used_by[a]}
                       for a, f in cited.items()]},
        ],
        "derived_facts": facts,
        "out_of_reach": list(OUT_OF_REACH_NOTES),
    }
