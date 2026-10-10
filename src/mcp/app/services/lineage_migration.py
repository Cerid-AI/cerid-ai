# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Give the memories and facts written before phase 5 their lineages (spec §7).

Memories: the old writers left a forest of ``superseded_by`` pointers, each
node pointing at the one that replaced it (the last writer won when a memory
was replaced twice). Each tree becomes one lineage: ``lineage_id`` is its
earliest version, versions are numbered by ingest time, the root is current,
and every other node gets ``valid_to`` (the world time its Chroma rows already
recorded, else its old ``valid_until``), which retires ``valid_until``. A node
whose successor no longer exists is current again. Every row of every version
is stamped to match.

Facts: a node used to collect every memory that asserted it. Each becomes one
version per source memory, open while that memory is current; a STATE fact
from a superseded memory is closed. A fact no memory points at any more is
what deletes before the forget engine left behind (every source gone, spec §4's
derived-facts sweep), and is removed.

Everything is recomputed from the graph, so a second run changes nothing and a
run after a sync import finishes what the import brought in.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from app.db.neo4j.facts import FACT_VALUE_MAX
from app.db.neo4j.migrations import m0007_lineage
from core.lineage.writer import collection, settle_lineage, stamp_rows, state_predicates
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.lineage_migration")

_RUN_LOCK = threading.Lock()
_BATCH = 200

_PENDING = """
MATCH (a) WHERE (a:Artifact OR a:Memory)
  AND (a.valid_until IS NOT NULL
       OR (a.superseded_by IS NOT NULL AND (a.lineage_id IS NULL OR a.valid_to IS NULL)))
RETURN count(a) AS n
"""

_CHAINED = """
MATCH (a) WHERE (a:Artifact OR a:Memory)
  AND (a.superseded_by IS NOT NULL OR a.lineage_id IS NOT NULL
       OR EXISTS { MATCH (o:Artifact) WHERE o.superseded_by = a.id }
       OR EXISTS { MATCH (o:Memory) WHERE o.superseded_by = a.id })
RETURN a.id AS id, a.superseded_by AS sup, a.valid_until AS until, a.valid_to AS valid_to,
       a.lineage_id AS lineage, coalesce(a.ingested_at, a.created_at, '') AS t
"""

_WRITE_VERSIONS = """
UNWIND $rows AS row
MATCH (a) WHERE a.id = row.id AND (a:Artifact OR a:Memory)
SET a.lineage_id = row.lineage_id, a.version = row.version, a.valid_to = row.valid_to,
    a.superseded_valid_to = row.valid_to,
    a.invalid_at = CASE WHEN row.valid_to IS NULL THEN null ELSE coalesce(a.invalid_at, a.valid_until, $now) END
REMOVE a.valid_until
"""

_REOPEN_ORPHANS = """
UNWIND $ids AS i
MATCH (a) WHERE a.id = i AND (a:Artifact OR a:Memory)
SET a.superseded_by = null, a.valid_to = null, a.invalid_at = null
REMOVE a.valid_until
"""

_JOIN_LINEAGE = """
UNWIND $rows AS row
MATCH (a) WHERE a.id = row.id AND (a:Artifact OR a:Memory)
SET a.lineage_id = row.lineage_id
REMOVE a.valid_until
"""

_FACTS_PENDING = (
    "MATCH (f:Fact) WHERE f.source_artifact_id IS NULL AND f.uid > $after "
    "RETURN f.uid AS uid ORDER BY f.uid LIMIT $limit"
)

_FACT_SOURCES = """
UNWIND $uids AS u
MATCH (f:Fact {uid: u})
OPTIONAL MATCH (a:Artifact)-[:FACT]->(f)
OPTIONAL MATCH (f)-[:FACT_OBJECT]->(o:Entity)
RETURN properties(f) AS props,
       collect(DISTINCT a {.id, .superseded_by, .valid_to, .lineage_id, .summary}) AS sources,
       collect(DISTINCT o.canonical_id) AS objects
"""

_WRITE_FACT_VERSIONS = """
UNWIND $rows AS row
MERGE (v:Fact {uid: row.uid})
  ON CREATE SET v += row.props
MERGE (s:Entity {canonical_id: row.props.subject_id})
MERGE (s)-[:HAS_FACT]->(v)
WITH v, row
MATCH (a:Artifact {id: row.props.source_artifact_id})
MERGE (a)-[:FACT]->(v)
WITH v, row
UNWIND row.objects AS oid
MATCH (o:Entity {canonical_id: oid})
MERGE (v)-[:FACT_OBJECT]->(o)
"""

