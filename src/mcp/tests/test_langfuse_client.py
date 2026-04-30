# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Langfuse client wrapper (Workstream E Phase 5b).

The wrapper's contract is "no-op cleanly when unconfigured" — these
tests prove that callers pay zero overhead and never raise when
``LANGFUSE_PUBLIC_KEY`` is unset, which is the default state.
"""

from __future__ import annotations

from unittest.mock import patch

from core.observability import langfuse_client as lc


def _reset_singleton() -> None:
    """Clear the memoised client between tests."""
    lc._client = None
    lc._client_init_failed = False


def test_is_enabled_false_when_keys_missing(monkeypatch):
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "")
    _reset_singleton()
    assert lc.is_enabled() is False


def test_get_client_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "")
    _reset_singleton()
    assert lc.get_client() is None


def test_trace_context_yields_none_when_disabled(monkeypatch):
    """A caller using `with lc.trace(...) as t:` gets t=None and never raises."""
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "")
    _reset_singleton()
    with lc.trace("test_trace") as t:
        assert t is None
        # Nested span on a None parent must also no-op
        with lc.span(t, "child_step", stage="test") as s:
            assert s is None


def test_score_trace_no_op_on_none():
    """score_trace silently accepts a None trace."""
    lc.score_trace(None, name="faithfulness", value=0.95)


def test_should_sample_llm_judge_zero_rate_returns_false(monkeypatch):
    monkeypatch.setattr(lc, "LANGFUSE_LLM_JUDGE_SAMPLE_RATE", 0.0)
    assert lc.should_sample_llm_judge() is False


def test_should_sample_llm_judge_full_rate_returns_true(monkeypatch):
    monkeypatch.setattr(lc, "LANGFUSE_LLM_JUDGE_SAMPLE_RATE", 1.0)
    # With rate=1.0, every call should return True
    for _ in range(10):
        assert lc.should_sample_llm_judge() is True


def test_should_sample_llm_judge_low_rate_distribution(monkeypatch):
    """At rate 0.5, ~50% of 1000 calls return True (loose bound — 35..65%)."""
    monkeypatch.setattr(lc, "LANGFUSE_LLM_JUDGE_SAMPLE_RATE", 0.5)
    sampled = sum(1 for _ in range(1000) if lc.should_sample_llm_judge())
    assert 350 <= sampled <= 650, f"sampled={sampled}/1000 outside loose CI"


def test_init_failure_is_logged_not_raised(monkeypatch):
    """If Langfuse SDK init throws, the client is permanently disabled
    and every subsequent get_client() returns None without re-raising.
    """
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(lc, "_langfuse_available", True)
    _reset_singleton()

    fake_langfuse_ctor = patch.object(
        lc, "Langfuse", side_effect=RuntimeError("simulated init error"),
    )
    with fake_langfuse_ctor:
        first = lc.get_client()
        second = lc.get_client()
    assert first is None
    assert second is None
    # Singleton state: _client_init_failed latched True
    assert lc._client_init_failed is True
