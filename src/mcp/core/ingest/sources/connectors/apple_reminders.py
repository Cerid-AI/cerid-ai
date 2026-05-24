# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Apple Reminders SourceConnector — Phase 4a B4a.4.

Wraps the ``ceridreminders`` Swift helper. Mirrors the Apple Mail
shape — Pro tier, subprocess to a TCC-entitled binary.

Cursor shape: ``{"last_modified_iso": iso8601 | None}``
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from typing import Any, AsyncIterator

from core.ingest.sources.base import (
    ConnectResult,
    HealthStatus,
    SourceArtifactEvent,
    SourceConnector,
)

logger = logging.getLogger("ai-companion.connectors.apple_reminders")

_HELPER_BIN = "ceridreminders"
_SCAN_TIMEOUT_S = 20


def _helper_path() -> str | None:
    return shutil.which(_HELPER_BIN)


class AppleRemindersConnector(SourceConnector):
    kind = "apple_reminders"
    tier = "pro"

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        bin_path = _helper_path()
        if bin_path is None:
            raise ValueError(
                "ceridreminders helper not found on PATH. "
                "Install via the Cerid desktop app's Apple connector setup."
            )

        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "scan",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SCAN_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ValueError("ceridreminders scan timed out") from exc

        if proc.returncode != 0:
            raise ValueError(
                f"ceridreminders scan failed: {stderr.decode('utf-8', errors='replace')[:200]}"
            )

        try:
            scan = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"ceridreminders scan returned non-JSON: {exc}") from exc

        if not scan.get("ok"):
            raise ValueError(
                f"ceridreminders scan reported failure: {scan.get('error', 'unknown')}"
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return ConnectResult(
            source_id=str(uuid.uuid4()),
            config={
                "name": config.get("name") or "Apple Reminders",
                "list_count": scan.get("list_count", 0),
                "list_names": scan.get("list_names", []),
            },
            connection_time_ms=elapsed_ms,
            initial_cursor={"last_modified_iso": None},
        )

    async def fetch_since(
        self, source_id: str, cursor: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Phase 4a stub — predicate-based EventKit fetch lands when
        the Swift helper's `since` subcommand returns reminders.
        """
        if False:  # pragma: no cover
            yield SourceArtifactEvent(  # type: ignore[unreachable]
                source_id=source_id,
                artifact_id="",
                elapsed_ms=0,
                cursor_after={},
            )
        return

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        if _helper_path() is None:
            return HealthStatus(ok=False, detail="ceridreminders helper not on PATH")
        return HealthStatus(ok=True, detail="ceridreminders helper available")

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
