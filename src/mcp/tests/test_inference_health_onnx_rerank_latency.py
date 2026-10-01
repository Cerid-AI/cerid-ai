# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F350: the in-process ONNX rerank leg reports its latency to /health.

Only the two remote rerank clients wrote rerank_latency_ms, so a host whose
every rerank ran in-process reported the latency as unmeasured for ever.
The periodic provider recheck also replaced the config object and dropped
whatever latency had been measured.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

import core.utils.inference_health as ih
import utils.inference_config as ic
from core.retrieval import reranker


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def perf_counter(self) -> float:
        return self.now


class _Tokenizer:
    def encode_batch(self, pairs):
        return [
            SimpleNamespace(ids=[1, 2], attention_mask=[1, 1], type_ids=[0, 0], overflowing=[])
            for _ in pairs
        ]


class _Session:
    """Stands in for the ONNX runtime; each inference takes ``cost_s``."""

    def __init__(self, clock: _Clock, cost_s: float) -> None:
        self._clock = clock
        self._cost_s = cost_s

    def get_inputs(self):
        return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

    def run(self, _outputs, feeds):
        self._clock.now += self._cost_s
        return [np.zeros((len(feeds["input_ids"]), 1), dtype=np.float32)]


@pytest.fixture
def cfg(monkeypatch) -> ic.InferenceConfig:
    ih.reset()
    cfg = ic.InferenceConfig(provider="ollama", tier=ic.InferenceTier.GOOD)
    monkeypatch.setattr(ic, "_config", cfg)
    return cfg


@pytest.fixture
def onnx(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(reranker, "time", SimpleNamespace(perf_counter=clock.perf_counter), raising=False)
    monkeypatch.setattr(reranker, "_session", _Session(clock, cost_s=0.040))
    monkeypatch.setattr(reranker, "_tokenizer", _Tokenizer())
    return clock


def _results(n: int) -> list[dict]:
    return [{"content": f"doc {i}", "relevance": 0.9 - i * 0.1} for i in range(n)]


def test_in_process_rerank_reports_its_latency(cfg, onnx):
    assert ic.inference_health_payload()["rerank_latency_ms"] is None

    reranker.rerank("query", _results(3))

    assert ic.inference_health_payload()["rerank_latency_ms"] == pytest.approx(40.0)


def test_rerank_that_never_runs_the_model_records_nothing(cfg, onnx):
    reranker.rerank("query", _results(1))

    assert ic.inference_health_payload()["rerank_latency_ms"] is None


async def test_provider_recheck_keeps_measured_latencies(cfg, monkeypatch):
    cfg.rerank_latency_ms = 40.0
    cfg.embed_latency_ms = 12.5
    monkeypatch.setenv("INFERENCE_RECHECK_INTERVAL", "300")
    monkeypatch.delenv("INFERENCE_MODE", raising=False)
    # The probes are the boundary: subprocess and HTTP calls to the host.
    monkeypatch.setattr(ic, "_probe_gpu", lambda _plat: (False, ""))
    monkeypatch.setattr(ic, "_probe_ollama", lambda: True)
    monkeypatch.setattr(ic, "_probe_sidecar", lambda: (False, ""))

    async def _no_probe() -> None:
        return None

    monkeypatch.setattr(ic, "probe_local_throughput", _no_probe)

    sleeps = {"n": 0}

    async def _sleep(_seconds: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise RuntimeError("stop after one pass")

    monkeypatch.setattr(
        ic, "asyncio", SimpleNamespace(sleep=_sleep, to_thread=asyncio.to_thread),
    )

    with pytest.raises(RuntimeError, match="stop after one pass"):
        await ic._inference_recheck_loop()

    assert ic.get_inference_config() is not cfg
    payload = ic.inference_health_payload()
    assert payload["rerank_latency_ms"] == pytest.approx(40.0)
    assert payload["embed_latency_ms"] == pytest.approx(12.5)
