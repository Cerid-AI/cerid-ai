# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for :mod:`app.services.external_ingest`.

Covers:
* ``apply_mappings`` with a flat payload
* ``apply_mappings`` with an array fan-out path (``highlights[].text``)
* ``apply_mappings`` with a nested dotted path (``meta.source.url``)
* ``MappingError`` on a missing required field
* Optional tags path: resolves correctly; absent → empty list
* ``ingest_external`` end-to-end with mocked ``ingest_content`` — verifies counts
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.external_ingest import (
    ExternalIngestRequest,
    FieldMappings,
    MappingError,
    apply_mappings,
    ingest_external,
)

# ---------------------------------------------------------------------------
# apply_mappings — flat payload
# ---------------------------------------------------------------------------


class TestApplyMappingsFlat:
    def test_flat_payload_single_item(self) -> None:
        payload = {
            "text": "Hello world",
            "url": "https://example.com/article",
            "created_at": "2026-05-10T08:00:00Z",
        }
        mappings = FieldMappings(content="text", source_uri="url", ts="created_at")
        items = apply_mappings(payload, mappings, source_type="test")

        assert len(items) == 1
        item = items[0]
        assert item.content == "Hello world"
        assert item.source_uri == "https://example.com/article"
        assert item.ts == "2026-05-10T08:00:00Z"
        assert item.source_type == "test"
        assert item.tags == []

    def test_flat_payload_with_all_optional_fields(self) -> None:
        payload = {
            "text": "Body text",
            "url": "https://example.com",
            "title": "My Article",
            "id": "abc123",
            "labels": ["python", "ai"],
        }
        mappings = FieldMappings(
            content="text",
            source_uri="url",
            title="title",
            id="id",
            tags="labels",
        )
        items = apply_mappings(payload, mappings, source_type="pocket")
        assert len(items) == 1
        item = items[0]
        assert item.title == "My Article"
        assert item.external_id == "abc123"
        assert item.tags == ["python", "ai"]

    def test_source_type_propagated(self) -> None:
        payload = {"body": "content", "link": "https://a.com"}
        mappings = FieldMappings(content="body", source_uri="link")
        items = apply_mappings(payload, mappings, source_type="instapaper")
        assert items[0].source_type == "instapaper"


# ---------------------------------------------------------------------------
# apply_mappings — array fan-out (highlights[].text)
# ---------------------------------------------------------------------------


class TestApplyMappingsArrayFanOut:
    def test_highlights_array_produces_multiple_items(self) -> None:
        payload = {
            "highlights": [
                {"text": "First highlight", "url": "https://book.com/ch1"},
                {"text": "Second highlight", "url": "https://book.com/ch2"},
                {"text": "Third highlight", "url": "https://book.com/ch3"},
            ]
        }
        mappings = FieldMappings(
            content="highlights[].text",
            source_uri="highlights[].url",
        )
        items = apply_mappings(payload, mappings, source_type="readwise")
        assert len(items) == 3
        assert items[0].content == "First highlight"
        assert items[1].source_uri == "https://book.com/ch2"
        assert items[2].content == "Third highlight"

    def test_empty_array_returns_empty_list(self) -> None:
        payload = {"highlights": []}
        mappings = FieldMappings(
            content="highlights[].text",
            source_uri="highlights[].url",
        )
        items = apply_mappings(payload, mappings, source_type="readwise")
        assert items == []

    def test_fanout_length_mismatch_raises_mapping_error(self) -> None:
        payload = {
            "texts": ["a", "b"],
            "urls": ["https://x.com"],
        }
        # texts resolves 2 items, but urls is a plain path → resolves 1 item
        mappings = FieldMappings(content="texts[].", source_uri="urls[].")
        # This exercises the length-mismatch guard indirectly — the actual
        # paths here are malformed but the mismatch guard fires first.
        with pytest.raises(MappingError):
            apply_mappings(payload, mappings)

    def test_array_spread_source_type_on_each_item(self) -> None:
        payload = {
            "notes": [
                {"body": "Note A", "link": "https://n.com/a"},
                {"body": "Note B", "link": "https://n.com/b"},
            ]
        }
        mappings = FieldMappings(content="notes[].body", source_uri="notes[].link")
        items = apply_mappings(payload, mappings, source_type="raindrop")
        assert all(i.source_type == "raindrop" for i in items)


