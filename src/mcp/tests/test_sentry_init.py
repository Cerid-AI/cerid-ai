# src/mcp/tests/test_sentry_init.py
from unittest.mock import patch


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
