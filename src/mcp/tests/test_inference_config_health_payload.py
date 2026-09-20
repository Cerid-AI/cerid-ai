# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The /health `inference` block must not contradict `inference_routing`.

F350: the block is a boot-time snapshot, so it reported tier "good" through a
20-hour outage while `inference_routing.rerank` in the SAME payload said
`{"serving": "onnx", "degraded": true}`. And `rerank_latency_ms` is only ever
written by the two backend clients, never by the in-process ONNX leg that was
actually serving all 44 reranks — so the number an operator would read to answer
"is reranking slow?" was structurally pinned at 0.0.
"""

from __future__ import annotations

import core.utils.inference_health as ih
import utils.inference_config as ic


def setup_function(_fn: object) -> None:
    ih.reset()


def _boot_config(monkeypatch):
    cfg = ic.InferenceConfig(
        provider="ollama",
        tier=ic.InferenceTier.GOOD,
        message="Ollama available for LLM tasks",
    )
    monkeypatch.setattr(ic, "_config", cfg)
    return cfg


def test_degraded_lane_downgrades_the_block(monkeypatch):
    _boot_config(monkeypatch)
    ih.record_fallback(
        "rerank", configured="quenchforge", served_by="onnx",
        detail="no rerank slot configured",
    )
    payload = ic.inference_health_payload()
    assert payload["tier"] == ic.InferenceTier.DEGRADED.value
    assert "rerank" in payload["degraded_workloads"]


def test_healthy_boot_state_is_left_alone(monkeypatch):
    _boot_config(monkeypatch)
    payload = ic.inference_health_payload()
    assert payload["tier"] == "good"
    assert payload["degraded_workloads"] == []


def test_unmeasured_rerank_latency_is_null_not_zero(monkeypatch):
    """0.0 reads as 'instant'. The truth is 'never measured'."""
    _boot_config(monkeypatch)
    payload = ic.inference_health_payload()
    assert payload["rerank_latency_ms"] is None
    assert payload["embed_latency_ms"] is None


def test_measured_latency_is_reported(monkeypatch):
    cfg = _boot_config(monkeypatch)
    cfg.rerank_latency_ms = 812.345
    payload = ic.inference_health_payload()
    assert payload["rerank_latency_ms"] == 812.35
