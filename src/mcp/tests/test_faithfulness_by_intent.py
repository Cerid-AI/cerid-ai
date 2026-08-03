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
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.931, n=12,
        source="fixtures",
    )

    raw = redis.get("cerid:ragas:by_intent:compiled_summary")
    data = json.loads(raw)
    assert data["faithfulness"] == 0.931
    assert data["n"] == 12
    assert data["source"] == "fixtures"


def test_the_two_producers_do_not_share_a_key():
    """The collision that produced the retracted 0.917.

    The nightly (hand-authored fixtures, ~0.9 by construction) and the soak
    (live product answers) both slice by router intent and both land
    compiled_summary at n=30, so the stored payload gave no way to tell them
    apart and the GA metric reported whichever ran last.
    """
    from core.utils.cache import record_faithfulness_by_intent

    redis = _make_fakeredis()
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.917, n=30,
        source="fixtures",
    )
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.548, n=30,
        source="live",
    )

    fixtures = json.loads(redis.get("cerid:ragas:by_intent:compiled_summary"))
    live = json.loads(redis.get("cerid:ragas:live_by_intent:compiled_summary"))
    assert fixtures["faithfulness"] == 0.917, "the fixture number must survive"
    assert live["faithfulness"] == 0.548, "the live number must not be overwritten"


def test_an_unknown_source_is_rejected():
    """A typo'd source must not silently create a third namespace."""
    import pytest

    from core.utils.cache import record_faithfulness_by_intent

    with pytest.raises(ValueError, match="source must be one of"):
        record_faithfulness_by_intent(
            _make_fakeredis(), intent="compiled_summary", faithfulness=0.9, n=1,
            source="nightly",
        )


def test_record_faithfulness_by_intent_noop_when_redis_none():
    from core.utils.cache import record_faithfulness_by_intent

    # Must not raise.
    record_faithfulness_by_intent(None, intent="x", faithfulness=0.9, n=1, source="live")


def test_record_faithfulness_never_raises_on_redis_error():
    from core.utils.cache import record_faithfulness_by_intent

    class _Boom:
        def set(self, *_a, **_k):
            raise RuntimeError("redis down")

    record_faithfulness_by_intent(
        _Boom(), intent="compiled_summary", faithfulness=0.9, n=3, source="live",
    )


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
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.95, n=20, source="live",
        abstention_rate=0.0,
    )

    res = collector.metric_faithfulness(redis)
    assert res["actual"] == 0.95
    assert res["denominator"] == 20
    assert res["meets_target"] is True  # clears the floor, abstention at 0


def test_a_high_score_bought_by_refusing_does_not_pass():
    """The floor alone is gameable downward, which is why it can be lowered.

    Faithfulness is entailed/total claims, so terser answers score higher and
    an abstention leaves the mean entirely. A run that scores well because the
    product stopped answering must fail, not pass.
    """
    from core.utils.cache import record_faithfulness_by_intent

    collector = _load_collector()
    redis = _make_fakeredis()
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.98, n=8, source="live",
        abstention_rate=0.45,
    )

    res = collector.metric_faithfulness(redis)
    assert res["actual"] == 0.98, "the score itself is excellent"
    assert res["meets_target"] is False, (
        "but 45% of questions went unanswered — that is not quality"
    )


def test_a_run_without_the_counter_metric_does_not_pass_by_default():
    """A pre-counter-metric soak result must read as unmet, not as passing."""
    from core.utils.cache import record_faithfulness_by_intent

    collector = _load_collector()
    redis = _make_fakeredis()
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.90, n=29, source="live",
    )

    res = collector.metric_faithfulness(redis)
    assert res["abstention_rate"] is None
    assert res["meets_target"] is False


def test_the_gate_ignores_the_fixture_number():
    """No fallback: fixture data must never satisfy the live metric.

    Falling back when the live key is empty would report a number measured on
    self-scoring fixtures as though it were the product's — the exact
    substitution this metric was retracted for.
    """
    from core.utils.cache import record_faithfulness_by_intent

    collector = _load_collector()
    redis = _make_fakeredis()
    record_faithfulness_by_intent(
        redis, intent="compiled_summary", faithfulness=0.917, n=30,
        source="fixtures",
    )

    res = collector.metric_faithfulness(redis)
    assert res["actual"] is None, (
        "the fixture number must not be reported as live faithfulness"
    )
    assert res["denominator"] == 0
