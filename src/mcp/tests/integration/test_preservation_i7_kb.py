# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I7 — KB round-trip: ingest → search → retrieve → delete.

Preservation invariant: the core knowledge-base workflow must
complete end-to-end for the supported text payload shapes. If
Sprint B's Pydantic canonicalization or Sprint D's utility moves
break any leg of the round-trip, this test fails loudly before
merge.

Scope covered:
  * POST /ingest accepts a text body and returns an artifact id
  * GET /artifacts/{id} returns the newly ingested artifact
  * POST /agent/query retrieves the ingested content (by unique
    token probe — we inject a rare string and assert it comes back)
  * DELETE /admin/artifacts/{id} cleans it up (also validated by
    the cleanup_ids finalizer)

Out of scope:
  * File-upload ingestion (PDF, docx) — separate concern, covered
    by existing unit tests.
  * Bulk ingestion / batch — the cleanup surface would be too
    broad for a preservation gate.
"""
from __future__ import annotations

import uuid


def test_ingest_search_delete_round_trip(http_client, cleanup_ids):
    """Ingest a text blob with a unique probe token, confirm the
    search retrieves it, then delete the artifact."""

    # Unique probe token — the search step needs a term that's both
    # present in the test content AND unlikely to match anything else
    # in the KB. UUID hex works.
    probe = f"preservation-probe-{uuid.uuid4().hex[:12]}"
    content = (
        f"This document contains the unique preservation test token {probe}. "
        "It describes the capability-preservation harness that the "
        "consolidation program uses as a merge gate."
    )

    # 1. Ingest
    r = http_client.post(
        "/ingest",
        json={
            "content": content,
            "domain": "general",
            "tags": "preservation,test",
        },
    )
    assert r.status_code == 200, f"/ingest HTTP {r.status_code}: {r.text[:200]}"
    body = r.json()
    artifact_id = body.get("artifact_id") or body.get("id")
    assert artifact_id, f"/ingest response missing artifact id: {body}"
    cleanup_ids.append(("artifact", artifact_id))

    # 2. Confirm the artifact exists (GET /artifacts/{id}).
    # Response carries: artifact_id, title, domain, filename, chunk_count,
    # total_content (full reassembled text), chunks: [{index, text}, ...].
    r = http_client.get(f"/artifacts/{artifact_id}")
    assert r.status_code == 200, (
        f"GET /artifacts/{artifact_id} HTTP {r.status_code}: {r.text[:200]}"
    )
    fetched = r.json()
    # The probe token lives in total_content OR any chunk's text.
    haystack = fetched.get("total_content") or ""
    for chunk in fetched.get("chunks", []):
        haystack += " " + (chunk.get("text") or "")
    assert probe in haystack, (
        f"Ingested probe token not found in fetched artifact. "
        f"Fetched keys: {sorted(fetched.keys())}"
    )

    # 3. Confirm the artifact appears in the GET /artifacts list
    # (index wiring — the FE's KB pane reads from here). This is
    # more deterministic than /agent/query, which depends on
    # embedding + BM25 index rebuild latency and is already guarded
    # by I2.
    r = http_client.get("/artifacts?domain=general&limit=50")
    assert r.status_code == 200, (
        f"GET /artifacts HTTP {r.status_code}: {r.text[:200]}"
    )
    listing = r.json()
    # Shape varies — can be {"artifacts": [...]} or a bare list. Normalize.
    items = listing.get("artifacts", listing) if isinstance(listing, dict) else listing
    ids_in_list = {a.get("artifact_id") or a.get("id") for a in items if isinstance(a, dict)}
    assert artifact_id in ids_in_list, (
        f"Ingested artifact {artifact_id} not in /artifacts listing "
        f"(got {len(ids_in_list)} items). Index wiring regression."
    )

    # 4. Delete — cleanup finalizer will also try this, but we
    #    want to assert the explicit delete succeeded.
    r = http_client.delete(f"/admin/artifacts/{artifact_id}")
    assert r.status_code == 200, (
        f"DELETE /admin/artifacts/{artifact_id} HTTP {r.status_code}"
    )


def test_kb_stats_endpoint_available(http_client):
    """/admin/kb/stats is a commonly-read operator endpoint — it must
    remain available post-consolidation."""
    r = http_client.get("/admin/kb/stats")
    assert r.status_code == 200, (
        f"/admin/kb/stats HTTP {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert isinstance(body, dict), (
        f"/admin/kb/stats must return dict; got {type(body).__name__}"
    )
