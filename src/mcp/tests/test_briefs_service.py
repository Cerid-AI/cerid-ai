# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the brief generation service (Phase N, v0.92).

Coverage
--------
* Template loading — both daily and weekly templates are readable .md files.
* BriefRecord Pydantic model shape — construction and round-trip via
  model_copy / model_dump.
* generate_daily — mocked call_internal_llm; asserts stage="brief/daily",
  returns a record with all three sections populated.
* generate_weekly — mocked call_internal_llm; asserts stage="brief/weekly",
  returns a record with all four sections populated.
* parse_sections() — correctly extracts named sections from a mock LLM
  response using ``## SECTION`` markers.
* Section parsing tolerates whitespace + minor format variation.

The LLM is always mocked — no real model calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Template loading tests
# ---------------------------------------------------------------------------


class TestTemplateLoading:
    """Templates are loaded from .md files relative to the service module."""

    def test_daily_template_is_non_empty_string(self):
        from app.services.briefs.service import _DAILY_TEMPLATE

        assert isinstance(_DAILY_TEMPLATE, str)
        assert len(_DAILY_TEMPLATE) > 50
        # Must contain expected section header
        assert "## CONNECTIONS" in _DAILY_TEMPLATE

    def test_weekly_template_is_non_empty_string(self):
        from app.services.briefs.service import _WEEKLY_TEMPLATE

        assert isinstance(_WEEKLY_TEMPLATE, str)
        assert len(_WEEKLY_TEMPLATE) > 50
        assert "## EMERGING_THESIS" in _WEEKLY_TEMPLATE

    def test_daily_template_has_all_section_headers(self):
        from app.services.briefs.service import _DAILY_TEMPLATE

        for header in ("## CONNECTIONS", "## PATTERN", "## QUESTION"):
            assert header in _DAILY_TEMPLATE, f"Missing header {header!r} in daily template"

    def test_weekly_template_has_all_section_headers(self):
        from app.services.briefs.service import _WEEKLY_TEMPLATE

        for header in (
            "## EMERGING_THESIS",
            "## CONTRADICTIONS",
            "## KNOWLEDGE_GAPS",
            "## ONE_ACTION",
        ):
            assert header in _WEEKLY_TEMPLATE, f"Missing header {header!r} in weekly template"

    def test_daily_template_contains_required_placeholders(self):
        from app.services.briefs.service import _DAILY_TEMPLATE

        for placeholder in ("{{inbox_last_24h}}", "{{notes_last_7d}}"):
            assert placeholder in _DAILY_TEMPLATE

    def test_weekly_template_contains_required_placeholders(self):
        from app.services.briefs.service import _WEEKLY_TEMPLATE

        for placeholder in (
            "{{full_vault}}",
            "{{week_window}}",
            "{{contradiction_log_recent}}",
        ):
            assert placeholder in _WEEKLY_TEMPLATE

    def test_templates_loaded_from_file_not_hardcoded(self):
        """Confirm templates live on disk, not embedded in Python source."""
        from app.services.briefs import service as svc_mod

        template_dir = Path(svc_mod.__file__).parent / "templates"
        assert (template_dir / "daily.md").exists()
        assert (template_dir / "weekly.md").exists()


# ---------------------------------------------------------------------------
# BriefRecord model tests
# ---------------------------------------------------------------------------


class TestBriefRecord:
    """Pydantic shape and validation."""

    def _make_record(self, **overrides):
        from app.services.briefs.service import BriefRecord

        defaults = dict(
            brief_id="test-brief-123",
            kind="daily",
            generated_at=datetime(2026, 5, 10, 6, 0, 0, tzinfo=timezone.utc),
            prompt_version="daily-v1",
            sections={"CONNECTIONS": "A links to B.", "PATTERN": "Trend.", "QUESTION": "Why?"},
            claim_ids=["claim-1", "claim-2"],
            status="generated",
        )
        defaults.update(overrides)
        return BriefRecord(**defaults)

    def test_round_trip_model_dump(self):
        record = self._make_record()
        dumped = record.model_dump()
        assert dumped["brief_id"] == "test-brief-123"
        assert dumped["kind"] == "daily"
        assert dumped["status"] == "generated"
        assert len(dumped["claim_ids"]) == 2

    def test_model_copy_preserves_fields(self):
        record = self._make_record()
        updated = record.model_copy(update={"status": "snoozed"})
        assert updated.status == "snoozed"
        assert updated.brief_id == record.brief_id

    def test_invalid_kind_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._make_record(kind="monthly")

    def test_invalid_status_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._make_record(status="archived")

    def test_valid_kind_weekly(self):
        record = self._make_record(kind="weekly")
        assert record.kind == "weekly"

    def test_all_valid_statuses(self):
        for status in ("pending", "generated", "failed", "snoozed"):
            record = self._make_record(status=status)
            assert record.status == status

    def test_claim_ids_default_empty(self):
        from app.services.briefs.service import BriefRecord

        record = BriefRecord(
            brief_id="x",
            kind="daily",
            generated_at=datetime.now(tz=timezone.utc),
            prompt_version="v1",
            sections={},
            claim_ids=[],
            status="pending",
        )
        assert record.claim_ids == []


