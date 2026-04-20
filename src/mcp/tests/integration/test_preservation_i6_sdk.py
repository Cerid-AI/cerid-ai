# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I6 — /sdk/v1/* contract stability.

Preservation invariant: the SDK namespace is the public contract for
cerid-trading-agent / cerid-finance / cerid-boardroom. Breaking a
response shape breaks downstream consumers silently. The
consolidation program MUST NOT touch /sdk/v1/* handlers unless the
same PR updates the consumer repos.

Scope covered:
  * /sdk/v1/health returns {status, version, features, internal_llm}
  * /sdk/v1/query returns an envelope compatible with SDKQueryResponse
  * /sdk/v1/hallucination returns per-claim shape
  * /sdk/v1/collections returns a listable object
  * /sdk/v1/taxonomy returns the full domain tree

Out of scope (separate tests):
  * Per-consumer rate-limit enforcement — belongs in
    test_preservation_i6_sdk_rate_limits if we grow concern.
  * /sdk/v1/ingest — write path; covered by I7's round-trip.
"""
from __future__ import annotations


def test_sdk_health_returns_envelope(http_client):
    r = http_client.get("/sdk/v1/health")
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    for key in ("status", "version", "features", "internal_llm"):
        assert key in body, f"/sdk/v1/health missing {key!r}"
    assert body["status"] in ("healthy", "degraded"), (
        f"status must be 'healthy' or 'degraded'; got {body['status']!r}"
    )


def test_sdk_health_version_is_semver_string(http_client):
    body = http_client.get("/sdk/v1/health").json()
    version = body["version"]
    assert isinstance(version, str) and version, "version must be non-empty string"
    parts = version.split(".")
    assert len(parts) >= 2, f"version {version!r} not in X.Y.Z form"


def test_sdk_query_returns_result_envelope(http_client):
    r = http_client.post(
        "/sdk/v1/query",
        json={"query": "parallel computing", "domains": ["general"], "n_results": 3},
    )
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    # SDK response contract — the minimum superset every consumer reads.
    for key in ("results", "sources", "source_breakdown"):
        assert key in body, f"/sdk/v1/query missing {key!r}"


def test_sdk_hallucination_returns_claim_list(http_client):
    import uuid
    conv_id = f"preservation-i6-sdk-{uuid.uuid4().hex[:8]}"
    r = http_client.post(
        "/sdk/v1/hallucination",
        json={
            "response_text": (
                "The Eiffel Tower is in Paris, France. "
                "It was completed in 1889."
            ),
            "conversation_id": conv_id,
            "user_query": "Where is the Eiffel Tower?",
        },
    )
    assert r.status_code in (200, 202), f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert "claims" in body, f"/sdk/v1/hallucination missing 'claims': {body}"
    assert isinstance(body["claims"], list), (
        f"claims must be list, got {type(body['claims']).__name__}"
    )
    # Full round-trip to avoid leaking a stub :VerificationReport (the
    # Sprint C bug: /agent/hallucination alone creates stubs via
    # promote_verified_facts; only /verification/save completes the
    # provenance). Preserves /health.verification_report_orphans=0
    # across the preservation suite.
    http_client.post(
        "/verification/save",
        json={
            "conversation_id": conv_id,
            "claims": body.get("claims", []),
            "overall_score": body.get("overall_score", 1.0),
            "verified": body.get("verified", 0),
            "unverified": body.get("unverified", 0),
            "uncertain": body.get("uncertain", 0),
            "total": body.get("total", len(body.get("claims", []))),
        },
    )


def test_sdk_collections_returns_listable(http_client):
    r = http_client.get("/sdk/v1/collections")
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    # Consumers loop on the response — it must be a list or a dict
    # with a well-known list key. Whichever shape it is today, it
    # must stay stable.
    assert isinstance(body, (list, dict)), (
        f"/sdk/v1/collections must be list|dict; got {type(body).__name__}"
    )


def test_sdk_taxonomy_returns_domain_tree(http_client):
    r = http_client.get("/sdk/v1/taxonomy")
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert isinstance(body, dict), f"/sdk/v1/taxonomy must be dict; got {type(body).__name__}"
    # Response shape: {"domains": [list of names], "taxonomy": {name: {description, icon, sub_categories}}}
    assert "domains" in body and "taxonomy" in body, (
        f"/sdk/v1/taxonomy must carry 'domains' + 'taxonomy' keys; got {list(body)[:10]}"
    )
    domain_names = set(body["domains"])
    # Minimum coverage — every shipped core domain must appear.
    for required_domain in ("general", "coding", "finance"):
        assert required_domain in domain_names, (
            f"/sdk/v1/taxonomy 'domains' list missing {required_domain!r}; got {sorted(domain_names)}"
        )
        assert required_domain in body["taxonomy"], (
            f"/sdk/v1/taxonomy 'taxonomy' tree missing {required_domain!r}"
        )


def test_sdk_prefix_is_exclusively_v1(http_client):
    """Guard: no stray /sdk/v2/ endpoints or unprefixed /sdk/ routes
    exist. A versioning slip would break every consumer."""
    spec = http_client.get("/openapi.json").json()
    paths = list(spec.get("paths", {}))
    sdk_paths = [p for p in paths if p.startswith("/sdk/")]
    non_v1 = [p for p in sdk_paths if not p.startswith("/sdk/v1/")]
    assert not non_v1, (
        f"Non-/sdk/v1/ routes leaked into SDK namespace: {non_v1}"
    )
    assert sdk_paths, "No /sdk/* routes found — SDK router not mounted?"
