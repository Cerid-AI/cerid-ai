# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for inference-path degradation observability.

The inference path degrades gracefully (quenchforge -> ONNX / OpenRouter) but
those fallbacks were historically silent, so /health advertised a provider it
wasn't actually using. This records the TRUE serving state so health can't lie.
"""

from __future__ import annotations

import core.utils.inference_health as ih


def setup_function(_fn: object) -> None:
    ih.reset()  # isolate each test


def test_unknown_workload_is_not_degraded() -> None:
    snap = ih.snapshot()
    assert "rerank" not in snap  # nothing recorded yet


def test_fallback_marks_degraded_with_served_by() -> None:
    ih.record_fallback("rerank", configured="quenchforge", served_by="onnx", detail="502")
    snap = ih.snapshot()
    assert snap["rerank"]["degraded"] is True
    assert snap["rerank"]["serving"] == "onnx"
    assert snap["rerank"]["configured"] == "quenchforge"
    assert snap["rerank"]["fallback_count"] == 1


def test_success_clears_degradation() -> None:
    ih.record_fallback("llm", configured="quenchforge", served_by="openrouter", detail="conn refused")
    assert ih.snapshot()["llm"]["degraded"] is True
    ih.record_success("llm", provider="quenchforge")
    snap = ih.snapshot()
    assert snap["llm"]["degraded"] is False
    assert snap["llm"]["serving"] == "quenchforge"


def test_fallback_count_accumulates() -> None:
    for _ in range(3):
        ih.record_fallback("embed", configured="quenchforge", served_by="onnx")
    assert ih.snapshot()["embed"]["fallback_count"] == 3


def test_recorders_never_raise_on_bad_input() -> None:
    # observability must never break the inference call path
    ih.record_fallback(None, configured=None, served_by=None)  # type: ignore[arg-type]
    ih.record_success(None, provider=None)  # type: ignore[arg-type]
    assert isinstance(ih.snapshot(), dict)


def test_merge_into_routing_helper() -> None:
    ih.record_fallback("rerank", configured="quenchforge", served_by="onnx")
    block = {"provider": "quenchforge", "model": "bge-reranker-v2-m3"}
    merged = ih.annotate_block("rerank", block)
    assert merged["serving"] == "onnx"
    assert merged["degraded"] is True
    # block without a recorded event is annotated as not-degraded, serving=provider
    clean = ih.annotate_block("sparse", {"provider": "disabled"})
    assert clean["degraded"] is False
