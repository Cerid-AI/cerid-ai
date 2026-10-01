# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The preservation ``cleanup_ids`` teardown deletes what tests created, and says so when it cannot.

It used to send no ``X-API-Key`` and DELETE ``/admin/kb/artifact/{id}``, a route
that does not exist, with every failure suppressed — so on an auth-on stack every
ingest preservation test leaked its artifact into the live KB without a word.

These run the real ``tests/integration/conftest.py`` in a pytester session with
``httpx`` replaced by a recorder, so the fixture's own teardown is what is
measured; nothing here reaches a stack.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).resolve().parent / "integration" / "conftest.py"
BASE = "http://stack.test:8888"
API_KEY = "placeholder"  # pragma: allowlist secret

TEST_MODULE = """
def test_creates(cleanup_ids):
    cleanup_ids.extend(IDS)
"""


class _Recorder:
    def __init__(self, statuses: dict[str, int | Exception]):
        self.statuses = statuses
        self.deletes: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))

    def delete(self, url, headers=None, **kwargs):
        self.deletes.append((url, dict(headers or {})))
        outcome = next((v for k, v in self.statuses.items() if url.endswith(k)), 200)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=httpx.Request("DELETE", url))


def _run(pytester, monkeypatch, ids, statuses=None):
    recorder = _Recorder(statuses or {})
    monkeypatch.setattr(httpx, "get", recorder.get)
    monkeypatch.setattr(httpx, "delete", recorder.delete)
    monkeypatch.setenv("CERID_PRESERVATION_MCP", BASE)
    monkeypatch.setenv("CERID_API_KEY", API_KEY)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(f"IDS = {ids!r}\n" + TEST_MODULE)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    return recorder, result


def test_deletes_each_kind_on_its_real_route_with_the_client_headers(pytester, monkeypatch):
    recorder, result = _run(
        pytester, monkeypatch, [("artifact", "art-1"), ("conversation", "conv-1")]
    )
    result.assert_outcomes(passed=1)
    assert [url for url, _ in recorder.deletes] == [
        f"{BASE}/admin/artifacts/art-1",
        f"{BASE}/user-state/conversations/conv-1",
    ]
    for _, headers in recorder.deletes:
        assert headers.get("X-API-Key") == API_KEY
        assert headers.get("X-Client-ID", "").startswith("preservation-")
    assert "preservation_cleanup_leak" not in result.stdout.str()


@pytest.mark.parametrize("status", [401, 403, 500])
def test_a_refused_delete_is_reported_by_id_and_does_not_fail_the_test(
    pytester, monkeypatch, status
):
    _, result = _run(
        pytester,
        monkeypatch,
        [("artifact", "art-leaked"), ("conversation", "conv-ok")],
        {"/art-leaked": status},
    )
    result.assert_outcomes(passed=1)
    out = result.stdout.str()
    assert "preservation_cleanup_leak" in out
    assert "art-leaked" in out
    assert str(status) in out
    assert "conv-ok" not in out


def test_already_deleted_is_not_a_leak(pytester, monkeypatch):
    _, result = _run(pytester, monkeypatch, [("artifact", "art-gone")], {"/art-gone": 404})
    result.assert_outcomes(passed=1)
    assert "preservation_cleanup_leak" not in result.stdout.str()


def test_an_unreachable_stack_is_reported_by_id(pytester, monkeypatch):
    _, result = _run(
        pytester,
        monkeypatch,
        [("conversation", "conv-down")],
        {"/conv-down": httpx.ConnectError("connection refused")},
    )
    result.assert_outcomes(passed=1)
    out = result.stdout.str()
    assert "preservation_cleanup_leak" in out
    assert "conv-down" in out


def test_an_unknown_kind_is_reported_not_dropped(pytester, monkeypatch):
    recorder, result = _run(pytester, monkeypatch, [("report", "rep-1")])
    result.assert_outcomes(passed=1)
    assert recorder.deletes == []
    out = result.stdout.str()
    assert "preservation_cleanup_leak" in out
    assert "rep-1" in out
