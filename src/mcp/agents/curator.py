# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-export bridge — see core/agents/curator.py for implementation.

Wraps ``curate`` and ``estimate_synopsis_run`` to accept a raw
``neo4j_driver`` (legacy callers) or a ``GraphStore`` instance.
Pure scoring functions are re-exported directly.
"""
from __future__ import annotations

import json
from typing import Any

# Re-export pure functions directly
from core.agents.curator import (  # noqa: F401
    _generate_synopsis,
    _is_truncated_summary,
    _node_to_dict,
    _store_quality_scores,
    compute_quality_score,
    score_completeness,
    score_freshness,
    score_keywords,
    score_summary,
)
from core.agents.curator import curate as _core_curate
from core.agents.curator import estimate_synopsis_run as _core_estimate
from core.contracts.stores import ArtifactNode, GraphStore


# ---------------------------------------------------------------------------
# Minimal Neo4j driver → GraphStore adapter (bridge-only, for legacy callers)
# ---------------------------------------------------------------------------

class _Neo4jDriverAdapter(GraphStore):
    """Thin adapter wrapping a raw Neo4j driver as a GraphStore.

    Only implements the methods used by the curator agent.  A full
    ``Neo4jGraphStore`` will be created in the app layer later.
    """

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None:
        raise NotImplementedError("Use the full Neo4jGraphStore for this method")

    async def get_related(
        self, artifact_ids: list[str], *, depth: int = 1, limit: int = 20,
    ) -> list[ArtifactNode]:
        raise NotImplementedError("Use the full Neo4jGraphStore for this method")

    async def list_artifacts(
        self, *, domain: str | None = None, offset: int = 0, limit: int = 100,
    ) -> list[ArtifactNode]:
        from db.neo4j.artifacts import list_artifacts as _db_list
        rows = _db_list(self._driver, domain=domain, limit=limit)
        return [
            ArtifactNode(
                id=r["id"],
                filename=r.get("filename", ""),
                domain=r.get("domain", ""),
                sub_category=r.get("sub_category", ""),
                tags=json.loads(r.get("tags", "[]")) if isinstance(r.get("tags"), str) else r.get("tags", []),
                summary=r.get("summary", ""),
                quality_score=float(r.get("quality_score", 0.0)),
            )
            for r in rows
        ]

    async def update_artifact(
        self, artifact_id: str, updates: dict[str, Any],
    ) -> None:
        if "summary" in updates:
            from db.neo4j.artifacts import update_artifact_summary as _db_update
            _db_update(self._driver, artifact_id, updates["summary"])
        if "quality_score" in updates:
            from core.utils.time import utcnow_iso
            scored_at = updates.get("quality_scored_at", utcnow_iso())
            with self._driver.session() as session:
                session.run(
                    """
                    MATCH (a:Artifact {id: $aid})
                    SET a.quality_score = $score,
                        a.quality_scored_at = $scored_at
                    """,
                    aid=artifact_id,
                    score=updates["quality_score"],
                    scored_at=scored_at,
                )

    async def list_domains(self) -> list[str]:
        raise NotImplementedError("Use the full Neo4jGraphStore for this method")


def _wrap_driver_if_needed(driver_or_store: Any) -> GraphStore:
    """Return *driver_or_store* as-is if it's already a GraphStore,
    otherwise wrap the raw Neo4j driver in a lightweight adapter."""
    if isinstance(driver_or_store, GraphStore):
        return driver_or_store
    return _Neo4jDriverAdapter(driver_or_store)


# ---------------------------------------------------------------------------
# Backward-compatible public API
# ---------------------------------------------------------------------------

async def curate(
    neo4j_driver: Any = None,
    *,
    graph_store: Any = None,
    mode: str = "audit",
    domains: list[str] | None = None,
    max_artifacts: int = 200,
    chroma_client: Any = None,
    generate_synopses: bool = False,
    synopsis_model: str | None = None,
    force_synopses: bool = False,
) -> dict[str, Any]:
    """Backward-compatible wrapper — accepts ``neo4j_driver`` (legacy)
    or ``graph_store`` (new contract)."""
    store = graph_store or _wrap_driver_if_needed(neo4j_driver)
    return await _core_curate(
        graph_store=store,
        mode=mode,
        domains=domains,
        max_artifacts=max_artifacts,
        chroma_client=chroma_client,
        generate_synopses=generate_synopses,
        synopsis_model=synopsis_model,
        force_synopses=force_synopses,
    )


async def estimate_synopsis_run(
    neo4j_driver: Any = None,
    chroma_client: Any = None,
    model: str = "",
    domains: list[str] | None = None,
    max_artifacts: int = 200,
    *,
    graph_store: Any = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper — accepts ``neo4j_driver`` (legacy)
    or ``graph_store`` (new contract)."""
    store = graph_store or _wrap_driver_if_needed(neo4j_driver)
    return await _core_estimate(
        graph_store=store,
        chroma_client=chroma_client,
        model=model,
        domains=domains,
        max_artifacts=max_artifacts,
    )
