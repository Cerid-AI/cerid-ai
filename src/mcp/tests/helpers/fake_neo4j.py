# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""In-memory behavioral double for Neo4j — delete / hide / active-learning lanes ONLY.

This closes the "no Neo4j double exists" gap called out by the CL-12 audit
(``docs/superpowers/specs/2026-07-15-phase01-shared-contract.md`` §0). It lets
the delete / hide / active-learning code paths be driven **synthetically** —
no live stack — so the four divergence probes run RED today and GREEN after
the Phase-1 fixes land.

**It is NOT a Cypher engine.** It matches a small, fixed set of query *shapes*
by substring and mutates/reads an in-memory ``{artifact_id: props}`` dict. It
deliberately covers only the shapes these lanes issue:

* ``delete_artifact`` MATCH (``app/db/neo4j/artifacts.py``) —
  ``RETURN a.chunk_ids AS chunk_ids, a.domain AS domain, a.filename AS filename``
* ``delete_artifact`` DELETE — ``MATCH (a:Artifact {id: $id}) DETACH DELETE a``
* retention inline delete (``app/services/retention.py``) —
  ``MATCH ... WITH ... DETACH DELETE a RETURN chunk_ids_json, domain``
* ``set_archived`` (Phase-1 hide lane) — ``MATCH ... SET a.archived=true, ...``
* active-learning join (``core/agents/query_agent.py``) —
  ``UNWIND $ids ... RETURN id, weight, flag, archived``
* divergence sample (``app/startup/invariants.py``) —
  ``MATCH (a:Artifact) RETURN a.id, a.chunk_count, a.chunk_ids, a.domain, a.archived``

Real Cypher scoping/semantics (a mis-scoped ``MATCH``, a wrong ``WHERE``) are
**out of scope for a fake** and are covered by the live preservation gate. Keep
this small and honest — do not grow it into a query planner.
"""
from __future__ import annotations

import json
from typing import Any


class _FakeResult:
    """Minimal stand-in for a neo4j Result: iterable + ``.single()``.

    Rows are plain dicts, so both ``record["key"]`` and ``record.get("key")``
    work — matching how the driven code reads real ``neo4j.Record`` rows.
    """

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = list(rows)

    def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """Context-manager session; dispatches ``run`` to the parent driver."""

    def __init__(self, driver: "_FakeNeo4jDriver"):
        self._driver = driver

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def close(self) -> None:  # pragma: no cover - parity with the real API
        pass

    def run(self, query: str, **params: Any) -> _FakeResult:
        return self._driver._run(query, params)


class _FakeNeo4jDriver:
    """Behavioral double for a neo4j Driver over the delete/hide/AL lanes.

    Seed nodes with :meth:`add_artifact`, then drive real code that calls
    ``driver.session()``. ``executed`` retains the raw query strings for
    debugging a probe that fails for the wrong reason.
    """

    def __init__(self, nodes: dict[str, dict[str, Any]] | None = None):
        self.nodes: dict[str, dict[str, Any]] = dict(nodes or {})
        self.executed: list[str] = []

    # -- seeding -------------------------------------------------------------

    def add_artifact(
        self,
        artifact_id: str,
        *,
        chunk_ids: list[str],
        domain: str = "coding",
        filename: str = "synthetic.txt",
        chunk_count: int | None = None,
        endorsement_weight: float = 1.0,
        flag_reason: str = "",
        archived: bool = False,
    ) -> "_FakeNeo4jDriver":
        self.nodes[artifact_id] = {
            "id": artifact_id,
            "chunk_ids": list(chunk_ids),
            "domain": domain,
            "filename": filename,
            "chunk_count": len(chunk_ids) if chunk_count is None else chunk_count,
            "endorsement_weight": endorsement_weight,
            "flag_reason": flag_reason,
            "archived": archived,
            "archived_at": None,
        }
        return self

    def session(self, *args: Any, **kwargs: Any) -> _FakeSession:
        return _FakeSession(self)

    # -- dispatch ------------------------------------------------------------

    def _run(self, query: str, params: dict[str, Any]) -> _FakeResult:
        self.executed.append(query)
        q = " ".join(query.split())

        # 1. Active-learning join (query_agent._apply_active_learning_signals).
        #    Checked first: its RETURN also starts "a.id AS id", which would
        #    otherwise be caught by the divergence-sample rule below.
        if "UNWIND $ids" in q:
            ids = params.get("ids") or []
            rows = [
                {
                    "id": n["id"],
                    "weight": n.get("endorsement_weight", 1.0),
                    "flag": n.get("flag_reason", "") or "",
                    "archived": bool(n.get("archived", False)),
                }
                for aid in ids
                if (n := self.nodes.get(aid)) is not None
            ]
            return _FakeResult(rows)

        # 2. Divergence sample (invariants._probe_divergence).
        if "RETURN a.id AS id" in q and "a.chunk_count" in q:
            rows = [
                {
                    "id": n["id"],
                    "chunk_count": n.get("chunk_count"),
                    "chunk_ids": json.dumps(n.get("chunk_ids") or []),
                    "domain": n.get("domain"),
                    "archived": bool(n.get("archived", False)),
                }
                for n in self.nodes.values()
            ]
            return _FakeResult(rows)

        # 3. Soft-delete / quarantine hide (Phase-1 set_archived).
        if "SET" in q and "archived" in q:
            aid = params.get("aid") or params.get("id")
            n = self.nodes.get(aid)
            if n is None:
                return _FakeResult([])
            n["archived"] = True
            n["archived_at"] = params.get("archived_at")
            extra = params.get("extra")
            if isinstance(extra, dict):
                n.update(extra)
            return _FakeResult([{"ok": True}])

        # 4. DETACH DELETE — retention's combined MATCH+WITH+DELETE+RETURN, or
        #    delete_artifact's standalone DELETE. Capture props BEFORE deleting.
        if "DETACH DELETE" in q:
            aid = params.get("aid") or params.get("id")
            n = self.nodes.get(aid)
            captured: dict[str, Any] | None = None
            if n is not None:
                captured = {
                    "chunk_ids_json": json.dumps(n.get("chunk_ids") or []),
                    "domain": n.get("domain"),
                }
                del self.nodes[aid]
            if "RETURN" in q and "chunk_ids_json" in q:
                return _FakeResult([captured] if captured is not None else [])
            return _FakeResult([])

        # 5. delete_artifact fetch MATCH (chunk_ids/domain/filename).
        if "RETURN a.chunk_ids AS chunk_ids" in q:
            aid = params.get("id") or params.get("aid")
            n = self.nodes.get(aid)
            if n is None:
                return _FakeResult([])
            return _FakeResult([
                {
                    "chunk_ids": json.dumps(n.get("chunk_ids") or []),
                    "domain": n.get("domain"),
                    "filename": n.get("filename"),
                }
            ])

        # Unknown shape — empty result (honest: this double covers only the
        # delete/hide/AL lanes; anything else is not modeled).
        return _FakeResult([])
