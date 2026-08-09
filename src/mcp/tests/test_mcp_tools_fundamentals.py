# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for Phase 1.5 fundamental tools.

Smoke + error-path coverage per tool. Each handler is called directly
(not through ``execute_registered_tool``) with mocked deps so the test
doesn't need a live neo4j/chroma stack. Routing through the registry
is covered separately in ``test_tool_registry.py``.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.mcp_tools.fundamentals import (
    pkb_artifact_delete,
    pkb_artifact_get,
    pkb_recategorize_bulk,
    pkb_search_filtered,
)
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
)

# -------------------------------------------------------------- pkb_artifact_get


@pytest.mark.asyncio
async def test_artifact_get_returns_artifact_and_chunks():
    fake_driver = MagicMock()
    fake_chroma_coll = MagicMock()
    fake_chroma_coll.get.return_value = {
        "ids": ["c1", "c2"],
        "documents": ["first chunk", "second chunk"],
        "metadatas": [{"pos": 0}, {"pos": 1}],
    }
    fake_chroma = MagicMock()
    fake_chroma.get_collection = MagicMock(return_value=fake_chroma_coll)

    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch("app.mcp_tools.fundamentals.get_chroma", return_value=fake_chroma),
        patch(
            "app.mcp_tools.fundamentals.graph.get_artifact",
            return_value={
                "id": "art-1",
                "filename": "f.md",
                "domain": "coding",
                "chunk_ids": '["c1", "c2"]',
                "chunk_count": 2,
            },
        ),
    ):
        out = await pkb_artifact_get(artifact_id="art-1")

    assert out["artifact"]["id"] == "art-1"
    assert out["chunk_count"] == 2
    assert len(out["chunks"]) == 2
    assert out["chunks"][0]["text"] == "first chunk"
    # Verified the proxy-safe kwarg call shape we fixed in Phase 0
    fake_chroma.get_collection.assert_called_once()
    assert fake_chroma.get_collection.call_args.args == ()
    assert "name" in fake_chroma.get_collection.call_args.kwargs


@pytest.mark.asyncio
async def test_artifact_get_skip_chunks_when_flag_false():
    fake_driver = MagicMock()
    fake_chroma = MagicMock()  # Should NOT be touched.

    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch("app.mcp_tools.fundamentals.get_chroma", return_value=fake_chroma),
        patch(
            "app.mcp_tools.fundamentals.graph.get_artifact",
            return_value={
                "id": "art-2", "filename": "x", "domain": "coding",
                "chunk_ids": '["c1"]', "chunk_count": 1,
            },
        ),
    ):
        out = await pkb_artifact_get(artifact_id="art-2", include_chunks=False)

    assert out["chunks"] == []
    fake_chroma.get_collection.assert_not_called()


@pytest.mark.asyncio
async def test_artifact_get_decrypts_summary_in_chunk_metadata():
    """Task 2.6a: with CERID_ENCRYPTION_KEY set, a chunk's raw Chroma
    ``summary`` metadata (``enc:v1:...``) must be decrypted before it is
    returned to the MCP caller — otherwise the ciphertext leaks verbatim.
    """
    import os

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        pytest.skip("cryptography not installed")
    from utils.encryption import encrypt_field, reset_encryptor

    key = Fernet.generate_key().decode()
    reset_encryptor()
    original_summary = "a private summary"

    try:
        with patch.dict(os.environ, {"CERID_ENCRYPTION_KEY": key}):
            encrypted_summary = encrypt_field(original_summary)
            assert encrypted_summary.startswith("enc:v1:")

            fake_driver = MagicMock()
            fake_chroma_coll = MagicMock()
            fake_chroma_coll.get.return_value = {
                "ids": ["c1"],
                "documents": ["chunk text"],
                "metadatas": [{"pos": 0, "summary": encrypted_summary}],
            }
            fake_chroma = MagicMock()
            fake_chroma.get_collection = MagicMock(return_value=fake_chroma_coll)

            with (
                patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
                patch("app.mcp_tools.fundamentals.get_chroma", return_value=fake_chroma),
                patch(
                    "app.mcp_tools.fundamentals.graph.get_artifact",
                    return_value={
                        "id": "art-enc",
                        "filename": "f.md",
                        "domain": "coding",
                        "chunk_ids": '["c1"]',
                        "chunk_count": 1,
                    },
                ),
            ):
                out = await pkb_artifact_get(artifact_id="art-enc")
    finally:
        reset_encryptor()

    assert out["chunks"][0]["metadata"]["summary"] == original_summary


@pytest.mark.asyncio
async def test_artifact_get_missing_raises_resource_not_found():
    fake_driver = MagicMock()
    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch(
            "app.mcp_tools.fundamentals.graph.get_artifact",
            return_value=None,
        ),
    ):
        with pytest.raises(ResourceNotFoundError):
            await pkb_artifact_get(artifact_id="nope")


# ------------------------------------------------------- pkb_artifact_delete


