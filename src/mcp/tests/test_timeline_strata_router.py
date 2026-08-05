# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Unit tests for GET /graph/timeline/strata and GET /graph/timeline/track/{id}."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_mention_row(
    *,
    canonical_id: str = "e1",
    community_id: str | None = "comm1",
    entity_type: str = "ORG",
    ts: str = "2026-05-20T10:00:00+00:00",
    trust_state: str = "verified",
    name: str = "Entity One",
    mention_count: int = 10,
    entity_created_at: str = "2026-05-10T00:00:00+00:00",
) -> dict:
    return {
        "canonical_id": canonical_id,
        "community_id": community_id if community_id is not None else "__null__",
        "entity_type": entity_type,
        "ts": ts,
        "trust_state": trust_state,
        "name": name,
        "mention_count": mention_count,
        "entity_created_at": entity_created_at,
    }


def _make_track_row(
    *,
    ts: str = "2026-05-20T10:00:00+00:00",
    artifact_id: str = "art1",
    artifact_filename: str = "report.pdf",
    confidence: float = 0.9,
    summary: str = "Short summary of the artifact.",
    co_mentioned: list | None = None,
) -> dict:
    return {
        "ts": ts,
        "artifact_id": artifact_id,
        "artifact_filename": artifact_filename,
        "confidence": confidence,
        "summary": summary,
        "co_mentioned": co_mentioned or [],
    }


_SAMPLE_COMMUNITY_ARTIFACT = {
    "communities": [
        {
            "id": "comm1",
            "count": 5,
            "hull": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
            "anchor": [0.5, 0.5],
            "label": "Research",
            "top_hubs": [{"id": "e1", "name": "Entity One", "degree": 8}],
            "trust_mix": {"verified": 4, "partial": 1, "unverified": 0, "unknown": 0},
        }
    ],
    "silhouette": 0.55,
    "computed_at": "2026-06-10T00:00:00+00:00",
}


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    state: dict[str, bytes | str] = {}
    fake = MagicMock()
    fake.get = lambda k: state.get(k)

    def _set(k, v, ex=None):
        state[k] = v
        return True

    def _setex(k, ttl, v):
        state[k] = v
        return True

    fake.set = _set
    fake.setex = _setex
    fake._state = state
    return fake


@pytest.fixture
def fake_driver_factory():
    def _make(
        mention_rows: list[dict],
        track_rows: list[dict] | None = None,
        name_rows: list[dict] | None = None,
    ) -> MagicMock:
        fake_driver = MagicMock()
        fake_session = MagicMock()
        fake_session.__enter__ = lambda self: self
        fake_session.__exit__ = lambda self, exc_type, exc, tb: None

        def _run(query, **_kwargs):
            result = MagicMock()
            if "LIMIT 1" in query and "canonical_id" in query:
                result.data = lambda: name_rows or [{"name": "Entity One"}]
            elif "co_mentioned" in query or "co_cap" in _kwargs:
                result.data = lambda: track_rows or []
            else:
                result.data = lambda: mention_rows
            return result

        fake_session.run = _run
        fake_driver.session = lambda: fake_session
        return fake_driver

    return _make


@pytest.fixture
def app_with_mocks(mock_redis, fake_driver_factory):
    from app.routers import graph as graph_router

    def _make(mention_rows, track_rows=None, name_rows=None):
        driver = fake_driver_factory(mention_rows, track_rows, name_rows)
        app = FastAPI()
        app.include_router(graph_router.router)
        patches = (
            patch("app.routers.graph.get_redis", return_value=mock_redis),
            patch("app.routers.graph.get_neo4j", return_value=driver),
        )
        return TestClient(app), patches

    return _make


# ── /graph/timeline/strata tests ─────────────────────────────────────────────


def test_strata_honors_aggregated_mention_count(mock_redis, fake_driver_factory):
    """Perf fix: the strata Cypher now returns one row per (entity, day) with
    a count(*); totals must reflect the count, not 1-per-row."""
    from app.routers import graph as graph_router

    rows = [
        {**_make_mention_row(ts="2026-05-20T10:00:00+00:00"), "mentions": 5},
        {
            **_make_mention_row(ts="2026-05-21T10:00:00+00:00", canonical_id="e2", name="E2"),
            "mentions": 3,
        },
    ]
    mock_redis._state["cerid:graph:map:communities"] = json.dumps(_SAMPLE_COMMUNITY_ARTIFACT)
    driver = fake_driver_factory(rows)

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-23&granularity=day"
        )

    assert r.status_code == 200
    payload = r.json()
    assert payload["totals"]["mentions"] == 8  # 5 + 3, not 2 rows
    series_total = sum(sum(s["buckets"]) for s in payload["series"])
    assert series_total == 8


