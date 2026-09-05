# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Hermetic coverage for quenchforge rerank fall-through + once-per-process warn.

When ``RERANK_PROVIDER=quenchforge`` but the slot is missing, query_agent must
return None (sidecar/ONNX continues), record inference_health fallback, and
emit a single WARNING — not a retry loop, not a live Quenchforge call.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core.agents import query_agent
from core.utils import inference_health


@pytest.fixture(autouse=True)
def _reset_rerank_fail_signal() -> None:
    query_agent._QUENCHFORGE_RERANK_FAIL_WARNED = False
    inference_health.reset()
    yield
    query_agent._QUENCHFORGE_RERANK_FAIL_WARNED = False
    inference_health.reset()


def _no_slot_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:11434/v1/rerank")
    response = httpx.Response(
        503,
        request=request,
        json={
            "error": "no rerank slot configured. Check `quenchforge doctor` for status.",
        },
    )
    return httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=request,
        response=response,
    )


@pytest.mark.asyncio
async def test_quenchforge_rerank_no_slot_warns_once_and_records_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")
    docs = [{"content": "a", "relevance": 0.5}]
    err = _no_slot_error()

    with (
        patch(
            "utils.quenchforge_client.quenchforge_rerank",
            new=AsyncMock(side_effect=err),
        ),
        caplog.at_level(logging.WARNING, logger="ai-companion.query_agent"),
    ):
        first = await query_agent._maybe_rerank_via_quenchforge(docs, "q")
        second = await query_agent._maybe_rerank_via_quenchforge(docs, "q")

    assert first is None
    assert second is None

    snap = inference_health.snapshot()
    assert snap["rerank"]["degraded"] is True
    assert snap["rerank"]["configured"] == "quenchforge"
    assert snap["rerank"]["serving"] == "onnx"
    # Fallback is recorded on every miss; the WARNING is once-per-process.
    assert snap["rerank"]["fallback_count"] == 2

    warns = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "ai-companion.query_agent"
        and "RERANK_PROVIDER=sidecar" in rec.getMessage()
    ]
    assert len(warns) == 1


@pytest.mark.asyncio
async def test_sidecar_rerank_provider_does_not_call_quenchforge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "sidecar")
    mock_rerank = AsyncMock()
    with patch("utils.quenchforge_client.quenchforge_rerank", new=mock_rerank):
        out = await query_agent._maybe_rerank_via_quenchforge(
            [{"content": "a", "relevance": 0.5}], "q",
        )
    assert out is None
    mock_rerank.assert_not_awaited()
    assert "rerank" not in inference_health.snapshot()
