"""Unit tests for lib/kb_cleanup.py. Runs on the host (no live stack needed)."""
import httpx
from lib.kb_cleanup import KbCleanup


def test_track_returns_artifact_id():
    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert cleanup.track({"artifact_id": "abc123"}) == "abc123"
    assert cleanup.ids == ["abc123"]


def test_track_falls_back_to_id_field():
    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert cleanup.track({"id": "xyz789"}) == "xyz789"


def test_track_returns_none_when_no_id():
    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert cleanup.track({}) is None
    assert cleanup.ids == []


def test_delete_all_deletes_tracked_ids():
    deleted = []

    def handler(request: httpx.Request) -> httpx.Response:
        deleted.append(request.url.path)
        return httpx.Response(200)

    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)))
    cleanup.ids = ["id1", "id2"]
    failed = cleanup.delete_all()
    assert failed == []
    assert deleted == ["/admin/artifacts/id1", "/admin/artifacts/id2"]


def test_delete_all_treats_404_as_gone():
    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    cleanup.ids = ["missing"]
    assert cleanup.delete_all() == []


def test_delete_all_retries_once_on_429_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200)

    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)))
    cleanup.ids = ["retry-me"]

    import time
    sleeps = []
    orig_sleep = time.sleep
    time.sleep = lambda s: sleeps.append(s)
    try:
        failed = cleanup.delete_all()
    finally:
        time.sleep = orig_sleep

    assert failed == []
    assert calls == ["/admin/artifacts/retry-me", "/admin/artifacts/retry-me"]
    assert sleeps == [2.0]


def test_delete_all_reports_ids_that_still_fail_after_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)))
    cleanup.ids = ["stuck"]

    import time
    orig_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        failed = cleanup.delete_all()
    finally:
        time.sleep = orig_sleep

    assert failed == ["stuck"]


def test_delete_all_reports_ids_on_non_retryable_error():
    cleanup = KbCleanup(httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    cleanup.ids = ["broken"]
    assert cleanup.delete_all() == ["broken"]


def test_track_ignores_non_dict_payloads():
    cleanup = KbCleanup(httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert cleanup.track(None) is None
    assert cleanup.track([{"id": "a"}]) is None
    assert cleanup.track("x") is None
    assert cleanup.ids == []

