# src/mcp/tests/test_request_id_filter.py
import asyncio
import logging

import pytest

from app.observability.request_id_filter import RequestIdFilter
from core.utils.tracing import request_id_var


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_filter_injects_default_when_no_context():
    """When no contextvar is set, the filter emits '-' so the format placeholder resolves."""
    token = request_id_var.set("")
    try:
        rec = _make_record()
        RequestIdFilter().filter(rec)
        assert rec.request_id == "-"
    finally:
        request_id_var.reset(token)


def test_filter_injects_current_context():
    """Filter picks up whatever request-id is active in the contextvar."""
    token = request_id_var.set("req-42")
    try:
        rec = _make_record()
        RequestIdFilter().filter(rec)
        assert rec.request_id == "req-42"
    finally:
        request_id_var.reset(token)


@pytest.mark.asyncio
async def test_contextvar_isolates_between_concurrent_tasks():
    """Two asyncio.gather tasks must see their own request-id (contextvars are per-task)."""

    async def task(req_id: str) -> str:
        request_id_var.set(req_id)
        await asyncio.sleep(0.01)
        rec = _make_record()
        RequestIdFilter().filter(rec)
        return rec.request_id

    a, b = await asyncio.gather(task("req-A"), task("req-B"))
    assert {a, b} == {"req-A", "req-B"}
