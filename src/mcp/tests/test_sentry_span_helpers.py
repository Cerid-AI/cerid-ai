# src/mcp/tests/test_sentry_span_helpers.py
from unittest.mock import MagicMock, patch

import pytest


def test_span_is_noop_without_transaction():
    from core.observability.span_helpers import span
    with patch("sentry_sdk.get_current_span", return_value=None):
        with span("retrieval.chroma", "fan-out", domains=3) as s:
            assert s is None


def test_span_creates_child_under_transaction():
    from core.observability.span_helpers import span
    mock_parent = MagicMock()
    mock_child = MagicMock()
    mock_parent.start_child.return_value.__enter__.return_value = mock_child
    with patch("sentry_sdk.get_current_span", return_value=mock_parent):
        with span("retrieval.rerank", "onnx", k=3) as s:
            assert s is not None
    mock_parent.start_child.assert_called_with(op="retrieval.rerank", description="onnx")
    mock_child.set_tag.assert_any_call("k", 3)


def test_breadcrumb_calls_sentry_sdk():
    from core.observability.span_helpers import breadcrumb
    with patch("sentry_sdk.add_breadcrumb") as mock_add:
        breadcrumb("vector fan-out complete", category="retrieval", data={"n": 5})
    mock_add.assert_called_once_with(
        message="vector fan-out complete",
        category="retrieval",
        data={"n": 5},
    )


def test_span_propagates_exceptions_in_noop_path():
    from core.observability.span_helpers import span
    with patch("sentry_sdk.get_current_span", return_value=None):
        with pytest.raises(ValueError, match="boom"):
            with span("retrieval.x", "err"):
                raise ValueError("boom")


def test_span_propagates_exceptions_in_active_path():
    from core.observability.span_helpers import span
    mock_parent = MagicMock()
    mock_child = MagicMock()
    mock_parent.start_child.return_value.__enter__.return_value = mock_child
    with patch("sentry_sdk.get_current_span", return_value=mock_parent):
        with pytest.raises(ValueError, match="boom"):
            with span("retrieval.x", "err"):
                raise ValueError("boom")
    # Sentry's Span.__exit__ handles status/exception; we just verify it was called.
    mock_parent.start_child.return_value.__exit__.assert_called_once()
