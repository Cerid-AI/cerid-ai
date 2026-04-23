# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 2.2 — Idempotency-Key on every OpenRouter POST.

The audit identified that retried OpenRouter calls without a stable
``Idempotency-Key`` could double-bill the cost ledger and leave orphan
verification rows for the m0002 cleanup to reconcile. Every POST from
``core.utils.llm_client`` now sends an ``Idempotency-Key`` of the form
``<request_id>-<uuid12>``:

* ``request_id`` from ``core.utils.tracing`` for log correlation;
* ``uuid12`` so two LLM calls inside the same FastAPI request get
  distinct keys (verification calls many claims per request).

These tests assert the header is sent, has the expected shape, and that
distinct ``call_llm`` invocations produce distinct keys.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from core.utils import llm_client
from core.utils.llm_client import _new_idempotency_key
from core.utils.tracing import request_id_var

pytestmark = pytest.mark.asyncio


_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+-[0-9a-f]{12}$")


# ---------------------------------------------------------------------------
# _new_idempotency_key — pure helper semantics
# ---------------------------------------------------------------------------


async def test_idempotency_key_uses_request_id_when_set() -> None:
    token = request_id_var.set("req-abc-123")
    try:
        key = _new_idempotency_key()
    finally:
        request_id_var.reset(token)
    assert key.startswith("req-abc-123-")
    assert _KEY_RE.match(key)


async def test_idempotency_key_falls_back_to_no_req_when_unset() -> None:
    # Default contextvar value is "" (empty); helper substitutes "no-req".
    key = _new_idempotency_key()
    assert key.startswith("no-req-")
    assert _KEY_RE.match(key)


async def test_idempotency_key_is_unique_per_call() -> None:
    keys = {_new_idempotency_key() for _ in range(50)}
    assert len(keys) == 50, "uuid suffix must make each key distinct"


# ---------------------------------------------------------------------------
# call_llm — Idempotency-Key reaches the OpenRouter POST
# ---------------------------------------------------------------------------


class _RecordingResponse:
    status_code = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


class _RecordingClient:
    """Fake httpx client capturing the headers of every POST."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.posts: list[dict[str, Any]] = []
        self._body = body

    async def post(self, _url: str, **kwargs: Any) -> _RecordingResponse:
        self.posts.append(kwargs)
        return _RecordingResponse(self._body)

    @property
    def is_closed(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class _RecordingClientCtx:
    def __init__(self, client: _RecordingClient) -> None:
        self._client = client

    async def __aenter__(self) -> _RecordingClient:
        return self._client

    async def __aexit__(self, *_: Any) -> None:
        return None


def _install_recording_client(monkeypatch: pytest.MonkeyPatch, client: _RecordingClient) -> None:
    monkeypatch.setattr(
        llm_client, "_acquire_client", lambda: _RecordingClientCtx(client),
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")


async def test_call_llm_sends_idempotency_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"choices": [{"message": {"content": "hi"}}]}
    rec = _RecordingClient(body)
    _install_recording_client(monkeypatch, rec)

    await llm_client.call_llm([{"role": "user", "content": "hello"}], model="x/y")

    assert len(rec.posts) == 1
    headers = rec.posts[0]["headers"]
    assert "Idempotency-Key" in headers
    assert _KEY_RE.match(headers["Idempotency-Key"])


async def test_call_llm_raw_sends_idempotency_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"choices": [{"message": {"content": "hi"}}]}
    rec = _RecordingClient(body)
    _install_recording_client(monkeypatch, rec)

    await llm_client.call_llm_raw([{"role": "user", "content": "hello"}], model="x/y")

    assert len(rec.posts) == 1
    headers = rec.posts[0]["headers"]
    assert "Idempotency-Key" in headers
    assert _KEY_RE.match(headers["Idempotency-Key"])


async def test_two_call_llm_invocations_get_distinct_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct logical calls within the same request must be deduplicatable
    independently — same-request cost-ledger writes for different claims
    must not collide on a single key."""
    body = {"choices": [{"message": {"content": "hi"}}]}
    rec = _RecordingClient(body)
    _install_recording_client(monkeypatch, rec)

    token = request_id_var.set("req-xyz")
    try:
        await llm_client.call_llm([{"role": "user", "content": "a"}], model="x/y")
        await llm_client.call_llm([{"role": "user", "content": "b"}], model="x/y")
    finally:
        request_id_var.reset(token)

    assert len(rec.posts) == 2
    keys = [post["headers"]["Idempotency-Key"] for post in rec.posts]
    assert keys[0] != keys[1]
    # Both must carry the request_id prefix for trace correlation.
    assert all(k.startswith("req-xyz-") for k in keys)


async def test_idempotency_key_uuid_suffix_is_well_formed() -> None:
    """The suffix is ``uuid4().hex[:12]`` — verify by parsing both sides."""
    token = request_id_var.set("req-shape")
    try:
        key = _new_idempotency_key()
    finally:
        request_id_var.reset(token)
    prefix, suffix = key.rsplit("-", 1)
    assert prefix == "req-shape"
    assert len(suffix) == 12
    # Suffix must be a valid hex prefix (uuid4().hex[:12])
    int(suffix, 16)
    # Sanity: a freshly generated uuid4 hex shares character class.
    assert all(c in "0123456789abcdef" for c in uuid.uuid4().hex[:12])
