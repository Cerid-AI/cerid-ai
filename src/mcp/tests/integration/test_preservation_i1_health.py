# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I1 — /health is truthful and complete.

Preservation invariant: a running stack must expose the full Task-14
invariants envelope via /health, with:

  * status == "healthy"
  * invariants.healthy_invariants == True
  * invariants.verification_report_orphans == 0 (post-v0.84.1 writer fix)
  * invariants.internal_modules present (Phase 1 observability)
  * invariants.swallowed_errors_last_hour present (Phase 2 observability)
  * invariants.nli_model_loaded == True (hard gate — verification depends on it)

If any of these drift, the observability contract the consolidation
program is building on has been broken. Every sprint after A must
keep this test green.
"""
from __future__ import annotations


def test_health_returns_healthy_status(http_client):
    r = http_client.get("/health")
    assert r.status_code == 200, f"/health HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["status"] == "healthy", f"status={body.get('status')!r}"


def test_health_invariants_envelope_complete(http_client):
    body = http_client.get("/health").json()
    inv = body.get("invariants")
    assert inv is not None, "/health.invariants block missing"
    # Every preservation gate below is a field we committed to maintaining.
    for field in (
        "healthy_invariants",
        "verification_report_orphans",
        "internal_modules",
        "swallowed_errors_last_hour",
        "nli_model_loaded",
    ):
        assert field in inv, f"/health.invariants.{field} missing"


def test_health_no_verification_report_orphans(http_client):
    body = http_client.get("/health").json()
    orphans = body["invariants"]["verification_report_orphans"]
    assert orphans == 0, (
        f"verification_report_orphans={orphans}; m0002 migration should keep this at 0. "
        "A non-zero count indicates a writer regression — check /agent/hallucination "
        "and /verification/save paths."
    )


def test_health_nli_model_loaded(http_client):
    body = http_client.get("/health").json()
    assert body["invariants"]["nli_model_loaded"] is True, (
        "NLI model failed to load — verification, Self-RAG, and RAGAS all degrade silently"
    )


def test_health_flips_to_healthy_when_critical_invariants_pass(http_client):
    body = http_client.get("/health").json()
    assert body["invariants"]["healthy_invariants"] is True, (
        "healthy_invariants is False — at least one critical invariant failed. "
        f"errors: {body['invariants'].get('errors', [])}"
    )