# ---------------------------------------------------------------------------
# parse_sections tests
# ---------------------------------------------------------------------------


class TestParseSections:
    """Section extraction from LLM output."""

    def test_extracts_all_three_daily_sections(self):
        from app.services.briefs.service import parse_sections

        text = (
            "Some preamble text.\n"
            "\n"
            "## CONNECTIONS\n"
            "Item A connects to item B.\n"
            "\n"
            "## PATTERN\n"
            "There is a rising trend.\n"
            "\n"
            "## QUESTION\n"
            "What does this imply?\n"
        )
        sections = parse_sections(text)
        assert sections["CONNECTIONS"] == "Item A connects to item B."
        assert sections["PATTERN"] == "There is a rising trend."
        assert sections["QUESTION"] == "What does this imply?"

    def test_extracts_all_four_weekly_sections(self):
        from app.services.briefs.service import parse_sections

        text = (
            "## EMERGING_THESIS\n"
            "The main thesis here.\n"
            "\n"
            "## CONTRADICTIONS\n"
            "Sources disagree on X.\n"
            "\n"
            "## KNOWLEDGE_GAPS\n"
            "Missing coverage on Y.\n"
            "\n"
            "## ONE_ACTION\n"
            "Read about Z this week.\n"
        )
        sections = parse_sections(text)
        assert "EMERGING_THESIS" in sections
        assert "CONTRADICTIONS" in sections
        assert "KNOWLEDGE_GAPS" in sections
        assert "ONE_ACTION" in sections

    def test_tolerates_extra_whitespace_on_header_line(self):
        from app.services.briefs.service import parse_sections

        text = (
            "##  CONNECTIONS  \n"
            "Some connections.\n"
            "\n"
            "##  PATTERN  \n"
            "Some pattern.\n"
            "\n"
            "##  QUESTION  \n"
            "A question?\n"
        )
        sections = parse_sections(text)
        assert "CONNECTIONS" in sections
        assert "PATTERN" in sections
        assert "QUESTION" in sections

    def test_case_insensitive_headers(self):
        from app.services.briefs.service import parse_sections

        text = (
            "## connections\n"
            "Lowercase connections.\n"
            "\n"
            "## PATTERN\n"
            "Pattern.\n"
            "\n"
            "## QUESTION\n"
            "Question.\n"
        )
        sections = parse_sections(text)
        # Keys are uppercased by parse_sections
        assert "CONNECTIONS" in sections
        assert sections["CONNECTIONS"] == "Lowercase connections."

    def test_strips_whitespace_around_content(self):
        from app.services.briefs.service import parse_sections

        text = "## CONNECTIONS\n\n   Leading and trailing spaces.   \n\n## PATTERN\nP.\n\n## QUESTION\nQ.\n"
        sections = parse_sections(text)
        assert sections["CONNECTIONS"] == "Leading and trailing spaces."

    def test_unknown_headers_ignored(self):
        from app.services.briefs.service import parse_sections

        text = (
            "## MADE_UP_SECTION\n"
            "Should be ignored.\n"
            "\n"
            "## CONNECTIONS\n"
            "Real content.\n"
            "\n"
            "## PATTERN\n"
            "Pattern.\n"
            "\n"
            "## QUESTION\n"
            "Q?\n"
        )
        sections = parse_sections(text)
        assert "MADE_UP_SECTION" not in sections
        assert "CONNECTIONS" in sections

    def test_returns_empty_dict_on_no_headers(self):
        from app.services.briefs.service import parse_sections

        sections = parse_sections("Just some plain text with no headers.")
        assert sections == {}


# ---------------------------------------------------------------------------
# generate_daily tests
# ---------------------------------------------------------------------------

_MOCK_DAILY_LLM_RESPONSE = """\
## CONNECTIONS
Item A in the inbox connects to note B from last week because both discuss
the same framework.

## PATTERN
A recurring focus on async patterns is evident across the recent inbox
and notes.

## QUESTION
Is the team converging on a new default for async task execution?
"""


