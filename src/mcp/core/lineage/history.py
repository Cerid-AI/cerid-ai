# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""History notes: what a returned version used to say (spec §7).

A result that has earlier versions carries ``history``, newest first and at
most ``HISTORY_LIMIT`` entries, each ``{value, valid_from, valid_to}``. Readers
answer from the current value and can mention the earlier one ("this is Y, but
it used to be X"). An earlier version appears only where it could be read
itself: same tenant, not forgotten, not archived or flagged.
"""
from __future__ import annotations

from typing import Any

import config
from core.context.identity import with_tenant_scope
from core.forget.registry import forgotten_ids
from core.retrieval.chunk_ids import chunk_body
from core.utils.swallowed import log_swallowed_error

HISTORY_LIMIT = 3
_VALUE_MAX = 300


_HIDDEN = """
UNWIND $ids AS vid
MATCH (n) WHERE n.id = vid AND (n:Artifact OR n:Memory)
  AND (coalesce(n.archived, false) OR coalesce(n.flag_reason, '') <> ''
       OR (n:Memory AND coalesce(n.status, 'active') <> 'active'))
RETURN n.id AS id
"""


def attach_history(
    rows: list[dict[str, Any]], chroma_client: Any, neo4j_driver: Any, *, limit: int = HISTORY_LIMIT,
) -> None:
    """Set ``history`` on each row whose lineage has earlier versions.

    An earlier version is shown only where its rows would be shown themselves:
    in the caller's tenant, not forgotten (whole or by passage), and not
    archived, flagged or retired in the graph. Without the graph there is no
    way to tell, so no history is shown. One Chroma read per lineage and one
    graph read in all; a failure leaves rows without notes.
    """
    wanted = [r for r in rows if r.get("lineage_id") and int(r.get("version") or 1) > 1]
    if not wanted or chroma_client is None or neo4j_driver is None:
        return
    try:
        forgotten = (forgotten_ids("artifact") | forgotten_ids("memory"), forgotten_ids("chunk"))
    except Exception as exc:  # noqa: BLE001 — without the registry no history is safe to show
        log_swallowed_error("core.lineage.history.registry", exc)
        return
    cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in wanted:
        key = (str(row.get("collection") or config.collection_name("conversations")), str(row["lineage_id"]))
        if key not in cache:
            try:
                cache[key] = _lineage_rows(chroma_client, *key, *forgotten)
            except Exception as exc:  # noqa: BLE001 — a note is an extra, never a failure
                log_swallowed_error("core.lineage.history.read", exc)
                cache[key] = []
    candidates = sorted({r["artifact_id"] for rows_ in cache.values() for r in rows_})
    try:
        with neo4j_driver.session() as session:
            hidden = {r["id"] for r in session.run(_HIDDEN, ids=candidates)} if candidates else set()
    except Exception as exc:  # noqa: BLE001 — unknown visibility shows nothing
        log_swallowed_error("core.lineage.history.hidden", exc)
        return
    for row in wanted:
        key = (str(row.get("collection") or config.collection_name("conversations")), str(row["lineage_id"]))
        earlier = _earlier(row, [r for r in cache[key] if r["artifact_id"] not in hidden])[:limit]
        if earlier:
            row["history"] = [
                {"value": v["value"], "valid_from": v["valid_from"], "valid_to": v["valid_to"]} for v in earlier
            ]


def _earlier(row: dict[str, Any], lineage: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The earlier versions of what ``row`` says, newest first. A memory's are
    the other memories of its lineage (each by its first passage); a document
    passage's are the closed passages that held its place (same index and
    level) in the document's earlier versions."""
    mine = int(row.get("version") or 1)
    aid = row.get("artifact_id")
    first: dict[str, dict[str, Any]] = {}
    same_place: list[dict[str, Any]] = []
    for r in lineage:
        if r["version"] >= mine:
            continue
        if r["artifact_id"] != aid:
            if r["artifact_id"] not in first or r["index"] < first[r["artifact_id"]]["index"]:
                first[r["artifact_id"]] = r
        elif r["closed"] and r["index"] == int(row.get("chunk_index") or 0) \
                and r["level"] == str(row.get("chunk_level") or ""):
            same_place.append(r)
    return sorted([*first.values(), *same_place], key=lambda v: -v["version"])


def _lineage_rows(
    chroma_client: Any, collection: str, lineage_id: str,
    forgotten_versions: frozenset[str], forgotten_chunks: frozenset[str],
) -> list[dict[str, Any]]:
    """Every row of a lineage the caller may read: in its tenant, and neither
    forgotten whole nor by passage."""
    got = chroma_client.get_or_create_collection(name=collection).get(
        where=with_tenant_scope({"lineage_id": lineage_id}), include=["documents", "metadatas"],
    )
    out: list[dict[str, Any]] = []
    for cid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
        m = meta or {}
        aid = str(m.get("artifact_id") or "")
        if not aid or aid in forgotten_versions:
            continue
        if cid in forgotten_chunks or str(m.get("parent_chunk_id") or "") in forgotten_chunks:
            continue
        out.append({
            "artifact_id": aid,
            "index": int(m.get("chunk_index") or 0),
            "level": str(m.get("chunk_level") or ""),
            "closed": bool(m.get("version_closed")) or bool(m.get("valid_to")),
            "version": int(m.get("version") or 1),
            "value": chunk_body(doc or "").strip()[:_VALUE_MAX],
            "valid_from": str(m.get("valid_from") or m.get("ingested_at") or ""),
            "valid_to": str(m.get("valid_to") or ""),
        })
    return out


def history_note(row: dict[str, Any]) -> str:
    """The notes as text for a context block; empty when there are none."""
    entries = row.get("history") or []
    if not entries:
        return ""
    lines = [f"- until {str(e.get('valid_to') or '')[:10] or 'unknown'}: {e.get('value', '')}" for e in entries]
    return "Earlier versions (newest first):\n" + "\n".join(lines)
