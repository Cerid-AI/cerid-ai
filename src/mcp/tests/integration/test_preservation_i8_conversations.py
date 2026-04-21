# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""I8 — Conversation CRUD and sync-directory persistence.

Preservation invariant: the ``/user-state/conversations`` CRUD surface
is how the GUI stores chat history and how cross-machine sync moves
conversation state. A regression silently loses chat history or
breaks multi-device sync.

Scope covered:
  * GET /user-state/conversations returns a list
  * POST /user-state/conversations writes a conversation
  * GET /user-state/conversations/{id} reads it back
  * POST /user-state/conversations/bulk accepts multiple
  * DELETE /user-state/conversations/{id} removes it

Out of scope:
  * FE-side archive semantics — ``archived: true`` is stored as a
    conversation field client-side; no dedicated endpoint exists.
    The FE guards this via use-conversations.ts vitest tests.
  * /preferences PATCH — covered by existing user-state unit tests.

Skips gracefully when CERID_SYNC_DIR isn't configured — some CI
environments run without a sync directory mounted.
"""
from __future__ import annotations

import time
import uuid

import pytest


def _sync_configured(http_client) -> bool:
    """Probe: GET /user-state/conversations returns 200 + a list when
    the sync directory is configured; returns [] via the short-circuit
    otherwise. A 503 on POST would be the unambiguous signal."""
    r = http_client.get("/user-state/conversations")
    return r.status_code == 200


def test_list_conversations_returns_iterable(http_client):
    if not _sync_configured(http_client):
        pytest.skip("sync directory not configured in this environment")
    r = http_client.get("/user-state/conversations")
    assert r.status_code == 200, (
        f"/user-state/conversations HTTP {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert isinstance(body, list), (
        f"GET /user-state/conversations must return list; got {type(body).__name__}"
    )


def test_conversation_crud_round_trip(http_client, cleanup_ids):
    if not _sync_configured(http_client):
        pytest.skip("sync directory not configured in this environment")

    conv_id = f"preservation-i8-{uuid.uuid4().hex[:8]}"
    cleanup_ids.append(("conversation", conv_id))

    payload = {
        "id": conv_id,
        "title": "Preservation I8 test",
        "messages": [{"role": "user", "content": "hi", "timestamp": int(time.time())}],
        "updatedAt": int(time.time() * 1000),
    }

    # 1. Create
    r = http_client.post("/user-state/conversations", json=payload)
    if r.status_code == 503:
        pytest.skip(f"sync directory reported 503 at write: {r.text[:120]}")
    assert r.status_code == 200, (
        f"POST /user-state/conversations HTTP {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("saved") == conv_id, (
        f"POST response should echo saved id; got {body}"
    )

    # 2. Read-back
    r = http_client.get(f"/user-state/conversations/{conv_id}")
    assert r.status_code == 200, (
        f"GET /user-state/conversations/{conv_id} HTTP {r.status_code}: {r.text[:200]}"
    )
    fetched = r.json()
    assert fetched.get("id") == conv_id
    assert fetched.get("title") == "Preservation I8 test"

    # 3. Delete
    r = http_client.delete(f"/user-state/conversations/{conv_id}")
    assert r.status_code == 200, (
        f"DELETE /user-state/conversations/{conv_id} HTTP {r.status_code}"
    )
    assert r.json().get("deleted") == conv_id


def test_conversation_missing_id_returns_400(http_client):
    if not _sync_configured(http_client):
        pytest.skip("sync directory not configured in this environment")
    r = http_client.post("/user-state/conversations", json={"title": "no id here"})
    # Either 400 (documented) or 422 (Pydantic) is acceptable — this
    # test guards against silent acceptance of malformed writes.
    assert r.status_code in (400, 422), (
        f"Missing 'id' should return 400/422; got HTTP {r.status_code}: {r.text[:200]}"
    )


def test_bulk_conversation_save(http_client, cleanup_ids):
    if not _sync_configured(http_client):
        pytest.skip("sync directory not configured in this environment")
    ids = [f"preservation-i8-bulk-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for i in ids:
        cleanup_ids.append(("conversation", i))
    payload = [
        {"id": i, "title": f"bulk {n}", "messages": [], "updatedAt": int(time.time() * 1000)}
        for n, i in enumerate(ids)
    ]
    r = http_client.post("/user-state/conversations/bulk", json=payload)
    if r.status_code == 503:
        pytest.skip("sync directory reported 503")
    assert r.status_code == 200, (
        f"POST /user-state/conversations/bulk HTTP {r.status_code}: {r.text[:200]}"
    )
    assert r.json().get("saved") == 3, f"Expected saved=3; got {r.json()}"