# ---------------------------------------------------------------------------
# apply_mappings — nested dotted path (meta.source.url)
# ---------------------------------------------------------------------------


class TestApplyMappingsNestedPath:
    def test_nested_dotted_path_resolves(self) -> None:
        payload = {
            "content": "Deep text",
            "meta": {
                "source": {
                    "url": "https://nested.example.com/page"
                }
            },
        }
        mappings = FieldMappings(content="content", source_uri="meta.source.url")
        items = apply_mappings(payload, mappings)
        assert len(items) == 1
        assert items[0].source_uri == "https://nested.example.com/page"

    def test_deeply_nested_content(self) -> None:
        payload = {"a": {"b": {"c": "deep content"}}, "url": "https://x.com"}
        mappings = FieldMappings(content="a.b.c", source_uri="url")
        items = apply_mappings(payload, mappings)
        assert items[0].content == "deep content"


# ---------------------------------------------------------------------------
# apply_mappings — MappingError on missing required field
# ---------------------------------------------------------------------------


class TestApplyMappingsMappingError:
    def test_missing_content_path_raises(self) -> None:
        payload = {"url": "https://example.com"}
        mappings = FieldMappings(content="nonexistent_field", source_uri="url")
        with pytest.raises(MappingError, match="nonexistent_field"):
            apply_mappings(payload, mappings)

    def test_missing_source_uri_path_raises(self) -> None:
        payload = {"text": "Some content"}
        mappings = FieldMappings(content="text", source_uri="missing_url")
        with pytest.raises(MappingError, match="missing_url"):
            apply_mappings(payload, mappings)

    def test_array_element_missing_sub_path_raises(self) -> None:
        payload = {
            "items": [
                {"text": "ok", "url": "https://ok.com"},
                {"text": "missing url field here"},  # no 'url'
            ]
        }
        mappings = FieldMappings(content="items[].text", source_uri="items[].url")
        with pytest.raises(MappingError):
            apply_mappings(payload, mappings)

    def test_non_dict_intermediate_raises(self) -> None:
        payload = {"meta": "string_not_dict"}
        mappings = FieldMappings(content="meta.url", source_uri="some_url")
        with pytest.raises(MappingError):
            apply_mappings(payload, mappings)


# ---------------------------------------------------------------------------
# apply_mappings — optional tags path
# ---------------------------------------------------------------------------


class TestApplyMappingsTags:
    def test_tags_present_resolves_correctly(self) -> None:
        payload = {
            "text": "Tagged content",
            "url": "https://example.com",
            "tags": ["python", "ml", "rag"],
        }
        mappings = FieldMappings(content="text", source_uri="url", tags="tags")
        items = apply_mappings(payload, mappings)
        assert items[0].tags == ["python", "ml", "rag"]

    def test_tags_absent_returns_empty_list(self) -> None:
        payload = {"text": "No tags here", "url": "https://example.com"}
        mappings = FieldMappings(content="text", source_uri="url", tags="nonexistent_tags")
        items = apply_mappings(payload, mappings)
        assert items[0].tags == []

    def test_tags_path_none_returns_empty_list(self) -> None:
        payload = {"text": "Content", "url": "https://example.com"}
        mappings = FieldMappings(content="text", source_uri="url")
        # tags defaults to None
        items = apply_mappings(payload, mappings)
        assert items[0].tags == []

    def test_tags_non_list_value_returns_empty_list(self) -> None:
        payload = {"text": "Content", "url": "https://x.com", "labels": "single-string"}
        mappings = FieldMappings(content="text", source_uri="url", tags="labels")
        items = apply_mappings(payload, mappings)
        assert items[0].tags == []


