# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apple Calendar DataSource — Phase G.4.

Wraps the `ceridek` Swift helper via subprocess + JSON-over-stdout. The
helper inherits TCC grants from the Electron parent app's signed bundle
(see packages/desktop/swift/README.md for the load-bearing TCC contract).

Conforms to CalendarDataSource Protocol so meeting_capture's calendar
stitching resolves Apple Calendar events alongside Google + Outlook.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
from datetime import datetime, timezone
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from app.data_sources.calendar_protocol import CalendarEvent
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.apple_calendar")

# Helper binary location. Resolved at module load with three fallbacks:
#   1. CERID_HELPER_CERIDEK env var (operator override / CI mock)
#   2. Packaged location: alongside the Electron app's Contents/MacOS
#      (resolved relative to argv[0] of the parent process)
#   3. Development build: packages/desktop/swift/build/ceridek
DEFAULT_HELPER_NAME = "ceridek"


def _resolve_helper_path() -> str | None:
    override = os.getenv("CERID_HELPER_CERIDEK")
    if override and os.path.exists(override):
        return override

    # Look on PATH (set by the Electron app's launch env)
    on_path = shutil.which(DEFAULT_HELPER_NAME)
    if on_path:
        return on_path

    # Dev fallback — repo-root-relative build/
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
    )
    dev_path = os.path.join(
        repo_root, "packages", "desktop", "swift", "build", DEFAULT_HELPER_NAME,
    )
    if os.path.exists(dev_path):
        return dev_path
    return None


class AppleCalendarDataSource(DataSource):
    name = "apple_calendar"
    description = "Apple Calendar via Swift EventKit helper"
    requires_api_key = False  # TCC-gated, not API-key-gated

    def __init__(self, helper_path: str | None = None) -> None:
        self._helper_path = helper_path or _resolve_helper_path()

    def is_configured(self) -> bool:
        return platform.system() == "Darwin" and bool(self._helper_path)

    async def _invoke_helper(self, args: list[str]) -> Any:
        """Spawn the Swift helper and parse stdout as JSON.

        Returns None on TCC denial or helper crash; the caller treats
        None as a soft-skip rather than a hard error so a single
        unconfigured source doesn't break the multi-source query.
        """
        if not self._helper_path:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._helper_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                # Exit code 3 = TCC denial (documented in main.swift). This
                # isn't an error per se — just means the user hasn't granted
                # calendar access yet.
                if proc.returncode == 3:
                    logger.info("ceridek TCC denied: %s", stderr_text)
                else:
                    logger.warning("ceridek exited %d: %s", proc.returncode, stderr_text)
                return None
            return json.loads(stdout.decode("utf-8"))
        except (TimeoutError, OSError, ValueError) as exc:
            log_swallowed_error("apple_calendar._invoke_helper", exc)
            return None

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        raw = await self._invoke_helper([
            "events", _to_iso(start), _to_iso(end),
        ])
        if not isinstance(raw, list):
            return []
        events: list[CalendarEvent] = []
        for ev in raw[:max_results]:
            if not isinstance(ev, dict):
                continue
            out: CalendarEvent = {}
            if (eid := ev.get("id")) is not None:
                out["id"] = str(eid)
            if (title := ev.get("title")) is not None:
                out["title"] = str(title)
            if (s := _parse_iso(ev.get("start"))) is not None:
                out["start"] = s
            if (e := _parse_iso(ev.get("end"))) is not None:
                out["end"] = e
            if isinstance(ev.get("attendees"), list):
                out["attendees"] = [str(a) for a in ev["attendees"]]
            if (org := ev.get("organizer")) is not None:
                out["organizer"] = str(org)
            if (loc := ev.get("location")) is not None:
                out["location"] = str(loc)
            if (desc := ev.get("description")) is not None:
                out["description"] = str(desc)
            events.append(out)
        return events

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        from datetime import timedelta

        now = datetime.now(tz=timezone.utc)
        events = await self.list_events(
            start=now,
            end=now + timedelta(days=30),
            max_results=int(kwargs.get("max_results", 25)),
        )
        out: list[DataSourceResult] = []
        for ev in events:
            title = ev.get("title", "(no title)")
            start = ev.get("start")
            attendees = ev.get("attendees", [])
            body = (
                f"Title: {title}\n"
                f"Start: {start.isoformat() if isinstance(start, datetime) else start}\n"
                f"Attendees: {', '.join(attendees) if attendees else '(none)'}\n"
                f"{ev.get('description', '')}"
            )
            out.append(
                DataSourceResult(
                    title=title,
                    content=body,
                    source_url="x-apple-calendar://",
                    source_name="Apple Calendar",
                    confidence=0.65,
                ),
            )
        return out


def _to_iso(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    # Format helpers expect milliseconds-precision ISO8601
    return d.isoformat()


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