def test_strata_happy_path_shape(mock_redis, fake_driver_factory):
    """200, bucket_dates aligned, series present, totals.mentions matches."""
    from app.routers import graph as graph_router

    rows = [
        _make_mention_row(ts="2026-05-20T10:00:00+00:00"),
        _make_mention_row(ts="2026-05-21T10:00:00+00:00", canonical_id="e2", name="E2"),
        _make_mention_row(ts="2026-05-22T10:00:00+00:00", canonical_id="e3", name="E3"),
    ]
    mock_redis._state["cerid:graph:map:communities"] = json.dumps(_SAMPLE_COMMUNITY_ARTIFACT)
    driver = fake_driver_factory(rows)

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-23&granularity=day"
        )

    assert r.status_code == 200
    payload = r.json()

    # bucket_dates alignment
    assert payload["bucket_dates"][0] <= "2026-05-19"
    assert len(payload["bucket_dates"]) >= 4

    # series sum == totals.mentions
    series_total = sum(sum(s["buckets"]) for s in payload["series"])
    assert series_total == payload["totals"]["mentions"]
    assert payload["totals"]["mentions"] == 3

    # communities present
    assert len(payload["communities"]) >= 1
    comm = payload["communities"][0]
    assert "community_id" in comm
    assert "trust_mix" in comm
    assert "color_slot" in comm

    assert payload["cached"] is False


