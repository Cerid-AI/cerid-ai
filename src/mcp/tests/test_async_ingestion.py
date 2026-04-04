# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for async ingestion and batch support.

Covers:
- asyncio.to_thread() wrapping of parse_file / ingest_content
- ingest_batch() service function and POST /ingest_batch endpoint
- Watcher batch queue integration
"""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_redis_globally():
    """Prevent Redis connections caused by cross-test module pollution."""
    with patch("deps._redis", MagicMock()), \
         patch("deps.get_redis", return_value=MagicMock()):
        yield


def _ensure_real_router():
    """Ensure the real routers.ingestion module is loaded.

    test_memory.py may inject a stub into sys.modules that lacks the batch
    endpoint. This helper detects the stub and force-loads the real module.
    """
    ri = sys.modules.get("routers.ingestion")
    if ri is not None and not hasattr(ri, "ingest_batch_endpoint"):
        del sys.modules["routers.ingestion"]
        import routers.ingestion  # noqa: F811
        importlib.reload(routers.ingestion)
    return importlib.import_module("routers.ingestion")

# ---------------------------------------------------------------------------
# 4A: Async file parsing — asyncio.to_thread() wrapping
# ---------------------------------------------------------------------------

class TestAsyncFileParsing:
    """Verify that ingest_file() uses asyncio.to_thread() for blocking calls."""

    @patch("services.ingestion.asyncio")
    @patch("services.ingestion.ai_categorize", new_callable=AsyncMock)
    @patch("services.ingestion.extract_metadata")
    @patch("services.ingestion.validate_file_path")
    def test_parse_file_runs_in_thread(
        self, mock_validate, mock_meta, mock_ai_cat, mock_asyncio
    ):
        """parse_file() should be called via asyncio.to_thread()."""
        mock_validate.return_value = MagicMock()
        mock_meta.return_value = {"filename": "test.txt"}
        mock_ai_cat.return_value = {"suggested_domain": "coding", "keywords": [], "summary": ""}

        # Make asyncio.to_thread return expected values
        # First call → parse_file result, second call → ingest_content result
        mock_asyncio.to_thread = AsyncMock(side_effect=[
            {"text": "parsed content", "file_type": "txt"},  # parse_file
            {"status": "success", "domain": "coding", "chunks": 1},  # ingest_content
        ])

        from services.ingestion import ingest_file

        asyncio.get_event_loop().run_until_complete(
            ingest_file("/archive/test.txt", domain="coding")
        )

        # Verify to_thread was called twice (parse_file + ingest_content)
        assert mock_asyncio.to_thread.call_count == 2

        # First call should be the virtiofs-retry-wrapped parse_file
        first_call = mock_asyncio.to_thread.call_args_list[0]
        target_fn = first_call.args[0]
        # Accept either the raw parse_file or the @virtiofs_retry wrapper
        from parsers import parse_file
        assert target_fn is parse_file or getattr(target_fn, "__wrapped__", None) is parse_file or target_fn.__name__ == "_parse_with_retry"

    @patch("services.ingestion.asyncio")
    @patch("services.ingestion.ai_categorize", new_callable=AsyncMock)
    @patch("services.ingestion.extract_metadata")
    @patch("services.ingestion.validate_file_path")
    def test_ingest_content_runs_in_thread(
        self, mock_validate, mock_meta, mock_ai_cat, mock_asyncio
    ):
        """ingest_content() should be called via asyncio.to_thread()."""
        mock_validate.return_value = MagicMock()
        mock_meta.return_value = {"filename": "test.txt", "domain": "coding"}
        mock_ai_cat.return_value = {"suggested_domain": "coding", "keywords": [], "summary": ""}

        mock_asyncio.to_thread = AsyncMock(side_effect=[
            {"text": "content", "file_type": "txt"},
            {"status": "success", "domain": "coding", "chunks": 2},
        ])

        from services.ingestion import ingest_file

        asyncio.get_event_loop().run_until_complete(
            ingest_file("/archive/test.txt", domain="coding")
        )

        # Second to_thread call should be ingest_content
        second_call = mock_asyncio.to_thread.call_args_list[1]
        from services.ingestion import ingest_content
        assert second_call.args[0] is ingest_content

    @patch("services.ingestion.asyncio")
    @patch("services.ingestion.ai_categorize", new_callable=AsyncMock)
    @patch("services.ingestion.extract_metadata")
    @patch("services.ingestion.validate_file_path")
    def test_parse_output_preserved(
        self, mock_validate, mock_meta, mock_ai_cat, mock_asyncio
    ):
        """asyncio.to_thread() wrapping should not alter parse_file output."""
        mock_validate.return_value = MagicMock()
        mock_meta.return_value = {"filename": "doc.pdf", "domain": "general"}
        mock_ai_cat.return_value = {"suggested_domain": "general", "keywords": [], "summary": ""}

        parsed = {"text": "PDF content here", "file_type": "pdf", "page_count": 5}
        mock_asyncio.to_thread = AsyncMock(side_effect=[
            parsed,
            {"status": "success", "domain": "general", "chunks": 3},
        ])

        from services.ingestion import ingest_file

        result = asyncio.get_event_loop().run_until_complete(
            ingest_file("/archive/doc.pdf", domain="general")
        )

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 4B: Batch ingestion — ingest_batch() service function
# ---------------------------------------------------------------------------

class TestIngestBatch:
    """Test the batch ingestion service function."""

    @pytest.mark.asyncio
    async def test_batch_validates_max_items(self):
        """Batch size exceeding BATCH_MAX_ITEMS should raise ValueError."""
        from services.ingestion import ingest_batch

        items = [{"content": f"item {i}"} for i in range(21)]
        with pytest.raises(ValueError, match="exceeds maximum"):
            await ingest_batch(items)

    @pytest.mark.asyncio
    @patch("services.ingestion.ingest_content")
    async def test_batch_content_items(self, mock_ingest):
        """Batch should handle content-based items."""
        mock_ingest.return_value = {"status": "success", "chunks": 1}

        from services.ingestion import ingest_batch

        items = [
            {"content": "first item", "domain": "coding"},
            {"content": "second item", "domain": "general"},
        ]
        result = await ingest_batch(items)

        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_batch_empty_item_returns_error(self):
        """Items without content or file_path should return an error result."""
        from services.ingestion import ingest_batch

        items = [{"domain": "coding"}]  # No content or file_path
        result = await ingest_batch(items)

        assert result["failed"] == 1
        assert result["results"][0]["status"] == "error"

    @pytest.mark.asyncio
    @patch("services.ingestion.ingest_content")
    async def test_batch_individual_failures_dont_block(self, mock_ingest):
        """Individual item failures should not prevent other items from succeeding."""
        mock_ingest.side_effect = [
            {"status": "success", "chunks": 1},
            Exception("DB error"),
            {"status": "success", "chunks": 2},
        ]

        from services.ingestion import ingest_batch

        items = [
            {"content": "good 1", "domain": "coding"},
            {"content": "bad", "domain": "coding"},
            {"content": "good 2", "domain": "coding"},
        ]
        result = await ingest_batch(items)

        assert result["succeeded"] == 2
        assert result["failed"] == 1
        assert result["results"][1]["status"] == "error"

    @pytest.mark.asyncio
    @patch("services.ingestion.ingest_content")
    async def test_batch_duplicate_counted_as_success(self, mock_ingest):
        """Duplicate results should count as 'succeeded'."""
        mock_ingest.return_value = {"status": "duplicate", "duplicate_of": "existing.txt"}

        from services.ingestion import ingest_batch

        items = [{"content": "duplicate content"}]
        result = await ingest_batch(items)

        assert result["succeeded"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    @patch("services.ingestion.ingest_file", new_callable=AsyncMock)
    async def test_batch_file_items(self, mock_ingest_file):
        """Batch should handle file_path-based items."""
        mock_ingest_file.return_value = {"status": "success", "chunks": 3}

        from services.ingestion import ingest_batch

        items = [
            {"file_path": "/archive/test.pdf", "domain": "coding"},
        ]
        result = await ingest_batch(items)

        assert result["succeeded"] == 1
        mock_ingest_file.assert_called_once()


# ---------------------------------------------------------------------------
# 4B: Batch ingestion — POST /ingest_batch endpoint
# ---------------------------------------------------------------------------

class TestBatchEndpoint:
    """Test the batch ingestion REST endpoint."""

    @pytest.mark.asyncio
    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("deps.get_redis", return_value=MagicMock())
    async def test_endpoint_returns_batch_result(self, _mock_redis, _mock_sysmon_redis):
        """POST /ingest_batch should return the batch result."""
        ri = _ensure_real_router()

        with patch.object(ri, "ingest_batch", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = {
                "results": [{"status": "success"}],
                "succeeded": 1,
                "failed": 0,
            }

            req = ri.BatchIngestRequest(items=[
                ri.BatchIngestItem(content="test content", domain="coding"),
            ])
            result = await ri.ingest_batch_endpoint(req)

        assert result["succeeded"] == 1
        mock_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_endpoint_rejects_both_content_and_filepath(self):
        """Items with both content and file_path should be rejected."""
        from fastapi import HTTPException

        ri = _ensure_real_router()

        req = ri.BatchIngestRequest(items=[
            ri.BatchIngestItem(content="text", file_path="/archive/test.txt"),
        ])

        with pytest.raises(HTTPException) as exc_info:
            await ri.ingest_batch_endpoint(req)

        assert exc_info.value.status_code == 400
        assert "not both" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_endpoint_rejects_empty_items(self):
        """Items with neither content nor file_path should be rejected."""
        from fastapi import HTTPException

        ri = _ensure_real_router()

        req = ri.BatchIngestRequest(items=[
            ri.BatchIngestItem(domain="coding"),  # no content or file_path
        ])

        with pytest.raises(HTTPException) as exc_info:
            await ri.ingest_batch_endpoint(req)

        assert exc_info.value.status_code == 400
        assert "must have" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 4C: Watcher batch queue integration
# ---------------------------------------------------------------------------

class TestWatcherBatchQueue:
    """Test the watcher's batch queue accumulation and flushing."""

    def test_queue_for_batch_adds_to_pending(self):
        """_queue_for_batch should add files to _pending_queue."""
        import scripts.watch_ingest as watcher

        # Reset state
        watcher._pending_queue.clear()
        watcher._recent.clear()

        with patch.object(watcher, "_should_process", return_value=True), \
             patch.object(watcher, "_wait_for_stable", return_value=True):
            watcher._queue_for_batch("/archive/test.txt", "smart")

        assert len(watcher._pending_queue) == 1
        assert watcher._pending_queue[0] == ("/archive/test.txt", "smart")

    def test_queue_deduplicates(self):
        """_queue_for_batch should not add duplicate paths."""
        import scripts.watch_ingest as watcher

        watcher._pending_queue.clear()
        watcher._recent.clear()

        with patch.object(watcher, "_should_process", return_value=True), \
             patch.object(watcher, "_wait_for_stable", return_value=True):
            watcher._queue_for_batch("/archive/test.txt", "smart")
            watcher._queue_for_batch("/archive/test.txt", "smart")

        assert len(watcher._pending_queue) == 1

    def test_flush_batch_sends_post(self):
        """_flush_batch should send a POST to /ingest_batch."""
        import scripts.watch_ingest as watcher

        watcher._pending_queue.clear()
        watcher._pending_queue.append(("/host/archive/test.txt", "smart"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"status": "success", "domain": "coding", "chunks": 1}],
            "succeeded": 1,
            "failed": 0,
        }

        with patch("scripts.watch_ingest.requests.post", return_value=mock_resp) as mock_post:
            watcher._flush_batch()

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/ingest_batch" in call_kwargs.args[0]
        assert "items" in call_kwargs.kwargs["json"]

    def test_flush_batch_clears_queue(self):
        """_flush_batch should clear _pending_queue after sending."""
        import scripts.watch_ingest as watcher

        watcher._pending_queue.clear()
        watcher._pending_queue.append(("/host/archive/a.txt", "smart"))
        watcher._pending_queue.append(("/host/archive/b.txt", "smart"))

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"status": "success"}, {"status": "success"}],
            "succeeded": 2,
            "failed": 0,
        }

        with patch("scripts.watch_ingest.requests.post", return_value=mock_resp):
            watcher._flush_batch()

        assert len(watcher._pending_queue) == 0

    def test_flush_batch_retries_on_http_error(self):
        """Failed batch requests should schedule retries for each item."""
        import scripts.watch_ingest as watcher

        watcher._pending_queue.clear()
        watcher._retry_queue.clear()
        watcher._pending_queue.append(("/host/archive/fail.txt", "smart"))

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("scripts.watch_ingest.requests.post", return_value=mock_resp):
            watcher._flush_batch()

        # Should have scheduled a retry
        assert len(watcher._retry_queue) == 1
        assert watcher._retry_queue[0][0] == "/host/archive/fail.txt"

        # Clean up
        watcher._retry_queue.clear()

    def test_flush_batch_empty_queue_noop(self):
        """_flush_batch with empty queue should be a no-op."""
        import scripts.watch_ingest as watcher

        watcher._pending_queue.clear()

        with patch("scripts.watch_ingest.requests.post") as mock_post:
            watcher._flush_batch()

        mock_post.assert_not_called()

    def test_queue_flushes_at_batch_max(self):
        """Queue should auto-flush when BATCH_MAX items are reached."""
        import scripts.watch_ingest as watcher

        watcher._pending_queue.clear()
        watcher._recent.clear()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"status": "success"}] * watcher.BATCH_MAX,
            "succeeded": watcher.BATCH_MAX,
            "failed": 0,
        }

        with patch.object(watcher, "_should_process", return_value=True), \
             patch.object(watcher, "_wait_for_stable", return_value=True), \
             patch("scripts.watch_ingest.requests.post", return_value=mock_resp) as mock_post:

            for i in range(watcher.BATCH_MAX):
                watcher._queue_for_batch(f"/archive/file_{i}.txt", "smart")

        # Should have auto-flushed
        mock_post.assert_called_once()
        assert len(watcher._pending_queue) == 0

        # Clean up
        watcher._retry_queue.clear()