@pytest.mark.asyncio
async def test_artifact_delete_soft_routes_through_hide_content():
    fake_driver = MagicMock()

    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch(
            "app.mcp_tools.fundamentals.graph.get_artifact",
            return_value={
                "id": "art-soft",
                "filename": "f.md",
                "domain": "coding",
                "chunk_count": 3,
            },
        ),
        patch(
            "app.services.content_lifecycle.hide_content", return_value=True
        ) as mock_hide,
    ):
        out = await pkb_artifact_delete(artifact_id="art-soft", hard=False)

    assert out["deleted"] is True
    assert out["mode"] == "soft"
    assert out["chunks_affected"] == 3
    assert out["filename"] == "f.md"
    assert out["domain"] == "coding"
    # Archived write is routed through the content-lifecycle coordinator, not an
    # inline cypher — it centralizes the flag + the query-cache bust.
    mock_hide.assert_called_once_with("art-soft", neo4j=fake_driver)


@pytest.mark.asyncio
async def test_artifact_delete_hard_routes_through_remove_content():
    from app.services.content_lifecycle import RemovalResult

    fake_driver = MagicMock()
    result = RemovalResult(
        found=True,
        artifact_id="art-hard",
        domain="coding",
        chunk_ids=["c1", "c2"],
        removed={"chroma": 2, "bm25": 2, "sparse": 0},
    )

    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch(
            "app.mcp_tools.fundamentals.graph.get_artifact",
            return_value={
                "id": "art-hard",
                "filename": "f.md",
                "domain": "coding",
                "chunk_count": 2,
            },
        ),
        patch(
            "app.services.content_lifecycle.remove_content", return_value=result
        ) as mock_remove,
    ):
        out = await pkb_artifact_delete(artifact_id="art-hard", hard=True)

    assert out["mode"] == "hard"
    assert out["chunks_affected"] == 2
    assert out["filename"] == "f.md"
    # Hard delete fans across every store via the coordinator (BM25/SPLADE no
    # longer orphaned) instead of the old Chroma-only inline delete.
    mock_remove.assert_called_once_with("art-hard", neo4j=fake_driver)


@pytest.mark.asyncio
async def test_artifact_delete_missing_raises_not_found():
    fake_session = MagicMock()
    fake_result = MagicMock()
    fake_result.single.return_value = None
    fake_session.run.return_value = fake_result
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__ = MagicMock(return_value=fake_session)
    fake_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver):
        with pytest.raises(ResourceNotFoundError):
            await pkb_artifact_delete(artifact_id="nope", hard=False)


# ----------------------------------------------------- pkb_search_filtered


@pytest.mark.asyncio
async def test_search_filtered_empty_pre_filter_returns_no_results():
    """When pre-filter narrows to nothing, skip the retrieval call."""
    fake_driver = MagicMock()

    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch("app.mcp_tools.fundamentals.get_chroma"),
        patch("app.mcp_tools.fundamentals.get_redis"),
        patch(
            "app.mcp_tools.fundamentals.graph.list_artifacts",
            return_value=[],  # pre-filter narrows to zero
        ),
    ):
        out = await pkb_search_filtered(
            query="x",
            tag="nonexistent",
        )

    assert out["total_results"] == 0
    assert out["results"] == []
    assert out["filter_applied"]["tag"] == "nonexistent"


# ----------------------------------------------------- pkb_recategorize_bulk


@pytest.mark.asyncio
async def test_recategorize_bulk_refuses_empty_filter():
    with pytest.raises(InvalidParamsError, match="empty filter"):
        await pkb_recategorize_bulk(
            filter={},
            new_domain="coding",
        )


@pytest.mark.asyncio
async def test_recategorize_bulk_refuses_invalid_domain():
    with pytest.raises(InvalidParamsError, match="Invalid new_domain"):
        await pkb_recategorize_bulk(
            filter={"tag": "x"},
            new_domain="not-a-real-domain",
        )


@pytest.mark.asyncio
async def test_recategorize_bulk_refuses_excessive_max_count():
    with pytest.raises(InvalidParamsError, match="hard cap"):
        await pkb_recategorize_bulk(
            filter={"tag": "x"},
            new_domain="coding",
            max_count=1001,
        )


@pytest.mark.asyncio
async def test_recategorize_bulk_moves_and_reports_failures():
    fake_driver = MagicMock()
    candidates = [
        {"id": "a1", "domain": "general", "ingested_at": "2026-05-01"},
        {"id": "a2", "domain": "general", "ingested_at": "2026-05-02"},
        {"id": "a3", "domain": "coding",  "ingested_at": "2026-05-03"},  # already in target
    ]
    call_log: list[str] = []

    def fake_recategorize(*, artifact_id, new_domain, tags):
        call_log.append(artifact_id)
        if artifact_id == "a2":
            raise RuntimeError("simulated failure")
        return {"status": "success"}

    with (
        patch("app.mcp_tools.fundamentals.get_neo4j", return_value=fake_driver),
        patch(
            "app.mcp_tools.fundamentals.graph.list_artifacts",
            return_value=candidates,
        ),
        patch(
            "app.mcp_tools.fundamentals.recategorize",
            side_effect=fake_recategorize,
        ),
    ):
        out = await pkb_recategorize_bulk(
            filter={"tag": "x"},
            new_domain="coding",
            max_count=10,
        )

    assert out["matched"] == 3
    assert out["moved"] == 1  # a1
    assert out["failed"] == 2  # a2 (RuntimeError) + a3 (already in target)
    failure_ids = {f["artifact_id"] for f in out["failures"]}
    assert failure_ids == {"a2", "a3"}