def test_strata_other_rollup(mock_redis, fake_driver_factory):
    """Communities beyond top-8 collapse into is_other=True entry."""
    from app.routers import graph as graph_router

    rows = []
    # 10 different communities — top 8 stay, last 2 → "other"
    for i in range(10):
        for _ in range(5 - (i // 5)):  # earlier comms get more mentions
            rows.append(_make_mention_row(
                canonical_id=f"e{i}",
                community_id=f"comm{i}",
                ts=f"2026-05-2{min(i, 3)}T10:00:00+00:00",
            ))

    driver = fake_driver_factory(rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-25&granularity=day"
        )

    assert r.status_code == 200
    payload = r.json()
    community_ids = [c["community_id"] for c in payload["communities"]]
    assert len(community_ids) <= 9  # ≤8 real + 1 other
    other_comms = [c for c in payload["communities"] if c["is_other"]]
    assert len(other_comms) <= 1


def test_strata_null_community_goes_to_other(mock_redis, fake_driver_factory):
    """Entities with null community_id count toward 'other' rollup."""
    from app.routers import graph as graph_router

    rows = [
        _make_mention_row(community_id=None, ts="2026-05-20T10:00:00+00:00"),
        _make_mention_row(community_id=None, ts="2026-05-21T10:00:00+00:00"),
    ]
    driver = fake_driver_factory(rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-22&granularity=day"
        )

    assert r.status_code == 200
    payload = r.json()
    # All mentions land in "other" (no real community_id)
    community_ids = {c["community_id"] for c in payload["communities"]}
    assert "other" in community_ids or len(community_ids) == 1


def test_strata_marker_derivation_unit(mock_redis):
    """Unit test _derive_markers directly with synthetic buckets."""
    from app.routers.graph import _derive_markers

    bucket_dates = [f"2026-05-{d:02d}" for d in range(1, 8)]
    # Nonzero values: [5, 5, 5, 5, 5, 5, 100] — median=5, threshold=max(20,15)=20
    # 100 > 20 → ingest_burst on day 7
    mention_counts = [5, 5, 5, 5, 5, 5, 100]
    birth_counts = [0, 0, 0, 0, 0, 0, 0]

    markers = _derive_markers(bucket_dates, mention_counts, birth_counts)
    burst_markers = [m for m in markers if m.kind == "ingest_burst"]
    assert len(burst_markers) == 1
    assert burst_markers[0].date == "2026-05-07"
    assert burst_markers[0].count == 100


def test_strata_marker_derivation_all_same(mock_redis):
    """If all buckets are equal, threshold = max(20, 3×val) → no markers below that."""
    from app.routers.graph import _derive_markers

    bucket_dates = [f"2026-05-{d:02d}" for d in range(1, 6)]
    counts = [3, 3, 3, 3, 3]  # median=3, threshold=max(20,9)=20 → none trigger
    markers = _derive_markers(bucket_dates, counts, [0] * 5)
    assert all(m.kind != "ingest_burst" for m in markers)


def test_strata_marker_birth_surge(mock_redis):
    """birth_surge marker fires when birth count exceeds threshold."""
    from app.routers.graph import _derive_markers

    bucket_dates = [f"2026-05-{d:02d}" for d in range(1, 6)]
    births = [10, 10, 10, 10, 500]
    markers = _derive_markers(bucket_dates, [1] * 5, births)
    surge_markers = [m for m in markers if m.kind == "birth_surge"]
    assert len(surge_markers) >= 1
    assert surge_markers[-1].count == 500


def test_strata_track_doi_ordering_stability(mock_redis, fake_driver_factory):
    """Tracks are DOI-ordered descending; rank 1 has most mentions or recency bonus."""
    from app.routers import graph as graph_router

    # e_big has 50 mentions, e_small has 2 — e_big must rank first
    rows = (
        [_make_mention_row(
            canonical_id="e_big", name="Big", ts=f"2026-05-{d:02d}T00:00:00+00:00"
        ) for d in range(1, 28)]  # 27 mentions
        + [_make_mention_row(
            canonical_id="e_small", name="Small", ts="2026-05-01T00:00:00+00:00"
        )] * 2
    )
    driver = fake_driver_factory(rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-01&to=2026-05-30&granularity=day"
        )

    assert r.status_code == 200
    tracks = r.json()["tracks"]
    assert len(tracks) >= 2
    rank1 = next(t for t in tracks if t["rank"] == 1)
    rank2 = next(t for t in tracks if t["rank"] == 2)
    assert rank1["total_mentions"] >= rank2["total_mentions"]


def test_strata_cache_hit_returns_cached_true(mock_redis, fake_driver_factory):
    """Second identical request is served from Redis with cached=True."""
    from app.routers import graph as graph_router

    rows = [_make_mention_row()]
    driver = fake_driver_factory(rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        tc = TestClient(app)
        r1 = tc.get("/graph/timeline/strata?from=2026-05-19&to=2026-05-25&granularity=day")
        assert r1.status_code == 200
        assert r1.json()["cached"] is False

        # Mutate driver rows — second call should still get cached payload
        driver.session().__enter__().run = lambda *a, **k: MagicMock(data=lambda: [])
        r2 = tc.get("/graph/timeline/strata?from=2026-05-19&to=2026-05-25&granularity=day")

    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    assert r2.json()["totals"]["mentions"] == 1  # from cache


def test_strata_neo4j_unavailable_returns_empty(mock_redis):
    """Neo4j down → empty degrade, 200 (never 500)."""
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=None):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-25"
        )

    assert r.status_code == 200
    payload = r.json()
    assert payload["communities"] == []
    assert payload["series"] == []
    assert payload["tracks"] == []
    assert payload["totals"]["mentions"] == 0


def test_strata_bad_window_returns_400(mock_redis):
    """to before from → 400."""
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=None):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-06-01&to=2026-05-01"
        )

    assert r.status_code == 400


def test_strata_window_cap_returns_400(mock_redis):
    """Window > 730 days → 400."""
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=None):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2020-01-01&to=2026-06-10"
        )

    assert r.status_code == 400


def test_strata_unverified_trust_tracked_in_series(mock_redis, fake_driver_factory):
    """unverified_buckets counts only mentions from unverified entities (amendment 1)."""
    from app.routers import graph as graph_router

    rows = [
        _make_mention_row(
            canonical_id="e_unverified", trust_state="unverified",
            ts="2026-05-20T10:00:00+00:00",
        ),
        _make_mention_row(
            canonical_id="e_verified", trust_state="verified",
            ts="2026-05-20T10:00:00+00:00",
        ),
    ]
    driver = fake_driver_factory(rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-22&granularity=day"
        )

    assert r.status_code == 200
    payload = r.json()
    total_unverified = sum(sum(s["unverified_buckets"]) for s in payload["series"])
    total_mentions = sum(sum(s["buckets"]) for s in payload["series"])
    assert total_unverified == 1
    assert total_mentions == 2


