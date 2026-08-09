# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Dependency-injection wiring for the Phase J/K agents (inbox triage + daily
digest).

``core/`` must never import ``app/`` (the Phase C layer contract). These two
agents need app-side dependencies — the DataSourceRegistry, the Neo4j driver,
and the artifact reader — so they expose setters (``set_inbox_registry`` /
``set_digest_graph``) that app injects at startup.

This module defines that wiring **once** so app startup (``app/main.py``) and
the agent tests use the *identical* seam rather than two divergent copies — the
exact kind of parallel-implementation drift the 2026-06-29 systemic audit flags.
"""
from __future__ import annotations

from typing import Any


def wire_inbox_triage_di() -> None:
    """Inject the concrete DataSourceRegistry into the inbox-triage agent."""
    from app.data_sources import registry
    from core.agents.inbox_triage import set_inbox_registry

    set_inbox_registry(registry)


class _DigestGraphAdapter:
    """Adapts ``app.deps.get_neo4j`` + ``app.db.neo4j.list_artifacts`` to the
    daily-digest DI surface.

    Dependencies are resolved by attribute lookup at call time (not captured at
    construction) so test patches on ``app.deps.get_neo4j`` /
    ``app.db.neo4j.list_artifacts`` take effect.
    """

    def get_driver(self) -> Any:
        from app import deps

        return deps.get_neo4j()

    def list_artifacts(
        self,
        driver: Any,
        *,
        since: str,
        limit: int = 200,
        domain: str | None = None,
    ) -> Any:
        from app.db import neo4j as graph_db

        if domain is not None:
            return graph_db.list_artifacts(driver, since=since, limit=limit, domain=domain)
        return graph_db.list_artifacts(driver, since=since, limit=limit)


def wire_daily_digest_di() -> None:
    """Inject the Neo4j-backed graph accessor into the daily-digest agent."""
    from core.agents.daily_digest import set_digest_graph

    set_digest_graph(_DigestGraphAdapter())


def wire_crag_external_di() -> None:
    """Inject the external-source registry + search-term extractor into the
    canonical CRAG path (``core.agents.crag``).

    Lets ``agent_query_full`` fire external augmentation without ``core/``
    importing ``app.data_sources`` / ``app.agents`` (Phase 1). Unwired → no-op.
    """
    from app.agents.retrieval_orchestrator import _extract_search_terms
    from app.data_sources import registry
    from core.agents.crag import set_external_source_registry, set_search_term_extractor

    set_external_source_registry(registry)
    set_search_term_extractor(_extract_search_terms)