_DROP_SPLIT = "UNWIND $uids AS u MATCH (f:Fact {uid: u}) WHERE f.source_artifact_id IS NULL DETACH DELETE f"
_DROP_SOURCELESS = (
    "UNWIND $uids AS u MATCH (f:Fact {uid: u}) "
    "WHERE f.source_artifact_id IS NULL AND NOT ()-[:FACT]->(f) DETACH DELETE f"
)


def migrate_lineages(*, neo4j: Any = None, chroma: Any = None, dry_run: bool = False) -> dict[str, int]:
    from app.deps import get_chroma, get_neo4j

    driver = neo4j or get_neo4j()
    chroma = chroma or get_chroma()
    with _RUN_LOCK:
        if not dry_run:
            m0007_lineage.run(driver)
        counts = _memory_lineages(driver, chroma, dry_run)
        counts.update(_fact_versions(driver, dry_run))
        counts.update(_closed_flags(driver, chroma, dry_run))
    if any(counts.values()):
        logger.info("lineage migration%s: %s", " (dry run)" if dry_run else "", counts)
    return counts


def _find(parent: dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _memory_lineages(driver: Any, chroma: Any, dry_run: bool) -> dict[str, int]:
    with driver.session() as session:
        pending = session.run(_PENDING).single()
        if not pending or not pending["n"]:
            return {"lineages": 0, "versions": 0, "reopened": 0, "settled": 0}
        nodes = {r["id"]: dict(r) for r in session.run(_CHAINED)}

    parent = {i: i for i in nodes}
    for i, n in nodes.items():
        if n["sup"] and n["sup"] in nodes:
            parent[_find(parent, i)] = _find(parent, n["sup"])
    components: dict[str, list[str]] = {}
    for i in nodes:
        components.setdefault(_find(parent, i), []).append(i)

    # A tree the lineage writer already manages keeps its lineage: its legacy
    # members join it and the writer settles it. Only trees no version of which
    # has a lineage yet are built here.
    joins: list[dict[str, Any]] = []
    to_settle: set[str] = set()
    fresh: list[list[str]] = []
    for root, ids in components.items():
        lineage = nodes[root]["lineage"] or next((nodes[i]["lineage"] for i in ids if nodes[i]["lineage"]), None)
        if lineage:
            joins += [{"id": i, "lineage_id": lineage} for i in ids if nodes[i]["lineage"] != lineage]
            if len(ids) > 1 or nodes[root]["sup"]:
                to_settle.add(lineage)
        elif len(ids) > 1:
            fresh.append(sorted(ids, key=lambda i: (nodes[i]["t"], i)))
    orphans = sorted(i for i, n in nodes.items() if n["sup"] and n["sup"] not in nodes and not n["lineage"])

    coll = collection(chroma)
    row_valid_to = _row_valid_to(coll, [i for ids in fresh for i in ids if nodes[i]["sup"]])
    now = utcnow_iso()
    rows: list[dict[str, Any]] = []
    for ids in fresh:
        for version, i in enumerate(ids, start=1):
            node = nodes[i]
            closed = bool(node["sup"]) and i not in orphans
            rows.append({
                "id": i, "lineage_id": ids[0], "version": version,
                "valid_to": (row_valid_to.get(i) or node["valid_to"] or node["until"] or now) if closed else None,
                "superseded_by": node["sup"] if closed else None,
            })
    counts = {"lineages": len(fresh), "versions": len(rows), "reopened": len(orphans), "settled": len(to_settle)}
    if dry_run:
        return counts
    with driver.session() as session:
        for start in range(0, len(orphans), _BATCH):
            session.run(_REOPEN_ORPHANS, ids=orphans[start:start + _BATCH])
        for start in range(0, len(rows), _BATCH):
            session.run(_WRITE_VERSIONS, rows=rows[start:start + _BATCH], now=now)
        for start in range(0, len(joins), _BATCH):
            session.run(_JOIN_LINEAGE, rows=joins[start:start + _BATCH])
    reopened = [{"id": i, "lineage_id": "", "version": 1} for i in orphans]
    for start in range(0, len(rows), _BATCH):
        stamp_rows(coll, rows[start:start + _BATCH])
    for start in range(0, len(reopened), _BATCH):
        stamp_rows(coll, reopened[start:start + _BATCH])
    for lineage in sorted(to_settle):
        settle_lineage(driver, chroma, lineage, forgotten=_forgotten)
    return counts


def _forgotten(version_id: str) -> bool:
    from core.forget import registry as forget_registry
    return forget_registry.is_forgotten("artifact", version_id) or forget_registry.is_forgotten("memory", version_id)


_CLOSED_VERSIONS = """
MATCH (n) WHERE (n:Artifact OR n:Memory) AND n.lineage_id IS NOT NULL
RETURN n.id AS id, n.lineage_id AS lineage_id, n.version AS version, n.valid_to AS valid_to,
       n.superseded_by AS superseded_by
"""


def _flags_marker() -> Path:
    return Path(os.getenv("DATA_DIR", "data")) / "lineage_migration" / "version_closed.done"


def _closed_flags(driver: Any, chroma: Any, dry_run: bool) -> dict[str, int]:
    """Once: stamp ``version_closed`` on every row of every memory version, so
    a current-only query leaves the closed ones out in its ``where`` clause.
    Rows stamped before the flag existed carry only ``valid_to``; the writer
    stamps it from now on."""
    if _flags_marker().exists():
        return {"flagged_versions": 0}
    with driver.session() as session:
        members = [dict(r) for r in session.run(_CLOSED_VERSIONS)]
    if dry_run:
        return {"flagged_versions": len(members)}
    coll = collection(chroma)
    for start in range(0, len(members), _BATCH):
        stamp_rows(coll, members[start:start + _BATCH])
    _flags_marker().parent.mkdir(parents=True, exist_ok=True)
    _flags_marker().write_text(utcnow_iso() + "\n")
    return {"flagged_versions": len(members)}


def _row_valid_to(coll: Any, ids: list[str]) -> dict[str, str]:
    """The ``valid_to`` the old extraction path wrote on each version's rows."""
    out: dict[str, str] = {}
    for start in range(0, len(ids), _BATCH):
        got = coll.get(where={"artifact_id": {"$in": ids[start:start + _BATCH]}}, include=["metadatas"])
        for meta in got.get("metadatas") or []:
            aid, valid_to = str((meta or {}).get("artifact_id") or ""), str((meta or {}).get("valid_to") or "")
            if aid and valid_to:
                out.setdefault(aid, valid_to)
    return out


def _fact_versions(driver: Any, dry_run: bool) -> dict[str, int]:
    state = set(state_predicates())
    now = utcnow_iso()
    split = written = sourceless = 0
    after = ""
    while True:
        with driver.session() as session:
            uids = [r["uid"] for r in session.run(_FACTS_PENDING, after=after, limit=_BATCH)]
            if not uids:
                break
            after = uids[-1]
            found = [dict(r) for r in session.run(_FACT_SOURCES, uids=uids)]
        rows: list[dict[str, Any]] = []
        drop: list[str] = []
        bare: list[str] = []
        for rec in found:
            props = dict(rec["props"] or {})
            uid = str(props.pop("uid", ""))
            sources = [s for s in rec["sources"] or [] if s and s.get("id")]
            if not sources:
                bare.append(uid)
                continue
            drop.append(uid)
            for src in sources:
                rows.append({"uid": f"{uid}|{src['id']}", "objects": list(rec["objects"] or []),
                             "props": _version_props(props, src, state, now)})
        split += len(drop)
        written += len(rows)
        sourceless += len(bare)
        if dry_run:
            continue
        with driver.session() as session:
            session.execute_write(_write_fact_batch, rows, drop, bare)
    return {"facts_split": split, "fact_versions": written, "orphan_facts_removed": sourceless}


def _version_props(props: dict[str, Any], src: dict[str, Any], state: set[str], now: str) -> dict[str, Any]:
    out = dict(props)
    out.update({
        "source_artifact_id": src["id"],
        "lineage_id": src.get("lineage_id") or src["id"],
        "value": str(src.get("summary") or "")[:FACT_VALUE_MAX],
    })
    if props.get("predicate") in state:
        if src.get("superseded_by") or src.get("valid_to"):
            out.update(valid_to=src.get("valid_to") or now, invalid_at=props.get("invalid_at") or now,
                       closed_by=src.get("superseded_by"))
        else:
            out.update(valid_to=None, invalid_at=None)
    return {k: v for k, v in out.items() if v is not None}


def _write_fact_batch(tx: Any, rows: list[dict[str, Any]], drop: list[str], bare: list[str]) -> None:
    if rows:
        tx.run(_WRITE_FACT_VERSIONS, rows=rows)
    if drop:
        tx.run(_DROP_SPLIT, uids=drop)
    if bare:
        tx.run(_DROP_SOURCELESS, uids=bare)