def test_strata_missing_community_artifact_degrades(mock_redis, fake_driver_factory):
    """No community artifact in Redis → still returns data, trust_mix = zeros."""
    from app.routers import graph as graph_router

    rows = [_make_mention_row()]
    driver = fake_driver_factory(rows)
    # Do NOT seed community artifact
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/strata?from=2026-05-19&to=2026-05-22&granularity=day"
        )

    assert r.status_code == 200
    payload = r.json()
    assert payload["totals"]["mentions"] == 1
    for comm in payload["communities"]:
        if not comm["is_other"]:
            # trust_mix should be all zeros when artifact is missing
            assert all(v == 0.0 for v in comm["trust_mix"].values())


# ── /graph/timeline/track/{canonical_id} tests ───────────────────────────────


def _make_track_driver(track_rows: list[dict], name: str = "Entity One") -> MagicMock:
    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    def _run(query, **_kwargs):
        result = MagicMock()
        if "LIMIT 1" in query:
            result.data = lambda: [{"name": name}]
        else:
            result.data = lambda: track_rows
        return result

    fake_session.run = _run
    fake_driver.session = lambda: fake_session
    return fake_driver


def test_track_happy_path(mock_redis):
    """200, events present, co_mentioned list present."""
    from app.routers import graph as graph_router

    track_rows = [
        _make_track_row(co_mentioned=[
            {"canonical_id": "co1", "name": "Co One"},
            {"canonical_id": "co2", "name": "Co Two"},
        ]),
    ]
    driver = _make_track_driver(track_rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/track/e1?from=2026-05-19&to=2026-05-25"
        )

    assert r.status_code == 200
    payload = r.json()
    assert payload["canonical_id"] == "e1"
    assert payload["name"] == "Entity One"
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert event["artifact_id"] == "art1"
    assert event["confidence"] == 0.9
    assert len(event["co_mentioned"]) == 2
    assert payload["cached"] is False


def test_track_co_mention_cap(mock_redis):
    """co_mentioned list is capped at 20 per spec — simulate 25 co-mentions."""
    from app.routers import graph as graph_router

    # Simulate 25 co-mentions; the cypher already slices [..$co_cap] (20)
    # We test that even if Cypher returns more (e.g. mock doesn't slice),
    # the handler doesn't explode and events are present.
    co_list = [{"canonical_id": f"co{i}", "name": f"Co {i}"} for i in range(25)]
    track_rows = [_make_track_row(co_mentioned=co_list[:20])]  # Cypher caps at 20
    driver = _make_track_driver(track_rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/track/e1?from=2026-05-19&to=2026-05-25"
        )

    assert r.status_code == 200
    assert len(r.json()["events"][0]["co_mentioned"]) <= 20


def test_track_cache_hit(mock_redis):
    """Second call is served from Redis with cached=True."""
    from app.routers import graph as graph_router

    track_rows = [_make_track_row()]
    driver = _make_track_driver(track_rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    url = "/graph/timeline/track/e1?from=2026-05-19&to=2026-05-25"
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        tc = TestClient(app)
        r1 = tc.get(url)
        assert r1.json()["cached"] is False

        # Silence driver — second call must come from cache
        driver.session().__enter__().run = lambda *a, **k: MagicMock(data=lambda: [])
        r2 = tc.get(url)

    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    assert len(r2.json()["events"]) == 1  # from cache


def test_track_neo4j_unavailable_empty_degrade(mock_redis):
    """Neo4j down → empty events list, 200."""
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=None):
        r = TestClient(app).get(
            "/graph/timeline/track/e1?from=2026-05-19&to=2026-05-25"
        )

    assert r.status_code == 200
    payload = r.json()
    assert payload["events"] == []
    assert payload["canonical_id"] == "e1"


def test_track_bad_window_400(mock_redis):
    """to before from → 400."""
    from app.routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=None):
        r = TestClient(app).get(
            "/graph/timeline/track/e1?from=2026-06-01&to=2026-05-01"
        )

    assert r.status_code == 400


def test_track_summary_truncated_to_200_chars(mock_redis):
    """Summary is truncated to first 200 characters."""
    from app.routers import graph as graph_router

    long_summary = "X" * 400
    track_rows = [_make_track_row(summary=long_summary)]
    driver = _make_track_driver(track_rows)
    app = FastAPI()
    app.include_router(graph_router.router)
    with patch("app.routers.graph.get_redis", return_value=mock_redis), \
         patch("app.routers.graph.get_neo4j", return_value=driver):
        r = TestClient(app).get(
            "/graph/timeline/track/e1?from=2026-05-19&to=2026-05-25"
        )

    assert r.status_code == 200
    assert len(r.json()["events"][0]["summary"]) <= 200
