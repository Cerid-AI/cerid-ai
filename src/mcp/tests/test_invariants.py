# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


# ---------------------------------------------------------------------------
# Task 5 — batched divergence probe: one Chroma get() per collection instead
# of one per artifact (200 -> 4 gets on a 4-domain sample).
# ---------------------------------------------------------------------------


class _FakeDivergenceCollection:
    """In-memory Chroma collection double that counts .get() calls."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self.get_calls = 0

    def get(self, ids=None, where=None):
        self.get_calls += 1
        present = [i for i in (ids or []) if i in self._store]
        return {"ids": present}


class _FakeDivergenceChroma:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeDivergenceCollection] = {}

    def get_or_create_collection(self, name, **kwargs):
        return self.collections.setdefault(name, _FakeDivergenceCollection())


def _mk_neo4j_rows(rows: list[dict]) -> MagicMock:
    """A driver mock whose session().run() yields exactly ``rows`` once."""
    neo4j = MagicMock()
    session = MagicMock()
    session.run.return_value = iter(rows)
    neo4j.session.return_value.__enter__.return_value = session
    neo4j.session.return_value.__exit__.return_value = False
    return neo4j


def test_probe_divergence_issues_one_get_per_collection() -> None:
    """200 sampled artifacts spread across 4 domains must issue exactly 4
    Chroma get() calls (one per collection) and report the same residual
    counts a naive per-artifact implementation would."""
    import config
    from app.startup.invariants import _DIVERGENCE_SAMPLE_LIMIT, _probe_divergence

    assert _DIVERGENCE_SAMPLE_LIMIT == 200

    domains = ["div-alpha", "div-bravo", "div-charlie", "div-delta"]
    chroma = _FakeDivergenceChroma()
    rows: list[dict] = []
    expected_two_store = 0
    expected_vector_visible_archived = 0

    for i in range(_DIVERGENCE_SAMPLE_LIMIT):
        domain = domains[i % len(domains)]
        chunk_id = f"{domain}-chunk-{i}"
        coll = chroma.get_or_create_collection(config.collection_name(domain))
        present = (i % 10) != 0  # every 10th artifact is missing its chunk
        if present:
            coll._store[chunk_id] = {"cerid_state": "committed"}
        archived = (i % 25) == 0
        rows.append(
            {
                "id": f"art-{i}",
                "chunk_ids": [chunk_id],
                "chunk_count": 1,
                "domain": domain,
                "archived": archived,
            }
        )
        if not present:
            expected_two_store += 1
        if archived and present:
            expected_vector_visible_archived += 1

    neo4j = _mk_neo4j_rows(rows)

    result = _probe_divergence(chroma, neo4j)

    total_gets = sum(c.get_calls for c in chroma.collections.values())
    assert len(chroma.collections) == 4
    assert total_gets == 4, f"expected exactly 4 Chroma get() calls, got {total_gets}"
    assert result == {
        "two_store_residual": expected_two_store,
        "vector_visible_archived": expected_vector_visible_archived,
    }


def test_probe_divergence_empty_sample_issues_no_gets() -> None:
    from app.startup.invariants import _probe_divergence

    chroma = _FakeDivergenceChroma()
    neo4j = _mk_neo4j_rows([])

    result = _probe_divergence(chroma, neo4j)

    assert result == {"two_store_residual": 0, "vector_visible_archived": 0}
    assert chroma.collections == {}


# ---------------------------------------------------------------------------
# Task 5 — invariants refresh moves off the /health request path.
# ---------------------------------------------------------------------------


def test_get_invariants_snapshot_pending_before_first_refresh() -> None:
    import app.startup.invariants as inv

    prior_cache, prior_ts = inv._cache, inv._cache_computed_at
    inv._cache = None
    inv._cache_computed_at = None
    try:
        assert inv.get_invariants_snapshot() == {"status": "pending"}
    finally:
        inv._cache, inv._cache_computed_at = prior_cache, prior_ts


def test_refresh_invariants_snapshot_populates_cache_with_computed_at(monkeypatch) -> None:
    import app.startup.invariants as inv

    monkeypatch.setattr(inv, "run_invariants", lambda c, r, n: {"healthy_invariants": True})

    result = inv.refresh_invariants_snapshot(MagicMock(), MagicMock(), MagicMock())

    assert result["healthy_invariants"] is True
    assert "computed_at" in result and result["computed_at"]

    cached = inv.get_invariants_snapshot()
    assert cached["healthy_invariants"] is True
    assert cached["computed_at"] == result["computed_at"]


def test_health_snapshot_does_not_call_run_invariants_on_request_path(monkeypatch) -> None:
    """/health must serve the background-refreshed snapshot, never call
    run_invariants() inline on the request path."""
    import app.routers.health as h
    import app.startup.invariants as inv
    from core.utils import nli

    prior_cache, prior_ts = inv._cache, inv._cache_computed_at
    prior_loaded = getattr(nli, "_MODEL_LOADED", False)
    inv._cache = {"healthy_invariants": True, "two_store_residual": 0}
    inv._cache_computed_at = "2026-09-06T00:00:00+00:00"
    # The live overlay re-reads this flag on every rebuild — set it to match
    # the cached snapshot's claim.
    nli._MODEL_LOADED = True

    def _must_not_run(*a, **k):
        raise AssertionError("run_invariants must not run on the /health request path")

    monkeypatch.setattr(inv, "run_invariants", _must_not_run)
    monkeypatch.setattr(h, "get_neo4j", lambda: MagicMock())
    monkeypatch.setattr(h, "get_chroma", lambda: MagicMock())
    monkeypatch.setattr(h, "get_redis", lambda: MagicMock())

    try:
        snap = h._invariants_snapshot()
    finally:
        inv._cache, inv._cache_computed_at = prior_cache, prior_ts
        nli._MODEL_LOADED = prior_loaded

    assert snap["healthy_invariants"] is True
    assert snap["nli_model_loaded"] is True
    assert snap["computed_at"] == "2026-09-06T00:00:00+00:00"
    assert "mcp" in snap


def test_refresh_invariants_loop_runs_immediately_then_sleeps(monkeypatch) -> None:
    import asyncio

    import app.startup.invariants as inv

    calls: list[float] = []
    sleeps: list[float] = []

    def fake_refresh(chroma, redis, neo4j):
        calls.append(1)
        return {"healthy_invariants": True}

    monkeypatch.setattr(inv, "refresh_invariants_snapshot", fake_refresh)
    monkeypatch.setattr(inv, "get_neo4j", lambda: MagicMock())
    monkeypatch.setattr(inv, "get_chroma", lambda: MagicMock())
    monkeypatch.setattr(inv, "get_redis", lambda: MagicMock())

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(inv.asyncio, "sleep", fake_sleep)

    asyncio.run(inv.refresh_invariants_loop())

    assert len(calls) == 1, "the loop must refresh immediately at startup"
    assert sleeps == [inv.INVARIANTS_REFRESH_S]


def test_refresh_invariants_loop_skips_refresh_in_lightweight_mode(monkeypatch) -> None:
    """A None Neo4j driver (lightweight mode) must not attempt the refresh."""
    import asyncio

    import app.startup.invariants as inv

    calls: list[float] = []

    monkeypatch.setattr(
        inv, "refresh_invariants_snapshot", lambda c, r, n: calls.append(1),
    )
    monkeypatch.setattr(inv, "get_neo4j", lambda: None)
    monkeypatch.setattr(inv, "get_chroma", lambda: MagicMock())
    monkeypatch.setattr(inv, "get_redis", lambda: MagicMock())

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(inv.asyncio, "sleep", fake_sleep)

    asyncio.run(inv.refresh_invariants_loop())

    assert calls == []


def test_main_lifespan_wires_invariants_refresh_loop() -> None:
    """Static contract: main.py's lifespan must start refresh_invariants_loop
    next to the BM25 warm-up (mirrors the existing E1 ollama-prewarm-gate
    static contract test)."""
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8",
    )
    assert "refresh_invariants_loop" in main_src
    assert "invariants_refresh_task" in main_src
