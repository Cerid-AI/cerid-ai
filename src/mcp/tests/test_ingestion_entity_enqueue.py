# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase K1.1 — verify ingestion enqueues EntityExtractionJob post-commit."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestEntityExtractionEnqueue:
    def test_enqueue_fires_when_flag_default(self, monkeypatch):
        from app.services import ingestion

        monkeypatch.delenv("CERID_ENTITY_EXTRACTION_ENABLED", raising=False)
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job_if_absent", mock_enqueue):
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")
        mock_enqueue.assert_called_once()
        payload = mock_enqueue.call_args.kwargs["payload"]
        assert payload["artifact_id"] == "art-1"
        assert payload["tenant_id"] == "default"

    def test_enqueue_skipped_when_disabled(self, monkeypatch):
        from app.services import ingestion

        monkeypatch.setenv("CERID_ENTITY_EXTRACTION_ENABLED", "false")
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job_if_absent", mock_enqueue):
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")
        mock_enqueue.assert_not_called()

    def test_enqueue_failure_swallowed(self, monkeypatch):
        """A broken queue must not break ingestion."""
        from app.services import ingestion

        monkeypatch.setenv("CERID_ENTITY_EXTRACTION_ENABLED", "true")
        with patch(
            "app.db.redis.processor_queue.enqueue_job_if_absent",
            side_effect=RuntimeError("redis down"),
        ):
            # Should not raise
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")

    def test_enqueue_collapses_onto_pending_duplicate(self, monkeypatch):
        """A pending/running job for the same artifact_id must not stack a
        second one — enqueue_if_absent's dedupe, exercised end-to-end
        against find_active_job_id rather than a bare mock."""
        from app.services import ingestion

        monkeypatch.delenv("CERID_ENTITY_EXTRACTION_ENABLED", raising=False)
        with (
            # enqueue_job_if_absent() falls back to app.deps.get_redis() when
            # no redis_client is passed — stub it so the dedupe path under
            # test never opens a real connection.
            patch("app.deps.get_redis", return_value=MagicMock()),
            patch(
                "app.db.redis.processor_queue.find_active_job_id",
                return_value="already-queued-job-id",
            ),
            patch("app.db.redis.processor_queue.enqueue_job") as mock_enqueue_job,
        ):
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")
        # The dedupe check found an active job, so the actual enqueue
        # (which would stack a duplicate) must never run.
        mock_enqueue_job.assert_not_called()
