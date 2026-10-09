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


def _chunk_rows(ids: list[str]) -> dict[str, tuple[str, dict[str, Any], Any, str, dict[str, Any]]]:
    """Each chunk's (artifact id, artifact node, collection, document, metadata),
    read live. A chunk whose document or row is gone is left out."""
    from app.deps import get_chroma, get_neo4j
    from core.retrieval.chunk_ids import chunk_artifact_id

    by_artifact: dict[str, list[str]] = {}
    for cid in ids:
        aid = chunk_artifact_id(cid)
        if aid:
            by_artifact.setdefault(aid, []).append(cid)
    if not by_artifact:
        return {}
    with get_neo4j().session() as session:
        nodes = {
            r["id"]: dict(r) for r in session.run(
                "UNWIND $ids AS i MATCH (a:Artifact {id: i}) "
                "RETURN a.id AS id, a.filename AS filename, a.domain AS domain",
                ids=sorted(by_artifact),
            )
        }
    chroma = get_chroma()
    out: dict[str, tuple[str, dict[str, Any], Any, str, dict[str, Any]]] = {}
    for aid, cids in by_artifact.items():
        node = nodes.get(aid)
        if not node or not node.get("domain"):
            continue
        collection = chroma.get_or_create_collection(name=config.collection_name(node["domain"]))
        got = collection.get(ids=cids, include=["documents", "metadatas"])
        for cid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
            out[cid] = (aid, node, collection, doc or "", meta or {})
    return out


def passage_ids(ids: list[str]) -> list[str]:
    """The passages to forget for these chunk ids: a child chunk stands for its
    parent. The parent's text holds the child's and is what retrieval serves for
    every sibling, so forgetting the child alone would leave its words in
    answers. Order is kept and duplicates dropped; an id not found is kept."""
    rows = _chunk_rows(ids)
    out: list[str] = []
    for cid in ids:
        meta = rows[cid][4] if cid in rows else {}
        parent = str(meta.get("parent_chunk_id") or "")
        unit = parent if parent and meta.get("chunk_level") != "parent" else cid
        if unit not in out:
            out.append(unit)
    return out


def chunk_details(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Live details of passages, by chunk id: the document they belong to and
    an excerpt. A passage whose document or row is gone is left out."""
    from core.retrieval.chunk_ids import chunk_body

    out: dict[str, dict[str, Any]] = {}
    for cid, (aid, node, collection, doc, meta) in _chunk_rows(ids).items():
        children = 0
        if meta.get("chunk_level") == "parent":
            children = len(collection.get(where={"parent_chunk_id": cid}, include=[]).get("ids") or [])
        out[cid] = {
            "artifact_id": aid,
            "filename": str(node.get("filename") or aid),
            "domain": str(node["domain"]),
            "excerpt": _label(chunk_body(doc), cid),
            "children": children,
        }
    return out


def _citations_by_artifact(aids: set[str]) -> dict[str, int]:
    """How many live conversations cited each artifact."""
    counts = dict.fromkeys(aids, 0)
    if not aids or not config.SYNC_DIR:
        return counts
    from app.sync.user_state import read_conversations
    for conversation in read_conversations(config.SYNC_DIR):
        cid = str(conversation.get("id") or "")
        if not cid or not _live("conversation", cid):
            continue
        for aid in _cited(conversation).keys() & aids:
            counts[aid] += 1
    return counts


def preview_items(subjects: list[Any]) -> dict[str, Any]:
    """What forgetting documents, passages and memories picked from search
    would remove. A passage of a document that is itself selected folds into
    the document."""
    from app.deps import get_neo4j

    artifacts = sorted({s.id for s in subjects if s.kind == "artifact" and _live("artifact", s.id)})
    memories = sorted({s.id for s in subjects if s.kind == "memory" and _live("memory", s.id)})
    requested = [s.id for s in subjects if s.kind == "chunk"]
    chunk_ids = [c for c in passage_ids(requested) if _live("chunk", c)]
    passages = chunk_details(chunk_ids)
    folded = [cid for cid, d in passages.items() if d["artifact_id"] in artifacts]
    for cid in folded:
        passages.pop(cid)

    with get_neo4j().session() as session:
        docs = [dict(r) for r in session.run(
            "UNWIND $ids AS i MATCH (a:Artifact {id: i}) "
            "RETURN a.id AS id, a.filename AS filename, a.summary AS summary, a.domain AS domain, "
            "a.chunk_ids AS chunk_ids, coalesce(a.chunk_count, 0) AS chunk_count",
            ids=artifacts,
        )]
        # A document with every listed passage selected, but not the document itself.
        whole: dict[str, list[str]] = {}
        for cid, d in passages.items():
            whole.setdefault(d["artifact_id"], []).append(cid)
        partial_nodes = {
            r["id"]: dict(r) for r in session.run(
                "UNWIND $ids AS i MATCH (a:Artifact {id: i}) RETURN a.id AS id, a.filename AS filename, "
                "a.chunk_ids AS chunk_ids",
                ids=sorted(whole),
            )
        }
        verified = _verified(session, memories)
        facts = _fact_count(session, [d["id"] for d in docs])

    used_by = _citations_by_artifact({d["id"] for d in docs})
    notes: list[str] = []
    for aid, cids in whole.items():
        node = partial_nodes.get(aid) or {}
        listed = _json_list(node.get("chunk_ids"))
        if listed and set(listed) <= set(cids):
            notes.append(
                f"These are all {len(listed)} passages of {node.get('filename') or aid}; "
                "forgetting the document instead also removes the facts drawn from it."
            )
    return {
        "subject": None,
        "title": "",
        "groups": [
            {"key": "documents", "default": "checked", "items": [
                {"kind": "artifact", "id": d["id"], "label": _label(d.get("filename"), d["id"]),
                 "domain": str(d.get("domain") or ""), "passages": int(d.get("chunk_count") or 0),
                 "used_by": used_by.get(d["id"], 0)}
                for d in docs if not _is_memory_artifact(d)
            ]},
            {"key": "passages", "default": "checked", "items": [
                {"kind": "chunk", "id": cid, "label": d["excerpt"], "document": d["filename"],
                 "domain": d["domain"], "children": d["children"]}
                for cid, d in passages.items()
            ]},
            {"key": "memories", "default": "checked", "items": [
                {"kind": "artifact", "id": d["id"], "label": _label(d.get("summary"), d.get("filename") or d["id"])}
                for d in docs if _is_memory_artifact(d)
            ] + [
                {"kind": "memory", "id": v["id"], "label": _label(v.get("text"), v["id"])} for v in verified
            ]},
        ],
        "derived_facts": facts,
        "notes": notes,
        "out_of_reach": list(OUT_OF_REACH_NOTES),
    }


def _is_memory_artifact(doc: dict[str, Any]) -> bool:
    return str(doc.get("filename") or "").startswith("memory_")


def _json_list(raw: Any) -> list[str]:
    import json
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []
