# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The one writer of supersession (spec §7).

A memory lineage is a chain of versions: ``:Artifact`` nodes for extracted
memories and summaries, ``:Memory`` nodes for verified ones, each with Chroma
rows in the conversations collection keyed by ``artifact_id``. Every version
carries ``lineage_id``, ``version``, ``valid_to`` and ``superseded_by`` in the
graph and in each of its rows; exactly one version per lineage has an empty
``valid_to``, and that is the current one.

``supersede`` closes a current version in favour of another, merging the two
lineages when the newer one already has history, and renumbers versions by
ingest time. The graph is written first; if the rows cannot be stamped the
graph is put back, so a version is never superseded in one store and current in
the other. ``settle_lineage`` serves the forget engine: after a forget, a restore
or a purge it recomputes which version is current from which versions are
forgotten, so the outcome never depends on the order those happened in.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import config
from core.lineage.current import VERSION_CLOSED
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.lineage")

_LOCK = threading.Lock()
_ON_CHANGE: Callable[[list[str]], Any] | None = None


def set_on_change(callback: Callable[[list[str]], Any] | None) -> None:
    """Tell the app which versions a supersede changed, so what was derived
    from them (wiki pages, session summaries) is refreshed. core cannot
    import app; app wires this at startup."""
    global _ON_CHANGE
    _ON_CHANGE = callback


def _changed(ids: list[str]) -> None:
    if _ON_CHANGE is None:
        return
    try:
        _ON_CHANGE(ids)
    except Exception as exc:  # noqa: BLE001 — a stale summary is refreshed by its sweep
        log_swallowed_error("core.lineage.writer.on_change", exc)
_NODE = "(n:Artifact OR n:Memory)"
_FIELDS = ("lineage_id", "version", "valid_to", "superseded_by")


@dataclass
class SupersedeResult:
    ok: bool
    lineage_id: str = ""
    version: int = 0
    reason: str = ""


_READ_PAIR = f"""
MATCH (old) WHERE old.id = $old AND (old:Artifact OR old:Memory)
MATCH (new) WHERE new.id = $new AND (new:Artifact OR new:Memory)
RETURN old.superseded_by AS old_sup, coalesce(old.lineage_id, old.id) AS old_lin,
       coalesce(new.lineage_id, new.id) AS new_lin, new.superseded_by AS new_sup,
       new.valid_to AS new_valid_to,
       old.valid_to AS old_valid_to, old.invalid_at AS old_invalid_at,
       EXISTS {{ MATCH (new)-[:SUPERSEDES]->(old) }} AS edge,
       EXISTS {{ MATCH (n) WHERE {_NODE} AND n.lineage_id = coalesce(new.lineage_id, new.id)
                 AND n.id <> new.id }} AS new_has_history
"""

_MEMBERS = f"""
MATCH (n) WHERE {_NODE} AND (n.lineage_id IN $lins OR n.id IN $ids)
RETURN n.id AS id, n.lineage_id AS lineage_id, n.version AS version
"""

_LINK = f"""
MATCH (old) WHERE old.id = $old AND (old:Artifact OR old:Memory) AND old.superseded_by IS NULL
MATCH (new) WHERE new.id = $new AND (new:Artifact OR new:Memory) AND new.superseded_by IS NULL
SET old.superseded_by = $new, old.valid_to = $valid_to, old.invalid_at = $now,
    old.superseded_valid_to = $valid_to
MERGE (new)-[:SUPERSEDES]->(old)
WITH old
MATCH (n) WHERE {_NODE} AND (n.lineage_id IN $lins OR n.id IN $ids)
SET n.lineage_id = $lineage
RETURN count(n) AS n
"""

_RENUMBER = f"""
MATCH (n) WHERE {_NODE} AND n.lineage_id = $lineage
WITH n ORDER BY coalesce(n.ingested_at, n.created_at, ''), n.id
WITH collect(n) AS ns
UNWIND range(0, size(ns) - 1) AS i
WITH ns[i] AS n, i
SET n.version = i + 1, n.updated_at = $now
RETURN n.id AS id, n.lineage_id AS lineage_id, n.version AS version,
       n.valid_to AS valid_to, n.superseded_by AS superseded_by
"""

_UNDO_LINK = """
MATCH (old) WHERE old.id = $old AND (old:Artifact OR old:Memory)
SET old.superseded_by = null, old.valid_to = $valid_to, old.invalid_at = $invalid_at,
    old.superseded_valid_to = $valid_to
WITH old
OPTIONAL MATCH (:Artifact|Memory {id: $new})-[r:SUPERSEDES]->(old)
FOREACH (_ IN CASE WHEN $drop_edge AND r IS NOT NULL THEN [1] ELSE [] END | DELETE r)
"""

# When the newer version's facts are already written (a dedup, or a restore
# behind a newer version), the older version's STATE facts close now; otherwise
# they close when the newer version's facts are written (``facts.write_facts``).
_CLOSE_OLD_FACTS = """
MATCH (f:Fact {source_artifact_id: $old})
WHERE f.valid_to IS NULL AND f.predicate IN $state
  AND EXISTS { MATCH (:Fact {source_artifact_id: $new}) }
SET f.valid_to = $valid_to, f.invalid_at = $now, f.closed_by = $new
"""

_REOPEN_OLD_FACTS = """
MATCH (f:Fact {source_artifact_id: $old, closed_by: $new})
SET f.valid_to = null, f.invalid_at = null
REMOVE f.closed_by
"""

_RESTORE_MEMBER = """
MATCH (n) WHERE n.id = $id AND (n:Artifact OR n:Memory)
SET n.lineage_id = $lineage_id, n.version = $version
"""


def state_predicates() -> list[str]:
    """Memory types whose facts describe a current state and close when a newer
    version replaces them; every other type is a dated event that never closes."""
    return sorted(config.settings.MEMORY_POWER_LAW_TYPES)


def collection(chroma_client: Any) -> Any:
    return chroma_client.get_or_create_collection(name=config.collection_name("conversations"))


def _row_meta(row: dict[str, Any]) -> dict[str, Any]:
    """The lineage fields as Chroma stores them: strings, with "" for open, and
    ``version_closed`` so a current-only query can leave closed versions out in
    the ``where`` clause (``$ne`` also keeps rows that never had the key)."""
    valid_to = str(row.get("valid_to") or "")
    superseded_by = str(row.get("superseded_by") or "")
    return {
        "lineage_id": str(row.get("lineage_id") or ""),
        "version": int(row.get("version") or 1),
        "valid_to": valid_to,
        "superseded_by": superseded_by,
        VERSION_CLOSED: 1 if (valid_to or superseded_by) else 0,
    }


def stamp_rows(coll: Any, members: list[dict[str, Any]]) -> list[tuple[list[str], list[dict[str, Any]]]]:
    """Write each member's lineage fields onto every Chroma row of it. Returns
    the previous metadata so a caller can put it back."""
    if not members:
        return []
    by_id = {str(m["id"]): _row_meta(m) for m in members}
    got = coll.get(where={"artifact_id": {"$in": list(by_id)}}, include=["metadatas"])
    ids = list(got.get("ids") or [])
    metas = list(got.get("metadatas") or [])
    if not ids:
        return []
    updated = [{**(meta or {}), **by_id.get(str((meta or {}).get("artifact_id")), {})} for meta in metas]
    coll.update(ids=ids, metadatas=updated)
    return [(ids, [dict(m or {}) for m in metas])]


def supersede(
    driver: Any, chroma_client: Any, old_id: str, new_id: str, *, valid_to: str = "",
) -> SupersedeResult:
    """Make ``new_id`` the current version and close ``old_id``. ``valid_to`` is
    the world time the old version stopped being true (the newer one's
    ``valid_from``); empty means now. Refused when ``old_id`` is already
    superseded by something else or ``new_id`` is not current."""
    if not old_id or not new_id or old_id == new_id:
        return SupersedeResult(False, reason="a version cannot supersede itself")
    if chroma_client is None:
        return SupersedeResult(False, reason="no vector store to stamp")
    now = utcnow_iso()
    valid_to = valid_to or now
    with _LOCK:
        with driver.session() as session:
            pair = session.run(_READ_PAIR, old=old_id, new=new_id).single()
            if pair is None:
                return SupersedeResult(False, reason="version not found")
            if pair["old_sup"] == new_id:
                lin = pair["new_lin"]
                return SupersedeResult(True, lineage_id=lin, reason="already superseded by this version")
            if pair["old_sup"]:
                return SupersedeResult(False, reason=f"{old_id} is already superseded by {pair['old_sup']}")
            if pair["new_sup"] or pair["new_valid_to"]:
                return SupersedeResult(False, reason=f"{new_id} is not the current version")
            lineage = pair["new_lin"] if pair["new_has_history"] else pair["old_lin"]
            lins = sorted({pair["old_lin"], pair["new_lin"]})
            ids = [old_id, new_id]
            before = [dict(r) for r in session.run(_MEMBERS, lins=lins, ids=ids)]
            linked = session.run(
                _LINK, old=old_id, new=new_id, valid_to=valid_to, now=now,
                lins=lins, ids=ids, lineage=lineage,
            ).single()
            if not linked or not linked["n"]:
                return SupersedeResult(False, reason="the versions changed while superseding")
        try:
            with driver.session() as session:
                members = [dict(r) for r in session.run(_RENUMBER, lineage=lineage, now=now)]
                session.run(
                    _CLOSE_OLD_FACTS, old=old_id, new=new_id, valid_to=valid_to, now=now,
                    state=state_predicates(),
                )
            stamp_rows(collection(chroma_client), members)
        except Exception as exc:  # noqa: BLE001 — any store failure puts the graph back
            try:
                _undo(driver, old_id, new_id, pair, before)
            except Exception as undo_exc:  # noqa: BLE001 — reported; the next settle or migration repairs it
                log_swallowed_error("core.lineage.writer.undo", undo_exc)
            logger.warning("supersede %s -> %s not applied: %s", old_id, new_id, exc)
            return SupersedeResult(False, reason=f"not applied: {type(exc).__name__}")
    version = next((int(m["version"]) for m in members if m["id"] == new_id), 0)
    logger.info("lineage %s: %s superseded by %s (version %d)", lineage, old_id, new_id, version)
    _changed([old_id, new_id])
    return SupersedeResult(True, lineage_id=lineage, version=version)


def _undo(driver: Any, old_id: str, new_id: str, pair: Any, before: list[dict[str, Any]]) -> None:
    with driver.session() as session:
        session.run(
            _UNDO_LINK, old=old_id, new=new_id, valid_to=pair["old_valid_to"],
            invalid_at=pair["old_invalid_at"], drop_edge=not pair["edge"],
        )
        for m in before:
            session.run(_RESTORE_MEMBER, id=m["id"], lineage_id=m["lineage_id"], version=m["version"])
        session.run(_REOPEN_OLD_FACTS, old=old_id, new=new_id)


_LINEAGE_OF = """
MATCH (a) WHERE a.id = $aid AND (a:Artifact OR a:Memory)
RETURN a.lineage_id AS lineage_id
"""

_LINEAGE_MEMBERS = f"""
MATCH (n) WHERE {_NODE} AND n.lineage_id = $lineage
RETURN n.id AS id, n.version AS version, n.superseded_by AS superseded_by, n.valid_to AS valid_to,
       n.superseded_valid_to AS closed_at, n.invalid_at AS invalid_at,
       coalesce(n.ingested_at, n.created_at, '') AS t
ORDER BY t, n.id
"""

_SET_CURRENT = """
MATCH (n) WHERE n.id = $id AND (n:Artifact OR n:Memory)
SET n.superseded_by = null, n.valid_to = null, n.invalid_at = null
"""

_SET_HISTORY = """
UNWIND $rows AS row
MATCH (n) WHERE n.id = row.id AND (n:Artifact OR n:Memory)
MATCH (s) WHERE s.id = row.successor AND (s:Artifact OR s:Memory)
SET n.superseded_by = row.successor, n.valid_to = row.valid_to, n.superseded_valid_to = row.valid_to,
    n.invalid_at = coalesce(n.invalid_at, $now)
MERGE (s)-[:SUPERSEDES]->(n)
"""

# Facts follow their version: a forgotten version's facts close while it is
# forgotten and open again when it comes back; the current version's STATE
# facts are open; every other version's STATE facts are closed at the time
# that version stopped being true. EVENT facts close only with a forget.
_FACTS_FORGOTTEN = """
MATCH (f:Fact) WHERE f.source_artifact_id IN $ids AND f.valid_to IS NULL
SET f.valid_to = $now, f.invalid_at = $now, f.closed_by = 'trash:' + f.source_artifact_id
"""

_FACTS_BACK = """
MATCH (f:Fact) WHERE f.source_artifact_id IN $ids AND f.closed_by = 'trash:' + f.source_artifact_id
SET f.valid_to = null, f.invalid_at = null
REMOVE f.closed_by
"""

_FACTS_CURRENT = """
MATCH (f:Fact {source_artifact_id: $id})
WHERE f.closed_by IS NOT NULL AND f.predicate IN $state
SET f.valid_to = null, f.invalid_at = null
REMOVE f.closed_by
"""

_FACTS_HISTORY = """
UNWIND $rows AS row
MATCH (f:Fact {source_artifact_id: row.id})
WHERE f.valid_to IS NULL AND f.predicate IN $state
SET f.valid_to = row.valid_to, f.invalid_at = $now, f.closed_by = row.successor
"""


def lineage_of(driver: Any, aid: str) -> str:
    """``aid``'s lineage, or "" for a version that never had history."""
    with driver.session() as session:
        rec = session.run(_LINEAGE_OF, aid=aid).single()
    return str(rec["lineage_id"] or "") if rec else ""


def settle_lineage(
    driver: Any, chroma_client: Any, lineage_id: str, *, forgotten: Callable[[str], bool],
) -> str:
    """Recompute a lineage after a forget, a restore or a purge (spec §7).

    The newest version that is not forgotten is current, and every other
    version that is not forgotten is superseded by the next one that is not,
    keeping the time it first stopped being true. Forgotten versions are left
    as they are: they are hidden, and come back through the same rule. The
    result depends only on which versions are forgotten, never on the order of
    events, so a retry after a failure repairs the stores. Returns the current
    version, or "" when every version is forgotten.
    """
    if not lineage_id:
        return ""
    now = utcnow_iso()
    with _LOCK:
        with driver.session() as session:
            members = [dict(r) for r in session.run(_LINEAGE_MEMBERS, lineage=lineage_id)]
            visible = [m for m in members if not forgotten(m["id"])]
            hidden = [m["id"] for m in members if forgotten(m["id"])]
            history = [
                {"id": m["id"], "successor": visible[i + 1]["id"],
                 "valid_to": m["closed_at"] or m["valid_to"] or now}
                for i, m in enumerate(visible[:-1])
            ]
            current = visible[-1]["id"] if visible else ""
            if hidden:
                session.run(_FACTS_FORGOTTEN, ids=hidden, now=now)
            if visible:
                session.run(_FACTS_BACK, ids=[m["id"] for m in visible])
            if current:
                session.run(_SET_CURRENT, id=current)
                session.run(_FACTS_CURRENT, id=current, state=state_predicates())
            if history:
                session.run(_SET_HISTORY, rows=history, now=now)
                session.run(_FACTS_HISTORY, rows=history, now=now, state=state_predicates())
            stamped = [dict(r) for r in session.run(_RENUMBER, lineage=lineage_id, now=now)]
        stamp_rows(collection(chroma_client), stamped)
    return current


def lineage_versions(driver: Any, aid: str) -> list[dict[str, Any]]:
    """Every version in ``aid``'s lineage, newest first."""
    with driver.session() as session:
        rows = session.run(
            "MATCH (a) WHERE a.id = $aid AND (a:Artifact OR a:Memory) "
            f"MATCH (n) WHERE {_NODE} AND n.lineage_id = coalesce(a.lineage_id, a.id) "
            "RETURN n.id AS id, CASE WHEN n:Memory THEN 'Memory' ELSE 'Artifact' END AS label, "
            "n.version AS version, "
            "coalesce(n.summary, n.text, '') AS text, n.valid_to AS valid_to, "
            "n.superseded_by AS superseded_by ORDER BY coalesce(n.version, 0) DESC",
            aid=aid,
        )
        return [dict(r) for r in rows]
