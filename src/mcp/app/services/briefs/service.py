# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Brief generation service — daily and weekly narrative output.

Phase N of v0.92 plan. This module exposes :class:`BriefService` whose
``generate_daily`` / ``generate_weekly`` coroutines are called by the
``BriefGenerationJob`` (Phase P). The service itself has no knowledge of
the background processor; it is a pure coroutine-over-LLM pattern.

Template loading uses :mod:`pathlib` at module init so the ``.md`` files
are read once and cached; the service is therefore safe to instantiate
at startup rather than per-request.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from core.utils.internal_llm import call_internal_llm
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.briefs")

# ---------------------------------------------------------------------------
# Template cache — loaded once at import time
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_DAILY_TEMPLATE: str = (_TEMPLATE_DIR / "daily.md").read_text(encoding="utf-8")
_WEEKLY_TEMPLATE: str = (_TEMPLATE_DIR / "weekly.md").read_text(encoding="utf-8")

# Bump this string whenever the template text changes in a semantically
# meaningful way (section headers, placeholder names, prompt intent).
PROMPT_VERSION_DAILY = "daily-v1"
PROMPT_VERSION_WEEKLY = "weekly-v1"

# Section headers expected in each brief type.
_DAILY_SECTIONS = ("CONNECTIONS", "PATTERN", "QUESTION")
_WEEKLY_SECTIONS = ("EMERGING_THESIS", "CONTRADICTIONS", "KNOWLEDGE_GAPS", "ONE_ACTION")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class BriefRecord(BaseModel):
    """Persisted record for a generated brief."""

    brief_id: str
    kind: str  # "daily" | "weekly"
    generated_at: datetime
    prompt_version: str
    sections: dict[str, str]
    claim_ids: list[str]
    status: str  # "pending" | "generated" | "failed" | "snoozed"

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in {"daily", "weekly"}:
            raise ValueError(f"kind must be 'daily' or 'weekly', got {v!r}")
        return v

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in {"pending", "generated", "failed", "snoozed"}:
            raise ValueError(
                f"status must be one of pending/generated/failed/snoozed, got {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

_SECTION_HEADER_RE = re.compile(
    r"^##\s+(CONNECTIONS|PATTERN|QUESTION|EMERGING_THESIS|CONTRADICTIONS|KNOWLEDGE_GAPS|ONE_ACTION)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_sections(text: str) -> dict[str, str]:
    """Extract named sections from LLM output.

    Sections are delimited by ``## SECTION_NAME`` headers (case-insensitive,
    tolerates leading/trailing whitespace on the header line). Content
    between the header and the next header (or end of text) is stripped.

    Returns a ``{SECTION_NAME: content}`` dict. Unrecognised headers are
    silently skipped. Content is stripped of leading/trailing whitespace.
    """
    sections: dict[str, str] = {}
    matches = list(_SECTION_HEADER_RE.finditer(text))
    for i, match in enumerate(matches):
        name = match.group(1).upper()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections[name] = content
    return sections


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class BriefService:
    """Generates, stores, and retrieves daily and weekly briefs.

    This class is intentionally stateless beyond the cached templates —
    all persistence goes through the Neo4j adapter
    (:mod:`app.db.neo4j.briefs`).
    """

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate_daily(
        self,
        inbox_recent: str,
        notes_recent_7d: str,
        *,
        verified_response_format_hint: str = "",
    ) -> BriefRecord:
        """Generate a daily brief from inbox and recent notes.

        Parameters
        ----------
        inbox_recent
            Concatenated inbox items from the last 24 hours.
        notes_recent_7d
            Concatenated notes / vault entries from the last 7 days.
        verified_response_format_hint
            Optional rendering hint injected into the prompt for
            ``<VerifiedResponse>`` downstream consumers.

        Returns
        -------
        BriefRecord
            Status ``"generated"`` when sections parsed successfully,
            ``"failed"`` on unrecoverable LLM errors.
        """
        from datetime import date

        prompt = (
            _DAILY_TEMPLATE
            .replace("{{brief_date}}", date.today().isoformat())
            .replace("{{inbox_last_24h}}", inbox_recent or "(empty)")
            .replace("{{notes_last_7d}}", notes_recent_7d or "(empty)")
            .replace(
                "{{verified_response_format_hint}}",
                verified_response_format_hint,
            )
        )

        record = BriefRecord(
            brief_id=str(uuid.uuid4()),
            kind="daily",
            generated_at=datetime.now(tz=timezone.utc),
            prompt_version=PROMPT_VERSION_DAILY,
            sections={},
            claim_ids=[],
            status="pending",
        )

        try:
            raw = await call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1200,
                stage="brief/daily",
            )
            sections = parse_sections(raw)
            missing = [s for s in _DAILY_SECTIONS if s not in sections]
            if missing:
                logger.warning(
                    "daily brief missing sections=%s brief_id=%s",
                    missing,
                    record.brief_id,
                )
            record = record.model_copy(
                update={"sections": sections, "status": "generated"}
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("briefs.generate_daily", exc)
            record = record.model_copy(update={"status": "failed"})

        return record

    async def generate_weekly(
        self,
        full_vault_snapshot: str,
        contradiction_log_recent: str,
        *,
        week_window: str = "",
    ) -> BriefRecord:
        """Generate a weekly synthesis brief.

        Parameters
        ----------
        full_vault_snapshot
            Serialised representation of the full vault (titles + summaries).
        contradiction_log_recent
            Recent contradiction log entries.
        week_window
            Human-readable label for the week (e.g. ``"2026-05-05 – 2026-05-11"``).
            Defaults to empty if not supplied.
        """
        from datetime import date

        if not week_window:
            week_window = date.today().isoformat()

        prompt = (
            _WEEKLY_TEMPLATE
            .replace("{{week_window}}", week_window)
            .replace("{{full_vault}}", full_vault_snapshot or "(empty)")
            .replace(
                "{{contradiction_log_recent}}",
                contradiction_log_recent or "(none)",
            )
        )

        record = BriefRecord(
            brief_id=str(uuid.uuid4()),
            kind="weekly",
            generated_at=datetime.now(tz=timezone.utc),
            prompt_version=PROMPT_VERSION_WEEKLY,
            sections={},
            claim_ids=[],
            status="pending",
        )

        try:
            raw = await call_internal_llm(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
                stage="brief/weekly",
            )
            sections = parse_sections(raw)
            missing = [s for s in _WEEKLY_SECTIONS if s not in sections]
            if missing:
                logger.warning(
                    "weekly brief missing sections=%s brief_id=%s",
                    missing,
                    record.brief_id,
                )
            record = record.model_copy(
                update={"sections": sections, "status": "generated"}
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("briefs.generate_weekly", exc)
            record = record.model_copy(update={"status": "failed"})

        return record

    # ------------------------------------------------------------------
    # Persistence (delegates to Neo4j adapter)
    # ------------------------------------------------------------------

    async def store(self, record: BriefRecord, neo4j_driver: Any) -> None:
        """Persist a :class:`BriefRecord` to Neo4j."""
        from app.db.neo4j.briefs import save_brief

        try:
            save_brief(neo4j_driver, record)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("briefs.store", exc)

    async def list_recent(
        self,
        neo4j_driver: Any,
        *,
        kind: str,
        limit: int = 20,
    ) -> list[BriefRecord]:
        """Return the most recent brief records of the given kind."""
        from app.db.neo4j.briefs import list_briefs

        try:
            return list_briefs(neo4j_driver, kind=kind, limit=limit)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("briefs.list_recent", exc)
            return []
