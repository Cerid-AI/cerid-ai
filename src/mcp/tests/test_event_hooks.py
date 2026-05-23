# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for app.processor.event_hooks + wiki_refresh subscriber (Phase K1.2/K1.3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_hooks():
    """Ensure each test starts with a clean event-hooks registry."""
    from app.processor import event_hooks

    event_hooks.clear_for_tests()
    yield
    event_hooks.clear_for_tests()


# ---------------------------------------------------------------------------
# event_hooks pub/sub
# ---------------------------------------------------------------------------


class TestEventHooks:
    def test_subscribe_and_emit(self):
        from app.processor.event_hooks import emit, subscribe

        received: list[dict] = []
        subscribe("test_event", received.append)

        emit("test_event", {"key": "value"})
        assert received == [{"key": "value"}]

    def test_subscribe_is_idempotent(self):
        from app.processor.event_hooks import emit, subscribe

        received: list[dict] = []
        fn = received.append
        subscribe("test_event", fn)
        subscribe("test_event", fn)  # second register should be a no-op
        emit("test_event", {"key": "value"})
        assert len(received) == 1

    def test_unsubscribe(self):
        from app.processor.event_hooks import emit, subscribe, unsubscribe

        received: list[dict] = []
        fn = received.append
        subscribe("test_event", fn)
        unsubscribe("test_event", fn)
        emit("test_event", {"key": "value"})
        assert received == []

    def test_no_subscribers_is_silent(self):
        from app.processor.event_hooks import emit

        # Should not raise
        emit("nobody_listens", {})

    def test_subscriber_failure_isolates(self):
        """A broken subscriber must not break the chain."""
        from app.processor.event_hooks import emit, subscribe

        received: list[dict] = []

        def broken(_):
            raise ValueError("boom")

        subscribe("test_event", broken)
        subscribe("test_event", received.append)

        emit("test_event", {"key": "value"})
        assert received == [{"key": "value"}]  # second sub still fires


# ---------------------------------------------------------------------------
# wiki_refresh subscriber
# ---------------------------------------------------------------------------


class TestWikiRefreshSubscriber:
    def test_enqueue_refresh_acquires_debounce(self, monkeypatch):
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # NX acquired

        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            result = wiki_refresh.enqueue_refresh("org:tesla")

        assert result is True
        mock_redis.set.assert_called_once_with(
            "cerid:wiki:debounce:org:tesla", "1", nx=True, ex=300,
        )
        mock_enqueue.assert_called_once()

    def test_enqueue_refresh_debounce_blocks_second_call(self, monkeypatch):
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # NX failed — key exists
        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            result = wiki_refresh.enqueue_refresh("org:tesla")

        assert result is False
        mock_enqueue.assert_not_called()

    def test_force_bypasses_debounce(self, monkeypatch):
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # debounce active
        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            result = wiki_refresh.enqueue_refresh("org:tesla", force=True)

        assert result is True
        # No debounce check on force path
        mock_redis.set.assert_not_called()
        mock_enqueue.assert_called_once()

    def test_redis_unavailable_fails_open(self, monkeypatch):
        """When Redis is down, we still enqueue (fail-open on the debounce)."""
        from app.processor.subscribers import wiki_refresh

        mock_enqueue = MagicMock()
        with (
            patch("app.deps.get_redis", return_value=None),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            result = wiki_refresh.enqueue_refresh("org:tesla")

        assert result is True
        mock_enqueue.assert_called_once()

    def test_disabled_via_env(self, monkeypatch):
        from app.processor.subscribers import wiki_refresh

        monkeypatch.setenv("CERID_WIKI_REFRESH_ON_INGEST", "false")
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            wiki_refresh._on_entities_added({
                "artifact_id": "a1",
                "entity_slugs": ["org:tesla"],
                "tenant_id": "default",
            })
        mock_enqueue.assert_not_called()

    def test_empty_slug_returns_false(self):
        from app.processor.subscribers import wiki_refresh

        assert wiki_refresh.enqueue_refresh("") is False
