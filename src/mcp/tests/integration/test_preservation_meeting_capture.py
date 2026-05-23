# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Meeting capture preservation invariants — Phase E Day 5.

Locks the contracts the Sources → Meetings UI depends on:

  * /settings/hf-token (GET) — status-only response shape, never echoes value
  * /settings/whisper/models — list shape, cache_dir, current_default
  * /meetings/upload — suffix gating (400 for unknown)
  * /meetings/jobs (GET) — list response
  * /meetings/job/{id} — 404 for unknown id

Run inside the integration harness against a live stack.
"""
from __future__ import annotations

from io import BytesIO

import pytest

pytestmark = pytest.mark.preservation


def test_hf_token_status_endpoint_shape(http_client):
    r = http_client.get("/settings/hf-token")
    assert r.status_code == 200, f"/settings/hf-token {r.status_code}: {r.text[:200]}"
    body = r.json()
    # Status-only contract: must NOT echo the token value
    assert "configured" in body
    assert "last4" in body
    assert "updated_at" in body
    assert "token" not in body
    assert "value" not in body


def test_whisper_models_endpoint_shape(http_client):
    r = http_client.get("/settings/whisper/models")
    assert r.status_code == 200, f"/settings/whisper/models {r.status_code}"
    body = r.json()
    assert "models" in body and isinstance(body["models"], list)
    assert "cache_dir" in body and isinstance(body["cache_dir"], str)
    assert "current_default" in body
    assert len(body["models"]) >= 5  # at least tiny/base/small/medium/large-v3
    # Each model carries the documented contract
    for m in body["models"]:
        for key in ("id", "filename", "size_mb", "rtf_estimate", "quality",
                    "description", "cached"):
            assert key in m, f"model missing key {key}: {m}"


def test_meetings_upload_rejects_unsupported_suffix(http_client, mcp_base, http_headers):
    # The shared http_client fixture defaults Content-Type to
    # application/json. Multipart uploads need a content-type with
    # boundary that httpx generates itself. Use a fresh client with
    # only the auth header so httpx's `files=` can set its own
    # multipart content-type.
    import httpx

    auth_only = {k: v for k, v in http_headers.items() if k.lower() != "content-type"}
    with httpx.Client(base_url=mcp_base, headers=auth_only, timeout=60.0) as c:
        r = c.post(
            "/meetings/upload",
            files={"file": ("note.txt", BytesIO(b"hi"), "text/plain")},
        )
    assert r.status_code == 400, f"expected 400 for .txt, got {r.status_code}"
    assert "unsupported audio type" in r.json().get("detail", "").lower()


def test_meetings_jobs_endpoint_returns_list(http_client):
    r = http_client.get("/meetings/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_meetings_job_unknown_returns_404(http_client):
    r = http_client.get("/meetings/job/nonexistent_test_id_xyz")
    assert r.status_code == 404
