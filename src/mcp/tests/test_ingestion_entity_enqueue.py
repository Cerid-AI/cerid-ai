# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase K1.1 — verify ingestion enqueues EntityExtractionJob post-commit."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestEntityExtractionEnqueue:
    def test_enqueue_fires_when_flag_default(self, monkeypatch):
        from app.services import ingestion

        monkeypatch.delenv("CERID_ENTITY_EXTRACTION_ENABLED", raising=False)
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")
        mock_enqueue.assert_called_once()
        payload = mock_enqueue.call_args.kwargs["payload"]
        assert payload["artifact_id"] == "art-1"
        assert payload["tenant_id"] == "default"

    def test_enqueue_skipped_when_disabled(self, monkeypatch):
        from app.services import ingestion

        monkeypatch.setenv("CERID_ENTITY_EXTRACTION_ENABLED", "false")
        mock_enqueue = MagicMock()
        with patch("app.db.redis.processor_queue.enqueue_job", mock_enqueue):
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")
        mock_enqueue.assert_not_called()

    def test_enqueue_failure_swallowed(self, monkeypatch):
        """A broken queue must not break ingestion."""
        from app.services import ingestion

        monkeypatch.setenv("CERID_ENTITY_EXTRACTION_ENABLED", "true")
        with patch("app.db.redis.processor_queue.enqueue_job", side_effect=RuntimeError("redis down")):
            # Should not raise
            ingestion._enqueue_entity_extraction_if_enabled(artifact_id="art-1")
