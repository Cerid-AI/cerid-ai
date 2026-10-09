# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Re-key positional chunk ids (``{aid}_chunk_3``) to content-addressed ones.

Rows keep their embeddings, documents and metadata; only the id changes, in
Chroma, the HyPE companion collections, the BM25 and SPLADE corpora, and the
Neo4j properties that store chunk ids. Nothing is re-embedded.

Each artifact's old-to-new map is appended to ``map.jsonl`` before any store
is touched and marked applied afterwards, so a run that dies part-way is
finished by the next one from the recorded map. Every step is idempotent under
a fixed map. The migration also runs after each sync import: a machine that has
not migrated yet still exports positional rows, and re-keying them here makes
them land on the rows this machine already holds instead of beside them. A
row whose new id names a purged chunk is deleted rather than re-keyed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.forget import registry as forget_registry
from core.retrieval import bm25, sparse_index
from core.retrieval.chunk_ids import ChunkIdAssigner, positional_artifact_id
from core.retrieval.hype_index import build_hype_doc_id, hype_collection_name
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.chunk_id_migration")

_PAGE = 5000
_HYPE_SUFFIX = "_hype"
_HYPE_ID = re.compile(r"_hype_(\d+)\Z")
_RUN_LOCK = threading.Lock()


def _map_path() -> Path:
    return Path(os.getenv("DATA_DIR", "data")) / "chunk_id_migration" / "map.jsonl"


