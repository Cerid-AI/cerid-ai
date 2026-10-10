# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Undo this update: the previous version becomes current again and the newer
one goes to the Trash (spec §7). It is a lineage action, not a forget of the
whole item: history stays, and restoring the newer version from the Trash
brings it back as history, not as the current version.

A memory's versions are separate memories, so the current one is moved to the
Trash and the lineage settles on the one before it. A document's versions are
its passages over time: the passages the newer version brought are closed and
moved to the Trash, and those it replaced are reopened.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import config
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j
from core.forget.registry import UNDO, Subject, forgotten_ids
from core.lineage.current import VERSION_CLOSED
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.lineage_undo")

_UNDOABLE = 2  # a current version and the one it replaced


class NothingToUndo(Exception):
    """The item has no earlier version, or is not the current one."""


def undo_update(kind: str, item_id: str) -> dict[str, Any]:
    from app.services.forget import engine
    from core.lineage.writer import lineage_versions

    if not engine.forget_available():
        raise engine.ForgetUnavailable("sync dir not configured")
    driver = get_neo4j()
    versions = lineage_versions(driver, item_id)
    me = next((v for v in versions if v["id"] == item_id), None)
    if kind == "memory" or (me is not None and len(versions) > 1):
        if me is None or me["valid_to"] or me["superseded_by"]:
            raise NothingToUndo("only the current version can be undone")
        if not any(v["id"] != item_id for v in versions):
            raise NothingToUndo("this memory has no earlier version")
        forget_id = engine.trash([Subject(kind, item_id)], requested_by=UNDO)
        return {"forget_id": forget_id, "kind": kind, "id": item_id}
    return _undo_document(item_id)


def _undo_document(artifact_id: str) -> dict[str, Any]:
    from app.services.forget import engine

    node = graph.get_artifact_versions(get_neo4j(), artifact_id) or {}
    history = _versions(node.get("versions"))
    if len(history) < _UNDOABLE:
        raise NothingToUndo("this document has no earlier version")
    newer, older = history[-1], history[-2]
    started = str(newer.get("valid_from") or "")
    domain = str(node.get("domain") or "")
    collection = get_chroma().get_or_create_collection(name=config.collection_name(domain))
    got = collection.get(where={"artifact_id": artifact_id}, include=["documents", "metadatas"])
    rows = list(zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []))
    brought = [(c, d, m or {}) for c, d, m in rows
               if not (m or {}).get(VERSION_CLOSED) and str((m or {}).get("valid_from") or "") == started]
    replaced = [(c, d, m or {}) for c, d, m in rows
                if (m or {}).get(VERSION_CLOSED) and str((m or {}).get("valid_to") or "") == started]
    forgotten = forgotten_ids("chunk")
    if any(c in forgotten for c, _, _ in replaced):
        raise NothingToUndo("the earlier version was forgotten")
    now = utcnow_iso()
    older_version = int(older.get("version") or 1)
    if replaced:
        collection.update(ids=[c for c, _, _ in replaced], metadatas=[
            {**m, "valid_to": "", VERSION_CLOSED: 0, "version": older_version} for _, _, m in replaced
        ])
    if brought:
        collection.update(ids=[c for c, _, _ in brought], metadatas=[
            {**m, "valid_to": now, VERSION_CLOSED: 1} for _, _, m in brought
        ])
    _reindex(domain, replaced, brought)
    current = [(c, m) for c, _, m in rows if c not in {b[0] for b in brought}
               and (not m.get(VERSION_CLOSED) or c in {r[0] for r in replaced})]
    retrievable = [c for c, m in current if (m or {}).get("chunk_level") != "parent"]
    graph.set_artifact_properties(get_neo4j(), artifact_id, {
        "version": older_version,
        "versions": json.dumps(history[:-1]),
        "content_hash": str(older.get("content_hash") or node.get("content_hash") or ""),
        "chunk_ids": json.dumps(retrievable),
        "chunk_count": len(retrievable),
        "updated_at": now,
    })
    forget_id = ""
    if brought:
        forget_id = engine.trash([Subject("chunk", c) for c, _, _ in brought], requested_by=UNDO)
    _after_content_change(artifact_id, domain)
    logger.info("Undid the last update of %s: %d passages back, %d to the Trash",
                artifact_id[:8], len(replaced), len(brought))
    return {"forget_id": forget_id, "kind": "artifact", "id": artifact_id, "version": older_version,
            "reopened": len(replaced), "trashed": len(brought)}


def _reindex(domain: str, reopened: list[tuple[str, str, dict[str, Any]]],
             closed: list[tuple[str, str, dict[str, Any]]]) -> None:
    """The keyword indexes serve current text only."""
    from core.retrieval import bm25, sparse_index

    back = [(c, d) for c, d, m in reopened if m.get("chunk_level") != "parent"]
    gone = [c for c, _, _ in closed]
    for index in (bm25, sparse_index):
        try:
            if gone:
                index.remove_chunks(domain, gone)
            if back:
                index.index_chunks(domain, [c for c, _ in back], [d for _, d in back])
        except Exception as exc:  # noqa: BLE001 — keyword index repair is best-effort
            log_swallowed_error("app.services.lineage_undo.reindex", exc)


def _after_content_change(artifact_id: str, domain: str) -> None:
    """What any change of a document's text sets off: the pages written from
    it are queued again, its mentions re-extracted and cached answers dropped."""
    from app.services.content_lifecycle import invalidate_caches
    from app.services.derived import mark_derived_stale
    from app.services.ingestion import _enqueue_entity_extraction_if_enabled

    mark_derived_stale([artifact_id])
    try:
        graph.remove_mentions_for_artifact(get_neo4j(), artifact_id)
        _enqueue_entity_extraction_if_enabled(artifact_id=artifact_id)
    except Exception as exc:  # noqa: BLE001 — re-extraction is retried by the sweep
        log_swallowed_error("app.services.lineage_undo.mentions", exc)
    invalidate_caches(trigger=f"lineage.undo:{artifact_id}")


def _versions(raw: Any) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def forget_earlier_versions(artifact_id: str, mode: str, *, user_id: str = "") -> dict[str, Any]:
    """Forget what only a document's earlier versions held: its closed passages.
    Editing a document keeps the old text as history (spec §7); this is how a
    person removes it, through the forget engine like any other forget."""
    from app.services.forget import engine

    if not engine.forget_available():
        raise engine.ForgetUnavailable("sync dir not configured")
    node = graph.get_artifact_versions(get_neo4j(), artifact_id) or {}
    if not node:
        raise NothingToUndo("no such document")
    collection = get_chroma().get_or_create_collection(name=config.collection_name(str(node.get("domain") or "")))
    closed = collection.get(where={"$and": [{"artifact_id": artifact_id}, {VERSION_CLOSED: 1}]}, include=[])
    ids = list(closed.get("ids") or [])
    if not ids:
        raise NothingToUndo("this document has no earlier version")
    subjects = [Subject("chunk", cid) for cid in ids]
    if mode == "permanent":
        receipt = engine.forget_permanently(subjects, requested_by="ui", user_id=user_id)
        done = all(a.get("status") == "done" for a in receipt["adapters"].values())
        return {"forget_id": receipt["forget_id"], "state": "purged" if done else "trashed_pending", "passages": len(ids)}
    forget_id = engine.trash(subjects, requested_by="ui", user_id=user_id)
    return {"forget_id": forget_id, "state": "trashed", "passages": len(ids)}
