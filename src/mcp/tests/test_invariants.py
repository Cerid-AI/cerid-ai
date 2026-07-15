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

    # NOTE: the custom-collection exemplar must be a name that can NEVER become
    # a built-in domain. "domain_trading" was used here originally, but trading
    # IS a built-in domain in internal builds once the internal bootstrap has
    # extended the taxonomy — which made this test order-dependent on whether
    # an earlier test in the session refreshed config.DOMAINS.
    chroma = _mk_chroma([
        {"name": "domain_general", "count": 50},
        {"name": "domain_clientzz", "count": 0},
        {"name": "domain_finance", "count": 0},
    ])
    redis = MagicMock()
    neo4j = _mk_neo4j(orphans=0)

    snap = run_invariants(chroma, redis, neo4j)
    assert "collections_empty" in snap
    # Built-in surface with no data — the real "empty" signal operators act on.
    assert "domain_finance" in snap["collections_empty"]
    assert "domain_general" not in snap["collections_empty"]
    # A custom/client collection (not a built-in domain) must NOT pollute the
    # built-in empty signal even when empty (a freshly-created client domain is
    # normal); it's surfaced separately so operators see client activity. (P5)
    assert "domain_clientzz" not in snap["collections_empty"]
    assert "custom_collections" in snap
    assert "domain_clientzz" in snap["custom_collections"]
    assert "domain_finance" not in snap["custom_collections"]
    assert "domain_general" not in snap["custom_collections"]


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


# ---------------------------------------------------------------------------
# Bi-temporal :Fact orphan invariant (m0004/m0006) — B4
# ---------------------------------------------------------------------------

def _mk_neo4j_fact_orphans(count: int) -> MagicMock:
    """A driver mock returning `count` for the :Fact-orphan COUNT query,
    isolated from the verification-report-orphan probe's mock shape."""
    neo4j = MagicMock()
    neo4j.session().__enter__().run.return_value.single.return_value = {
        "orphans": count
    }
    return neo4j


def test_probe_fact_orphans_zero_when_no_facts() -> None:
    """No writer exists yet (m0006 is schema-only) — an empty graph (no
    :Fact nodes at all) must report 0, not an error. The Cypher's own
    MATCH-on-absent-label semantics guarantee this; the probe adds no
    special-casing."""
    from app.startup.invariants import _probe_fact_orphans

    neo4j = _mk_neo4j_fact_orphans(0)
    result = _probe_fact_orphans(neo4j)
    assert result == {"fact_orphans": 0}


def test_probe_fact_orphans_surfaces_orphan_count() -> None:
    """A :Fact with no inbound :HAS_FACT edge is a writer-regression
    signal — the probe must surface the count, mirroring
    verification_report_orphans."""
    from app.startup.invariants import _probe_fact_orphans

    neo4j = _mk_neo4j_fact_orphans(3)
    result = _probe_fact_orphans(neo4j)
    assert result == {"fact_orphans": 3}


def test_invariants_snapshot_includes_fact_orphans() -> None:
    """run_invariants wires _probe_fact_orphans in beside the
    verification-orphan probe — fact_orphans must appear in the full
    snapshot and must NOT flip healthy_invariants (non-critical, same
    treatment as verification_report_orphans)."""
    from app.startup.invariants import run_invariants
    from core.utils import nli

    prior = getattr(nli, "_MODEL_LOADED", False)
    nli._MODEL_LOADED = True
    try:
        chroma = _mk_chroma([{"name": "domain_general", "count": 50}])
        redis = MagicMock()
        neo4j = _mk_neo4j(orphans=0)

        snap = run_invariants(chroma, redis, neo4j)
        assert "fact_orphans" in snap
        assert snap["healthy_invariants"] is True
    finally:
        nli._MODEL_LOADED = prior