def _record(row: dict[str, Any]) -> None:
    path = _map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _unapplied() -> list[dict[str, Any]]:
    path = _map_path()
    if not path.exists():
        return []
    recorded: dict[tuple[str, str], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            key = (str(row["collection"]), str(row["artifact_id"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if row.get("applied"):
            recorded.pop(key, None)
        elif isinstance(row.get("map"), dict):
            recorded[key] = row
    return list(recorded.values())


def _positional_by_artifact(collection: Any) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    offset = 0
    while True:
        got = collection.get(include=[], limit=_PAGE, offset=offset)
        ids = got.get("ids") or []
        for cid in ids:
            aid = positional_artifact_id(cid)
            if aid:
                groups.setdefault(aid, []).append(cid)
        if len(ids) < _PAGE:
            return groups
        offset += _PAGE


_SUFFIX_NUMBERS = re.compile(r"_(?:chunk_(\d+)|parent_(\d+)|child_(\d+)_(\d+))\Z")


def _order_key(cid: str, meta: dict[str, Any]) -> tuple[int, int, int]:
    """Stored order: ``chunk_index``, then the positional numbers for rows
    written before the index was stamped (a parent sorts before its children)."""
    match = _SUFFIX_NUMBERS.search(cid)
    groups = match.groups() if match else (None, None, None, None)
    chunk, parent, child_parent, child = groups
    major = int(chunk or parent or child_parent or 0)
    minor = int(child) if child is not None else -1
    return (int(meta.get("chunk_index") or 0), major, minor)


def plan_artifact(collection: Any, artifact_id: str, old_ids: list[str]) -> dict[str, str]:
    """The old-to-new id map for one artifact's positional rows.

    Occurrences are counted among these rows only, in their stored order: a
    row already in the new form came from another ingest and is not part of
    this sequence.
    """
    got = collection.get(ids=old_ids, include=["documents", "metadatas"])
    rows = [
        (cid, doc or "", meta or {})
        for cid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or [])
    ]
    rows.sort(key=lambda r: _order_key(r[0], r[2]))
    ids = ChunkIdAssigner(artifact_id)
    return {cid: ids.assign(str(meta.get("chunk_level") or "child"), doc) for cid, doc, meta in rows}


def _purged(chunk_id: str) -> bool:
    return forget_registry.get_registry().state_of("chunk", chunk_id) == "purged"


def _rekey_chroma(collection: Any, mapping: dict[str, str], dropped: set[str]) -> tuple[int, str]:
    got = collection.get(ids=list(mapping), include=["documents", "metadatas", "embeddings"])
    old_ids = list(got.get("ids") or [])
    if not old_ids:
        return 0, ""
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    embeddings = got.get("embeddings")
    if embeddings is None:
        embeddings = []
    up_ids, up_docs, up_metas, up_embs = [], [], [], []
    domain = ""
    for i, old in enumerate(old_ids):
        meta = dict(metas[i] or {})
        domain = domain or str(meta.get("domain") or "")
        if mapping[old] in dropped:
            continue
        parent = meta.get("parent_chunk_id")
        if parent and parent in mapping:
            meta["parent_chunk_id"] = mapping[parent]
        up_ids.append(mapping[old])
        up_docs.append(docs[i])
        up_metas.append(meta)
        up_embs.append(embeddings[i])
    if up_ids:
        collection.upsert(ids=up_ids, documents=up_docs, metadatas=up_metas, embeddings=up_embs)
    collection.delete(ids=old_ids)
    return len(old_ids), domain


def _rekey_hype(chroma: Any, base: str, artifact_id: str, mapping: dict[str, str], dropped: set[str]) -> int:
    try:
        hype = chroma.get_collection(name=hype_collection_name(base))
    except Exception as exc:  # noqa: BLE001 — core does not import chromadb's error types
        if type(exc).__name__ != "NotFoundError":
            log_swallowed_error("app.services.chunk_id_migration.hype_collection", exc)
        return 0
    got = hype.get(where={"source_artifact_id": artifact_id}, include=["documents", "metadatas", "embeddings"])
    ids = list(got.get("ids") or [])
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    embeddings = got.get("embeddings")
    if embeddings is None:
        embeddings = []
    old, up_ids, up_docs, up_metas, up_embs = [], [], [], [], []
    for i, hid in enumerate(ids):
        meta = dict(metas[i] or {})
        src = str(meta.get("source_chunk_id") or "")
        match = _HYPE_ID.search(hid)
        if src not in mapping or not match:
            continue
        old.append(hid)
        if mapping[src] in dropped:
            continue
        meta["source_chunk_id"] = mapping[src]
        up_ids.append(build_hype_doc_id(mapping[src], int(match.group(1))))
        up_docs.append(docs[i])
        up_metas.append(meta)
        up_embs.append(embeddings[i])
    if up_ids:
        hype.upsert(ids=up_ids, documents=up_docs, metadatas=up_metas, embeddings=up_embs)
    if old:
        hype.delete(ids=old)
    return len(old)


def _rekey_lexical(domain: str, mapping: dict[str, str], dropped: set[str]) -> int:
    if not domain:
        return 0
    gone = [old for old, new in mapping.items() if new in dropped]
    keep = {old: new for old, new in mapping.items() if new not in dropped}
    n = 0
    for module in (bm25, sparse_index):
        if gone:
            module.remove_chunks(domain, gone)
        n += module.rekey_chunks(domain, keep)
    return n


def _remap_json_list(raw: Any, mapping: dict[str, str], dropped: set[str]) -> list[str] | None:
    try:
        ids = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    except (json.JSONDecodeError, TypeError):
        return None
    if not any(i in mapping for i in ids):
        return None
    out: list[str] = []
    for i in ids:
        new = mapping.get(i, i)
        if new in dropped or new in out:
            continue
        out.append(new)
    return out


def _rekey_graph(neo4j: Any, artifact_id: str, mapping: dict[str, str], dropped: set[str]) -> int:
    live = {old: new for old, new in mapping.items() if new not in dropped}
    changed = 0
    with neo4j.session() as session:
        rec = session.run("MATCH (a:Artifact {id: $aid}) RETURN a.chunk_ids AS ids", aid=artifact_id).single()
        if rec is not None:
            ids = _remap_json_list(rec["ids"], mapping, dropped)
            if ids is not None:
                # chunk_count moves only when a purged chunk left the list.
                session.run(
                    "MATCH (a:Artifact {id: $aid}) SET a.chunk_ids = $ids"
                    + (", a.chunk_count = $n" if dropped else ""),
                    aid=artifact_id, ids=json.dumps(ids), n=len(ids),
                )
                changed += 1
        for row in list(session.run(
            "MATCH (:Artifact {id: $aid})-[m:MENTIONS]->() RETURN elementId(m) AS rid, m.chunk_ids AS ids",
            aid=artifact_id,
        )):
            ids = _remap_json_list(row["ids"], mapping, dropped)
            if ids is not None:
                session.run(
                    "MATCH ()-[m:MENTIONS]->() WHERE elementId(m) = $rid SET m.chunk_ids = $ids",
                    rid=row["rid"], ids=json.dumps(ids),
                )
                changed += 1
        rec = session.run(
            "MATCH (:Artifact {id: $aid})-[r:WIKILINKS_TO|EMBEDS]->() WHERE r.source_chunk_id IN $olds "
            "SET r.source_chunk_id = $map[r.source_chunk_id] RETURN count(r) AS n",
            aid=artifact_id, olds=list(live), map=live,
        ).single()
        changed += int(rec["n"]) if rec else 0
        rec = session.run(
            "MATCH (c:Correction {artifact_id: $aid}) WHERE c.applies_to_chunk_id IN $olds "
            "SET c.applies_to_chunk_id = $map[c.applies_to_chunk_id] RETURN count(c) AS n",
            aid=artifact_id, olds=list(live), map=live,
        ).single()
        changed += int(rec["n"]) if rec else 0
    return changed


def _apply(chroma: Any, neo4j: Any, row: dict[str, Any], counts: dict[str, int]) -> None:
    collection = chroma.get_collection(name=row["collection"])
    mapping: dict[str, str] = row["map"]
    dropped = {new for new in mapping.values() if _purged(new)}
    moved, domain = _rekey_chroma(collection, mapping, dropped)
    counts["rows"] += moved
    counts["dropped_purged"] += len(dropped) if moved else 0
    counts["hype_rows"] += _rekey_hype(chroma, row["collection"], row["artifact_id"], mapping, dropped)
    counts["lexical_rows"] += _rekey_lexical(domain or str(row.get("domain") or ""), mapping, dropped)
    counts["graph_properties"] += _rekey_graph(neo4j, row["artifact_id"], mapping, dropped)
    _record({"collection": row["collection"], "artifact_id": row["artifact_id"], "applied": True})


def _kb_collections(chroma: Any) -> list[str]:
    names = [getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else c) for c in chroma.list_collections()]
    return sorted(str(n) for n in names if n and not str(n).endswith(_HYPE_SUFFIX))


def _domain_of(collection: Any, old_ids: list[str]) -> str:
    got = collection.get(ids=old_ids[:1], include=["metadatas"])
    metas = got.get("metadatas") or []
    return str((metas[0] or {}).get("domain") or "") if metas else ""


def migrate_chunk_ids(*, dry_run: bool = False, chroma: Any | None = None, neo4j: Any | None = None) -> dict[str, int]:
    """Re-key every positional chunk id. Safe to run repeatedly; a run with
    nothing left to move only reads the collections' ids."""
    if chroma is None or neo4j is None:
        from app.deps import get_chroma, get_neo4j
        chroma = chroma or get_chroma()
        neo4j = neo4j or get_neo4j()
    counts = {"artifacts": 0, "rows": 0, "dropped_purged": 0, "hype_rows": 0, "lexical_rows": 0,
              "graph_properties": 0, "resumed": 0, "failed": 0}
    if not _RUN_LOCK.acquire(blocking=False):
        return counts
    try:
        if not dry_run:
            for row in _unapplied():
                try:
                    _apply(chroma, neo4j, row, counts)
                    counts["resumed"] += 1
                except Exception as exc:  # noqa: BLE001 — left recorded; the next run resumes it
                    log_swallowed_error("app.services.chunk_id_migration.resume", exc)
                    counts["failed"] += 1
        for name in _kb_collections(chroma):
            collection = chroma.get_collection(name=name)
            for artifact_id, old_ids in _positional_by_artifact(collection).items():
                counts["artifacts"] += 1
                if dry_run:
                    counts["rows"] += len(old_ids)
                    continue
                try:
                    row = {
                        "collection": name,
                        "artifact_id": artifact_id,
                        "domain": _domain_of(collection, old_ids),
                        "map": plan_artifact(collection, artifact_id, old_ids),
                    }
                    _record(row)
                    _apply(chroma, neo4j, row, counts)
                except Exception as exc:  # noqa: BLE001 — one artifact must not stop the rest
                    log_swallowed_error("app.services.chunk_id_migration.artifact", exc,
                                        context={"collection": name})
                    counts["failed"] += 1
        if counts["rows"] and not dry_run:
            from app.services.content_lifecycle import invalidate_caches
            invalidate_caches(trigger="chunk_id_migration")
    finally:
        _RUN_LOCK.release()
    logger.info("chunk id migration%s: %s", " (dry run)" if dry_run else "", counts)
    return counts
