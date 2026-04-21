# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 14: startup invariants provide observable health beyond `connected`."""
from __future__ import annotations

from unittest.mock import MagicMock


def _mk_collection(name: str, count: int = 0) -> MagicMock:
    """Build a MagicMock that mimics the chromadb Collection API."""
    c = MagicMock()
    # MagicMock assigns `.name` to itself internally — set via configure_mock
    # so our intended value actually sticks.
    c.configure_mock(name=name)
    c.count = MagicMock(return_value=count)
    return c


def _mk_chroma(collections: list[dict]) -> MagicMock:
    client = MagicMock()
    client.list_collections.return_value = [
        _mk_collection(c["name"], c.get("count", 0)) for c in collections
    ]
    return client


def _mk_neo4j(orphans: int = 0) -> MagicMock:
    neo4j = MagicMock()
    neo4j.session().__enter__().run.return_value.single.return_value = {"orphans": orphans}
    return neo4j


def test_invariants_flag_empty_collections():
    """Collections with zero items are reported so dashboards can surface
    the 10-empty-collection problem from the audit."""
    from app.startup.invariants import run_invariants

    chroma = _mk_chroma([
        {"name": "domain_general", "count": 50},
        {"name": "domain_trading", "count": 0},
        {"name": "domain_finance", "count": 0},
    ])
    redis = MagicMock()
    neo4j = _mk_neo4j(orphans=0)

    snap = run_invariants(chroma, redis, neo4j)
    assert "collections_empty" in snap
    assert "domain_trading" in snap["collections_empty"]
    assert "domain_finance" in snap["collections_empty"]
    assert "domain_general" not in snap["collections_empty"]


def test_invariants_surface_verification_orphans():
    from app.startup.invariants import run_invariants

    chroma = _mk_chroma([{"name": "domain_general", "count": 1}])
    redis = MagicMock()
    neo4j = _mk_neo4j(orphans=5)

    snap = run_invariants(chroma, redis, neo4j)
    assert snap["verification_report_orphans"] == 5


def test_invariants_never_raises():
    """A broken driver must not crash the invariants; snapshot returns
    partial data with error flags per subsystem."""
    from app.startup.invariants import run_invariants

    bad_chroma = MagicMock()
    bad_chroma.list_collections.side_effect = RuntimeError("boom")
    redis = MagicMock()
    neo4j = _mk_neo4j(orphans=0)

    snap = run_invariants(bad_chroma, redis, neo4j)
    assert "errors" in snap
    assert any("chroma" in e for e in snap["errors"])


def test_invariants_include_healthy_flag():
    from app.startup.invariants import run_invariants

    chroma = _mk_chroma([{"name": "domain_general", "count": 50}])
    redis = MagicMock()
    neo4j = _mk_neo4j(orphans=0)

    snap = run_invariants(chroma, redis, neo4j)
    assert isinstance(snap.get("healthy_invariants"), bool)


def test_invariants_healthy_flag_false_when_nli_not_loaded():
    """NLI is a hard invariant — when the model isn't loaded, /health should
    flip to unhealthy."""
    from app.startup.invariants import run_invariants
    from core.utils import nli

    prior = getattr(nli, "_MODEL_LOADED", False)
    nli._MODEL_LOADED = False
    try:
        chroma = _mk_chroma([{"name": "domain_general", "count": 50}])
        redis = MagicMock()
        neo4j = _mk_neo4j(orphans=0)

        snap = run_invariants(chroma, redis, neo4j)
        assert snap["healthy_invariants"] is False
        assert snap["nli_model_loaded"] is False
    finally:
        nli._MODEL_LOADED = prior


def test_invariants_healthy_flag_true_when_all_good():
    from app.startup.invariants import run_invariants
    from core.utils import nli

    prior = getattr(nli, "_MODEL_LOADED", False)
    nli._MODEL_LOADED = True
    try:
        chroma = _mk_chroma([{"name": "domain_general", "count": 50}])
        redis = MagicMock()
        neo4j = _mk_neo4j(orphans=0)

        snap = run_invariants(chroma, redis, neo4j)
        assert snap["healthy_invariants"] is True
        assert snap["nli_model_loaded"] is True
    finally:
        nli._MODEL_LOADED = prior