class TestGenerateDaily:
    """generate_daily returns a BriefRecord with all three sections."""

    @pytest.mark.asyncio
    async def test_returns_brief_record_with_all_sections(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_DAILY_LLM_RESPONSE,
        ) as mock_llm:
            record = await service.generate_daily(
                inbox_recent="[item A] Discussion of async tasks.",
                notes_recent_7d="[note B] Async patterns in Python.",
            )

        # Stage kwarg assertion — contract requirement
        call_kwargs = mock_llm.call_args.kwargs
        assert call_kwargs.get("stage") == "brief/daily"

        assert record.kind == "daily"
        assert record.status == "generated"
        assert "CONNECTIONS" in record.sections
        assert "PATTERN" in record.sections
        assert "QUESTION" in record.sections

    @pytest.mark.asyncio
    async def test_passes_stage_brief_daily_to_llm(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_DAILY_LLM_RESPONSE,
        ) as mock_llm:
            await service.generate_daily("inbox", "notes")

        assert mock_llm.called
        assert mock_llm.call_args.kwargs["stage"] == "brief/daily"

    @pytest.mark.asyncio
    async def test_status_is_failed_on_llm_error(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM unavailable"),
        ), patch("app.services.briefs.service.log_swallowed_error") as mock_swallowed:
            record = await service.generate_daily("inbox", "notes")

        assert record.status == "failed"
        mock_swallowed.assert_called_once()
        assert mock_swallowed.call_args.args[0] == "briefs.generate_daily"

    @pytest.mark.asyncio
    async def test_record_has_prompt_version(self):
        from app.services.briefs.service import PROMPT_VERSION_DAILY, BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_DAILY_LLM_RESPONSE,
        ):
            record = await service.generate_daily("inbox", "notes")

        assert record.prompt_version == PROMPT_VERSION_DAILY

    @pytest.mark.asyncio
    async def test_record_has_brief_id_and_generated_at(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_DAILY_LLM_RESPONSE,
        ):
            record = await service.generate_daily("inbox", "notes")

        assert record.brief_id  # non-empty UUID string
        assert isinstance(record.generated_at, datetime)


# ---------------------------------------------------------------------------
# generate_weekly tests
# ---------------------------------------------------------------------------

_MOCK_WEEKLY_LLM_RESPONSE = """\
## EMERGING_THESIS
The vault consistently shows that async patterns outperform synchronous
approaches at scale. Multiple independent sources corroborate this.

## CONTRADICTIONS
Source X claims threads are superior for I/O-bound tasks; source Y claims
async is always better. The contradiction is unresolved.

## KNOWLEDGE_GAPS
Limited coverage of structured concurrency patterns beyond asyncio. No
benchmarks comparing async vs thread pools on the user's specific workload.

## ONE_ACTION
Ingest the official Python asyncio documentation chapter on task groups
to fill the structured-concurrency gap.
"""


class TestGenerateWeekly:
    """generate_weekly returns a BriefRecord with all four sections."""

    @pytest.mark.asyncio
    async def test_returns_brief_record_with_all_sections(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_WEEKLY_LLM_RESPONSE,
        ):
            record = await service.generate_weekly(
                full_vault_snapshot="[summary of full vault]",
                contradiction_log_recent="[recent contradictions]",
                week_window="2026-05-05 – 2026-05-11",
            )

        assert record.kind == "weekly"
        assert record.status == "generated"
        assert "EMERGING_THESIS" in record.sections
        assert "CONTRADICTIONS" in record.sections
        assert "KNOWLEDGE_GAPS" in record.sections
        assert "ONE_ACTION" in record.sections

    @pytest.mark.asyncio
    async def test_passes_stage_brief_weekly_to_llm(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_WEEKLY_LLM_RESPONSE,
        ) as mock_llm:
            await service.generate_weekly("vault", "contradictions")

        assert mock_llm.called
        assert mock_llm.call_args.kwargs["stage"] == "brief/weekly"

    @pytest.mark.asyncio
    async def test_status_is_failed_on_llm_error(self):
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM unavailable"),
        ), patch("app.services.briefs.service.log_swallowed_error") as mock_swallowed:
            record = await service.generate_weekly("vault", "contradictions")

        assert record.status == "failed"
        mock_swallowed.assert_called_once()
        assert mock_swallowed.call_args.args[0] == "briefs.generate_weekly"

    @pytest.mark.asyncio
    async def test_record_has_prompt_version(self):
        from app.services.briefs.service import PROMPT_VERSION_WEEKLY, BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_WEEKLY_LLM_RESPONSE,
        ):
            record = await service.generate_weekly("vault", "contradictions")

        assert record.prompt_version == PROMPT_VERSION_WEEKLY

    @pytest.mark.asyncio
    async def test_default_week_window_does_not_raise(self):
        """generate_weekly works when week_window is not supplied."""
        from app.services.briefs.service import BriefService

        service = BriefService()
        with patch(
            "app.services.briefs.service.call_internal_llm",
            new_callable=AsyncMock,
            return_value=_MOCK_WEEKLY_LLM_RESPONSE,
        ):
            record = await service.generate_weekly("vault", "contradictions")

        # No exception raised; kind is correct
        assert record.kind == "weekly"