# ---------------------------------------------------------------------------
# ingest_external — end-to-end with mocked ingest_content
# ---------------------------------------------------------------------------


class TestIngestExternal:
    @pytest.mark.asyncio
    async def test_single_item_accepted(self) -> None:
        request = ExternalIngestRequest(
            source_type="pocket",
            payload={"text": "Article text", "url": "https://pocket.com/article/1"},
            field_mappings=FieldMappings(content="text", source_uri="url"),
        )
        mock_result = {
            "status": "success",
            "artifact_id": "abc-123",
            "domain": "general",
            "chunks": 2,
        }
        # ingest_content is imported lazily inside ingest_external; patch it
        # at its canonical location so the lazy import resolves to the mock.
        with patch(
            "app.services.ingestion.ingest_content",
            return_value=mock_result,
        ):
            result = await ingest_external(request, tenant="default")

        assert result.accepted == 1
        assert result.skipped == 0
        assert result.errors == []
        assert result.source_type == "pocket"

    @pytest.mark.asyncio
    async def test_duplicate_item_counted_as_skipped(self) -> None:
        request = ExternalIngestRequest(
            source_type="readwise",
            payload={
                "highlights": [
                    {"text": "Same text", "url": "https://readwise.io/h/1"},
                ]
            },
            field_mappings=FieldMappings(
                content="highlights[].text",
                source_uri="highlights[].url",
            ),
        )
        mock_result = {"status": "duplicate", "artifact_id": "dup-99", "domain": "general", "chunks": 0}
        with patch(
            "app.services.ingestion.ingest_content",
            return_value=mock_result,
        ):
            result = await ingest_external(request, tenant="default")

        assert result.skipped == 1
        assert result.accepted == 0

    @pytest.mark.asyncio
    async def test_multiple_items_counts_correctly(self) -> None:
        """Three highlights: success, duplicate, error — verifies counts."""
        request = ExternalIngestRequest(
            source_type="readwise",
            payload={
                "highlights": [
                    {"text": "First", "url": "https://r.io/h/1"},
                    {"text": "Second", "url": "https://r.io/h/2"},
                    {"text": "Third", "url": "https://r.io/h/3"},
                ]
            },
            field_mappings=FieldMappings(
                content="highlights[].text",
                source_uri="highlights[].url",
            ),
        )
        side_effects = [
            {"status": "success", "artifact_id": "a1", "domain": "general", "chunks": 1},
            {"status": "duplicate", "artifact_id": "a2", "domain": "general", "chunks": 0},
            {"status": "error", "error": "Neo4j failure", "artifact_id": "a3", "domain": "general", "chunks": 0},
        ]
        call_count = 0

        def _mock_ingest_content(content, domain, metadata, **kwargs):
            nonlocal call_count
            r = side_effects[call_count]
            call_count += 1
            return r

        with patch("app.services.ingestion.ingest_content", side_effect=_mock_ingest_content):
            result = await ingest_external(request, tenant="default")

        assert result.accepted == 1
        assert result.skipped == 1
        assert len(result.errors) == 1
        assert result.errors[0]["phase"] == "ingest"

    @pytest.mark.asyncio
    async def test_mapping_error_returns_error_in_result(self) -> None:
        request = ExternalIngestRequest(
            source_type="broken",
            payload={"wrong_key": "value"},
            field_mappings=FieldMappings(content="missing_content", source_uri="missing_url"),
        )
        with patch("app.services.ingestion.ingest_content") as mock_ic:
            result = await ingest_external(request, tenant="default")
            mock_ic.assert_not_called()

        assert result.accepted == 0
        assert result.skipped == 0
        assert len(result.errors) == 1
        assert result.errors[0]["phase"] == "mapping"
