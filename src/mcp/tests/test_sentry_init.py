# src/mcp/tests/test_sentry_init.py
from unittest.mock import patch

import pytest


def test_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN_MCP", raising=False)
    from app.observability.sentry_init import init_sentry
    assert init_sentry() is False


def test_initialises_when_dsn_set(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN_MCP", "https://example@o1.ingest.sentry.io/1")
    with patch("sentry_sdk.init") as mock_init:
        from app.observability.sentry_init import init_sentry
        result = init_sentry()
    assert result is True
    mock_init.assert_called_once()
    kwargs = mock_init.call_args.kwargs
    assert kwargs["traces_sampler"] is not None
    assert kwargs["profiles_sample_rate"] == 0.1
    assert kwargs["send_default_pii"] is False
    assert "openrouter_api_key" in kwargs["event_scrubber"].denylist


def test_traces_sampler_down_samples_poll_paths():
    from app.observability.sentry_init import _traces_sampler
    # SDK-native shape: transaction name lives under transaction_context.name
    def ctx(name: str) -> dict:
        return {"transaction_context": {"name": name}}

    # Poll endpoints: heavily down-sampled
    assert _traces_sampler(ctx("GET /health")) < 0.02
    assert _traces_sampler(ctx("GET /health/status")) < 0.02
    assert _traces_sampler(ctx("GET /ingestion/progress")) < 0.02
    assert _traces_sampler(ctx("GET /observability/queue-depth")) < 0.02

    # Real endpoints: default SENTRY_TRACES_SAMPLE_RATE
    default_rate = float(__import__("os").environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    assert _traces_sampler(ctx("POST /agent/query")) == default_rate
    assert _traces_sampler(ctx("POST /chat/stream")) == default_rate

    # Edge case: empty / missing context should fall through to default rate
    assert _traces_sampler({}) == default_rate
    assert _traces_sampler({"transaction_context": {}}) == default_rate

    # Edge case: span_context fallback (child spans on older SDKs)
    assert _traces_sampler({"span_context": {"name": "GET /health"}}) < 0.02


def test_cookies_in_denylist():
    from app.observability.sentry_init import _EXTRA_DENYLIST
    assert "cookies" in _EXTRA_DENYLIST
    assert "set-cookie" in _EXTRA_DENYLIST
    assert "x-session-id" in _EXTRA_DENYLIST


# ---------------------------------------------------------------------------
# before_send rate limit — caps per-fingerprint event emission
# ---------------------------------------------------------------------------
#
# 2026-04-26 incident: a single Neo4j WAL crashloop emitted 1,716 events
# into one Sentry issue group in ~1h. Sentry's server-side grouping
# collapsed them into one issue but each event still cost quota. The
# rate-limit guards the SDK side: drop the (N+1)th event whose
# fingerprint already fired within the window.


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """The rate-limit state is module-level. Tests that add timestamps
    must not leak into the next test."""
    from app.observability import sentry_init
    sentry_init._rate_limit_state.clear()
    yield
    sentry_init._rate_limit_state.clear()


def _event(exc_type: str = "HTTPException", transaction: str = "POST /sdk/v1/query") -> dict:
    return {
        "exception": {"values": [{"type": exc_type, "value": "Couldn't connect to neo4j:7687"}]},
        "transaction": transaction,
    }


def test_rate_limit_allows_first_max_events():
    from app.observability.sentry_init import _RATE_LIMIT_MAX, _before_send
    for i in range(_RATE_LIMIT_MAX):
        assert _before_send(_event(), {}) is not None, f"event {i+1} of {_RATE_LIMIT_MAX} dropped early"


def test_rate_limit_drops_burst_above_max():
    from app.observability.sentry_init import _RATE_LIMIT_MAX, _before_send
    for _ in range(_RATE_LIMIT_MAX):
        _before_send(_event(), {})
    # Next event for the same fingerprint must drop.
    assert _before_send(_event(), {}) is None
    # And every subsequent event in the same window also drops.
    for _ in range(50):
        assert _before_send(_event(), {}) is None


def test_rate_limit_isolates_distinct_fingerprints():
    """A burst on one error class must not block events for a different
    error class. Otherwise one outage gags all unrelated alerts."""
    from app.observability.sentry_init import _RATE_LIMIT_MAX, _before_send
    for _ in range(_RATE_LIMIT_MAX):
        _before_send(_event("HTTPException", "POST /sdk/v1/query"), {})
    # Different transaction → different fingerprint → not throttled.
    assert _before_send(_event("HTTPException", "POST /sdk/v1/hallucination"), {}) is not None
    # Different exception type → different fingerprint → not throttled.
    assert _before_send(_event("ValueError", "POST /sdk/v1/query"), {}) is not None


def test_rate_limit_window_expires(monkeypatch):
    """After the window elapses, the fingerprint's budget refreshes."""
    from app.observability import sentry_init
    fake_now = [1000.0]
    monkeypatch.setattr(sentry_init.time, "time", lambda: fake_now[0])

    for _ in range(sentry_init._RATE_LIMIT_MAX):
        sentry_init._before_send(_event(), {})
    # Saturated.
    assert sentry_init._before_send(_event(), {}) is None

    # Advance past the window. The next event must be allowed.
    fake_now[0] += sentry_init._RATE_LIMIT_WINDOW_S + 1
    assert sentry_init._before_send(_event(), {}) is not None


def test_rate_limit_idle_fingerprints_get_gc_d(monkeypatch):
    """Long-idle fingerprints must drop out of the state dict so the
    rate-limit table can't grow unboundedly under churn."""
    from app.observability import sentry_init
    fake_now = [2000.0]
    monkeypatch.setattr(sentry_init.time, "time", lambda: fake_now[0])

    sentry_init._before_send(_event("ErrA"), {})
    sentry_init._before_send(_event("ErrB"), {})
    assert len(sentry_init._rate_limit_state) == 2

    # Advance past the GC TTL and emit a new event. The two idle
    # fingerprints should be evicted as a side-effect.
    fake_now[0] += sentry_init._RATE_LIMIT_TTL_S + 1
    sentry_init._before_send(_event("ErrC"), {})
    assert "HTTPException|ErrA" not in sentry_init._rate_limit_state
    assert "HTTPException|ErrB" not in sentry_init._rate_limit_state


def test_init_wires_before_send(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN_MCP", "https://example@o1.ingest.sentry.io/1")
    with patch("sentry_sdk.init") as mock_init:
        from app.observability.sentry_init import _before_send, init_sentry
        init_sentry()
    kwargs = mock_init.call_args.kwargs
    assert kwargs["before_send"] is _before_send
