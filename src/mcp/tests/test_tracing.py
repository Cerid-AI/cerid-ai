"""Verify tracing contextvars accessors work from core."""
from core.utils.tracing import get_client_id, get_request_id, request_id_var, tracing_headers


def test_get_request_id_returns_default():
    rid = get_request_id()
    assert isinstance(rid, str)


def test_get_client_id_returns_default():
    cid = get_client_id()
    assert isinstance(cid, str)


def test_tracing_headers_returns_dict():
    headers = tracing_headers()
    assert isinstance(headers, dict)
    assert "X-Request-ID" in headers


def test_set_and_get_request_id():
    token = request_id_var.set("test-123")
    try:
        assert get_request_id() == "test-123"
        headers = tracing_headers()
        assert headers["X-Request-ID"] == "test-123"
    finally:
        request_id_var.reset(token)
