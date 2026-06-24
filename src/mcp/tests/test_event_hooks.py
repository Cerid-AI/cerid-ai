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

    # WK4 --- grew-trigger tests -------------------------------------------------

    def test_grew_trigger_enqueues_debounced_for_existing_entity(self, monkeypatch):
        """_on_entities_added enqueues a DEBOUNCED refresh for an entity that
        already has a summary (existing entity, not just new ones)."""
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        # NX acquired — not already debounced
        mock_redis.set.return_value = True
        mock_enqueue = MagicMock()
        # Simulate the entity already has a summary (get_entity returns non-None
        # with a summary field); the subscriber does NOT call get_entity today,
        # but the trigger must fire unconditionally for entities in the event.
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            wiki_refresh._on_entities_added({
                "artifact_id": "a1",
                "entity_slugs": ["org:existing-entity"],
            })

        # Debounced (force=False) so Redis set was attempted
        mock_redis.set.assert_called_once_with(
            "cerid:wiki:debounce:org:existing-entity", "1", nx=True, ex=300,
        )
        mock_enqueue.assert_called_once()

    def test_grew_trigger_debounce_blocks_storm(self, monkeypatch):
        """When debounce is active, the grew-trigger does NOT enqueue again."""
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # debounce key exists
        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            wiki_refresh._on_entities_added({
                "artifact_id": "a1",
                "entity_slugs": ["org:existing-entity"],
            })

        mock_enqueue.assert_not_called()

    def test_human_edit_protected_entity_skipped_by_grew_trigger(self, monkeypatch):
        """_on_entities_added SKIPS entities whose summary_edited_by=="user" within
        the protection window. The check is done via _human_edit_protected_slugs()."""
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_enqueue = MagicMock()

        # Patch the batch protection guard to return the slug as protected
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch.object(wiki_refresh, "_human_edit_protected_slugs", return_value={"org:human-edited"}),
        ):
            wiki_refresh._on_entities_added({
                "artifact_id": "a1",
                "entity_slugs": ["org:human-edited"],
            })

        mock_enqueue.assert_not_called()

    def test_contradiction_force_overrides_human_edit_protection(self, monkeypatch):
        """contradiction_detected always enqueues force=True even for human-edited entities."""
        from app.processor.subscribers import wiki_refresh

        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=MagicMock()),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
            patch.object(wiki_refresh, "_is_human_edit_protected", return_value=True),
        ):
            wiki_refresh._on_contradiction_detected({"entity_slug": "org:human-edited"})

        # force=True must bypass both debounce AND human-edit protection
        mock_enqueue.assert_called_once()

    def test_tz_naive_summary_updated_at_treated_as_protected(self):
        """A tz-naive ISO timestamp in summary_updated_at (legacy node) is
        assumed UTC and compared correctly — not silently fail-open."""
        from datetime import datetime, timedelta, timezone

        from app.processor.subscribers import wiki_refresh

        # Recent timestamp, no UTC offset (legacy bare ISO format)
        recent_naive_ts = (
            datetime.now(tz=timezone.utc) - timedelta(hours=1)
        ).replace(tzinfo=None).isoformat()  # e.g. "2026-06-24T11:00:00"

        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value.single.return_value = {
            "summary_edited_by": "user",
            "summary_updated_at": recent_naive_ts,
        }

        with patch("app.deps.get_neo4j", return_value=mock_driver):
            result = wiki_refresh._is_human_edit_protected("org:some-entity")

        assert result is True, (
            "A tz-naive recent summary_updated_at must be treated as protected "
            "(assumed UTC), not fail-open as unprotected"
        )


