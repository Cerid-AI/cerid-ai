# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A reranker that was asked for by name and is not there shows in /health.

On the Mac Studio RERANK_PROVIDER was ``sidecar`` and nothing listened on the
sidecar's port. Every rerank was served by the in-process model and the lane
read ``serving: unknown, degraded: false``.
"""
from __future__ import annotations

import asyncio

import pytest

from core.agents import query_agent
from core.utils import inference_health
from core.utils.inference_routing import get_routing_snapshot
from utils import inference_config


@pytest.fixture
def _no_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    inference_health.reset()
    monkeypatch.setattr(
        inference_config,
        "get_inference_config",
        lambda: inference_config.InferenceConfig(
            provider="onnx-cpu", sidecar_available=False, sidecar_url="http://localhost:8889",
        ),
    )
    yield
    inference_health.reset()


def _rerank_lane() -> dict:
    block = dict(get_routing_snapshot()["rerank"])
    return inference_health.annotate_block("rerank", block)


@pytest.mark.usefixtures("_no_sidecar")
def test_named_and_absent_is_degraded_and_says_what_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "sidecar")

    out = asyncio.run(query_agent._maybe_rerank_via_sidecar([{"content": "a"}], "q"))

    assert out is None  # falls through to the in-process model
    lane = _rerank_lane()
    assert lane["degraded"] is True
    assert lane["serving"] == "onnx"
    assert "http://localhost:8889" in lane["degraded_detail"]


@pytest.mark.usefixtures("_no_sidecar")
@pytest.mark.parametrize("value", [None, "in-process", "quenchforge"])
def test_not_named_is_not_degraded(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("RERANK_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("RERANK_PROVIDER", value)

    asyncio.run(query_agent._maybe_rerank_via_sidecar([{"content": "a"}], "q"))

    assert _rerank_lane()["degraded"] is False


# ---------------------------------------------------------------------------
# The in-process reranker, when it is the one named
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_health():
    inference_health.reset()
    yield
    inference_health.reset()


@pytest.mark.usefixtures("_clean_health")
def test_named_and_serving_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "in-process")

    query_agent._record_in_process_rerank(served=True)

    lane = _rerank_lane()
    assert lane["serving"] == "in-process"
    assert lane["degraded"] is False


@pytest.mark.usefixtures("_clean_health")
def test_named_and_failing_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "in-process")

    query_agent._record_in_process_rerank(served=False, detail="model file missing")

    lane = _rerank_lane()
    assert lane["degraded"] is True
    assert lane["serving"] == "none"
    assert lane["degraded_detail"] == "model file missing"


@pytest.mark.usefixtures("_clean_health")
def test_serving_as_a_fallback_does_not_clear_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_PROVIDER", "sidecar")
    inference_health.record_fallback("rerank", configured="sidecar", served_by="onnx", detail="refused")

    query_agent._record_in_process_rerank(served=True)

    lane = _rerank_lane()
    assert lane["degraded"] is True
    assert lane["serving"] == "onnx"


def test_the_rerank_path_reports_both_outcomes() -> None:
    import inspect

    src = inspect.getsource(query_agent._rerank_cross_encoder)
    assert "_record_in_process_rerank(served=True)" in src
    assert "_record_in_process_rerank(served=False" in src
