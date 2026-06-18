# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the by-intent faithfulness soak-metric producer.

`scripts/k_program_metrics.py::metric_faithfulness` reads
`cerid:ragas:by_intent:compiled_summary` but nothing wrote it — the metric was
dark. `core.utils.cache.record_faithfulness_by_intent` is the producer (the
direct parallel to `record_chunks_per_answer`). An end-to-end test against the
real reader protects the contract.
"""
from __future__ import annotations

import importlib.util
import json

import pytest

from ._helpers import scripts_dir


def _make_fakeredis():
    try:
        import fakeredis

        return fakeredis.FakeRedis()
    except ImportError:  # pragma: no cover
        pytest.skip("fakeredis not installed")


def _load_collector():
    sd = scripts_dir()
    if sd is None:
        pytest.skip("scripts/ dir not reachable from test env")
    spec = importlib.util.spec_from_file_location(
        "k_program_metrics", sd / "k_program_metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_record_faithfulness_by_intent_roundtrip():
    from core.utils.cache import record_faithfulness_by_intent

    redis = _make_fakeredis()
    record_faithfulness_by_intent(redis, intent="compiled_summary", faithfulness=0.931, n=12)

    raw = redis.get("cerid:ragas:by_intent:compiled_summary")
    data = json.loads(raw)
    assert data["faithfulness"] == 0.931
    assert data["n"] == 12


def test_record_faithfulness_by_intent_noop_when_redis_none():
    from core.utils.cache import record_faithfulness_by_intent

    # Must not raise.
    record_faithfulness_by_intent(None, intent="x", faithfulness=0.9, n=1)


def test_record_faithfulness_never_raises_on_redis_error():
    from core.utils.cache import record_faithfulness_by_intent

    class _Boom:
        def set(self, *_a, **_k):
            raise RuntimeError("redis down")

    record_faithfulness_by_intent(_Boom(), intent="compiled_summary", faithfulness=0.9, n=3)


def test_loading_collector_does_not_leak_dotenv_into_environ():
    """Importing the collector must have NO env side effects.

    `scripts/k_program_metrics.py` used to call `_load_dotenv_into_environ()` at
    module level, so exec-loading it mid-suite injected repo `.env` keys into the
    global `os.environ` — which silently broke other tests (e.g.
    `test_ollama_models` reads `OLLAMA_DEFAULT_MODEL`). `.env` must load on CLI
    invocation only.
    """
    import os

    # OLLAMA_DEFAULT_MODEL is set only by the repo .env, never the test env.
    os.environ.pop("OLLAMA_DEFAULT_MODEL", None)
    _load_collector()
    assert "OLLAMA_DEFAULT_MODEL" not in os.environ, (
        "importing the collector leaked .env into os.environ"
    )


def test_emit_then_real_metric_faithfulness_reads():
    """The producer's write is read back by the soak collector, contract-exact."""
    from core.utils.cache import record_faithfulness_by_intent

    collector = _load_collector()
    redis = _make_fakeredis()
    record_faithfulness_by_intent(redis, intent="compiled_summary", faithfulness=0.95, n=20)

    res = collector.metric_faithfulness(redis)
    assert res["actual"] == 0.95
    assert res["denominator"] == 20
    assert res["meets_target"] is True  # 0.95 >= 0.92 target
