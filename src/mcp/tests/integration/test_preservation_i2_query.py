# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I2 — /agent/query returns the canonical QueryEnvelope shape.

Preservation invariant: every /agent/query response must carry the
Task-0 envelope (results, sources, source_breakdown) with

  * ``results`` and ``sources`` arrays of equal length
  * ``source_breakdown`` keys == {"kb", "memory", "external"} (the three
    retrieval lanes the FE renders as separate source columns)
  * ``sum(source_breakdown.values()) == len(results)`` — the shape
    invariant that v0.84.0's "QueryEnvelope single-writer" fix
    guarantees (CHANGELOG wave 0)

A regression here silently drops source attribution in the chat UI.
"""
from __future__ import annotations

QUERY_BODY = {
    "query": "what is parallel computing",
    "domains": ["general"],
    "n_results": 5,
}


def test_agent_query_returns_200_with_envelope(http_client):
    r = http_client.post("/agent/query", json=QUERY_BODY)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
    body = r.json()
    for key in ("results", "sources", "source_breakdown", "strategy"):
        assert key in body, f"/agent/query envelope missing {key!r}"


def test_agent_query_results_and_sources_length_match(http_client):
    body = http_client.post("/agent/query", json=QUERY_BODY).json()
    assert len(body["sources"]) == len(body["results"]), (
        f"sources ({len(body['sources'])}) vs results ({len(body['results'])}) drift"
    )


def test_agent_query_source_breakdown_has_three_lanes(http_client):
    body = http_client.post("/agent/query", json=QUERY_BODY).json()
    bd = body["source_breakdown"]
    for lane in ("kb", "memory", "external"):
        assert lane in bd, f"source_breakdown missing {lane!r} lane; got {list(bd)}"


def test_agent_query_source_breakdown_sums_to_results_length(http_client):
    body = http_client.post("/agent/query", json=QUERY_BODY).json()
    total = sum(len(v) for v in body["source_breakdown"].values())
    assert total == len(body["results"]), (
        f"source_breakdown total {total} != len(results) {len(body['results'])}"
        " — envelope shape drift (see CHANGELOG v0.84.0 Wave 0)"
    )


def test_agent_query_caches_warm_reads(http_client):
    """Second identical call returns ``cached: true`` — confirms the
    semantic-cache layer is wired and honored."""
    http_client.post("/agent/query", json=QUERY_BODY)  # warm
    r = http_client.post("/agent/query", json=QUERY_BODY)
    body = r.json()
    # Either "cached" (new flag) or "cache_hit" (legacy) — tolerate both
    # until Sprint B normalizes.
    cached = body.get("cached", body.get("cache_hit"))
    assert cached is True, (
        "Expected cache hit on second identical query; got "
        f"cached={cached}. Semantic cache may be disabled or collection dim mismatch."
    )
