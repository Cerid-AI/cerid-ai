# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Apple Reminders SourceConnector.

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
_FETCH_TIMEOUT_S = 60


def _helper_path() -> str | None:
    return shutil.which(_HELPER_BIN)


def _parse_reminders(stdout: str) -> list[dict[str, Any]]:
    """Normalize the ``ceridreminders since`` JSON payload into reminder dicts
    (oldest-first by modified time). Raises ``ValueError`` on a malformed or
    failure payload so ``fetch_since`` can degrade gracefully.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ceridreminders since returned non-JSON: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        detail = payload.get("error", "unknown") if isinstance(payload, dict) else "non-object"
        raise ValueError(f"ceridreminders since reported failure: {detail}")
    raw = payload.get("reminders", [])
    if not isinstance(raw, list):
        raise ValueError("ceridreminders since: 'reminders' is not a list")
    out: list[dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "id": str(r.get("id", "")),
                "title": str(r.get("title", "")),
                "notes": str(r.get("notes") or ""),
                "due": str(r.get("due") or ""),
                "completed": bool(r.get("completed", False)),
                "priority": int(r.get("priority", 0) or 0),
                "list": str(r.get("list", "")),
                "modified": str(r.get("modified", "")),
            }
        )
    return out


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
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Run ``ceridreminders since <cursor>``, ingest each returned reminder
        via the DI sink, and yield one :class:`SourceArtifactEvent` per artifact
        with the cursor advance embedded — crash-safe at-least-once
        (``ingest_content`` dedups re-delivery).

        Safe no-op (empty iterator) when the helper isn't installed or the ingest
        sink isn't wired.
        """
        from core.ingest.sources.ingest_sink import get_source_ingest_fn
        from core.utils.swallowed import log_swallowed_error

        bin_path = _helper_path()
        ingest_fn = get_source_ingest_fn()
        if bin_path is None or ingest_fn is None:
            return

        since = (cursor or {}).get("last_modified_iso") or ""
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "since",
            since,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_FETCH_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("ceridreminders since timed out for %s", source_id)
            return

        if proc.returncode != 0:
            logger.warning(
                "ceridreminders since failed for %s: %s",
                source_id,
                stderr.decode("utf-8", errors="replace")[:200],
            )
            return

        try:
            reminders = _parse_reminders(stdout.decode("utf-8", errors="replace"))
        except ValueError as exc:
            logger.warning("ceridreminders since parse failed for %s: %s", source_id, exc)
            return

        domain = (config.get("domain") or "general").strip() or "general"

        for rem in reminders:  # oldest-first → monotonic cursor advance
            title = rem.get("title", "")
            notes = rem.get("notes", "")
            content = (f"{title}\n\n{notes}" if notes else title).strip()
            if not content:
                continue
            t0 = time.monotonic()
            try:
                artifact_id = await ingest_fn(
                    content,
                    domain=domain,
                    metadata={
                        "source_id": source_id,
                        "source_type": "apple_reminders",
                        "title": title,
                        "reminder_id": rem.get("id", ""),
                        "list": rem.get("list", ""),
                        "due": rem.get("due", ""),
                        "modified_at": rem.get("modified", ""),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — one bad reminder must not abort the source
                log_swallowed_error(
                    "core.ingest.sources.connectors.apple_reminders.fetch_since", exc
                )
                return
            yield SourceArtifactEvent(
                source_id=source_id,
                artifact_id=str(artifact_id or ""),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                cursor_after={"last_modified_iso": rem.get("modified", "")},
                title=title,
                domain=domain,
            )

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        if _helper_path() is None:
            return HealthStatus(ok=False, detail="ceridreminders helper not on PATH")
        return HealthStatus(ok=True, detail="ceridreminders helper available")

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