class TestBatchHumanEditProtectedSlugs:
    """WK4: _human_edit_protected_slugs issues ONE query and returns the protected subset."""

    def _make_driver(self, rows: list[dict]) -> MagicMock:
        """Return a mock Neo4j driver whose session.run().data() returns ``rows``."""
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value.data.return_value = rows
        return mock_driver, mock_session

    def test_returns_protected_subset_in_one_query(self):
        """Only slugs within the protection window are returned; one Cypher call total."""
        from datetime import datetime, timedelta, timezone

        from app.processor.subscribers import wiki_refresh

        recent_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat()

        rows = [
            {"canonical_id": "org:recent", "summary_updated_at": recent_ts},
            {"canonical_id": "org:old", "summary_updated_at": old_ts},
        ]
        driver, mock_session = self._make_driver(rows)

        result = wiki_refresh._human_edit_protected_slugs(driver, ["org:recent", "org:old", "org:unrelated"])

        assert result == {"org:recent"}
        # Exactly one Cypher query was issued
        assert mock_session.run.call_count == 1

    def test_tz_naive_timestamp_treated_as_utc(self):
        """A tz-naive ISO timestamp in the batch result is assumed UTC — not fail-open."""
        from datetime import datetime, timedelta, timezone

        from app.processor.subscribers import wiki_refresh

        naive_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat()
        rows = [{"canonical_id": "org:naive", "summary_updated_at": naive_ts}]
        driver, _ = self._make_driver(rows)

        result = wiki_refresh._human_edit_protected_slugs(driver, ["org:naive"])
        assert "org:naive" in result

    def test_unparseable_timestamp_skips_slug(self):
        """A malformed timestamp is skipped (fail-open for that slug — not protected)."""
        from app.processor.subscribers import wiki_refresh

        rows = [{"canonical_id": "org:bad-ts", "summary_updated_at": "not-a-date"}]
        driver, _ = self._make_driver(rows)

        result = wiki_refresh._human_edit_protected_slugs(driver, ["org:bad-ts"])
        assert "org:bad-ts" not in result

    def test_empty_slugs_returns_empty_without_querying(self):
        """No Cypher query is issued when the slug list is empty."""
        from app.processor.subscribers import wiki_refresh

        driver, mock_session = self._make_driver([])
        result = wiki_refresh._human_edit_protected_slugs(driver, [])

        assert result == set()
        mock_session.run.assert_not_called()

    def test_on_entities_added_skips_protected_batch(self, monkeypatch):
        """_on_entities_added calls the batch function once and skips protected slugs."""
        from app.processor.subscribers import wiki_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch.object(
                wiki_refresh,
                "_human_edit_protected_slugs",
                return_value={"org:protected"},
            ) as mock_batch,
        ):
            wiki_refresh._on_entities_added({
                "artifact_id": "a1",
                "entity_slugs": ["org:protected", "org:free"],
            })

        # Batch called exactly once
        mock_batch.assert_called_once()
        # Only org:free was enqueued — org:protected was skipped
        assert mock_enqueue.call_count == 1
        job_payload = mock_enqueue.call_args.kwargs.get("payload") or mock_enqueue.call_args.args[1] if len(mock_enqueue.call_args.args) > 1 else mock_enqueue.call_args.args[0]
        assert "org:protected" not in str(job_payload)


# ---------------------------------------------------------------------------
# constellation_refresh subscriber (living Constellation — recompute on ingest)
# ---------------------------------------------------------------------------


class TestConstellationRefreshSubscriber:
    _PAYLOAD = {"artifact_id": "a1", "entity_slugs": ["org:tesla"], "tenant_id": "default"}

    def test_entities_added_enqueues_umap_job(self):
        from app.processor.subscribers import constellation_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # global debounce acquired
        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            constellation_refresh._on_entities_added(self._PAYLOAD)

        mock_redis.set.assert_called_once_with(
            "cerid:constellation:debounce", "1", nx=True, ex=180,
        )
        mock_enqueue.assert_called_once()
        job = mock_enqueue.call_args.args[0]
        assert job.job_type == "compute_umap_3d"

    def test_debounce_coalesces_bulk_ingest(self):
        from app.processor.subscribers import constellation_refresh

        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # debounce already held
        mock_enqueue = MagicMock()

        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            constellation_refresh._on_entities_added(self._PAYLOAD)

        mock_enqueue.assert_not_called()

    def test_redis_unavailable_fails_open(self):
        from app.processor.subscribers import constellation_refresh

        mock_enqueue = MagicMock()
        with (
            patch("app.deps.get_redis", return_value=None),
            patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue),
        ):
            constellation_refresh._on_entities_added(self._PAYLOAD)

        mock_enqueue.assert_called_once()

    def test_disabled_via_env(self, monkeypatch):
        from app.processor.subscribers import constellation_refresh

        monkeypatch.setenv("CERID_CONSTELLATION_REFRESH_ON_INGEST", "false")
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            constellation_refresh._on_entities_added(self._PAYLOAD)
        mock_enqueue.assert_not_called()

    def test_no_slugs_is_noop(self):
        from app.processor.subscribers import constellation_refresh

        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            constellation_refresh._on_entities_added({"artifact_id": "a1", "entity_slugs": []})
        mock_enqueue.assert_not_called()
