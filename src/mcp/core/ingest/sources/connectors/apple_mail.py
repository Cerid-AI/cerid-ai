# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Apple Mail SourceConnector.

Wraps the ``ceridmail`` Swift helper (``packages/desktop/swift/CeridMail/``).
The helper holds the TCC entitlement; this connector marshals
subprocess invocations + JSON parsing.

Cursor shape: ``{"last_message_iso": iso8601 | None}``
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

logger = logging.getLogger("ai-companion.connectors.apple_mail")

_HELPER_BIN = "ceridmail"
_SCAN_TIMEOUT_S = 30
_FETCH_TIMEOUT_S = 90


def _helper_path() -> str | None:
    """Resolve the path to the bundled ``ceridmail`` binary. Returns
    None when the helper isn't installed (community / non-mac hosts).
    """
    return shutil.which(_HELPER_BIN)


def _parse_messages(stdout: str) -> list[dict[str, Any]]:
    """Normalize the ``ceridmail since`` JSON payload into message dicts
    (oldest-first, as emitted by the helper). Raises ``ValueError`` on a
    malformed or failure payload so ``fetch_since`` can degrade gracefully
    rather than crash the poll sweep.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ceridmail since returned non-JSON: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        detail = payload.get("error", "unknown") if isinstance(payload, dict) else "non-object"
        raise ValueError(f"ceridmail since reported failure: {detail}")
    raw = payload.get("messages", [])
    if not isinstance(raw, list):
        raise ValueError("ceridmail since: 'messages' is not a list")
    out: list[dict[str, Any]] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        out.append(
            {
                "id": str(m.get("id", "")),
                "date": str(m.get("date", "")),
                "subject": str(m.get("subject", "")),
                "from": str(m.get("from", "")),
                "to": str(m.get("to", "")),
                "body": str(m.get("body", "")),
            }
        )
    return out


class AppleMailConnector(SourceConnector):
    """Pro-tier connector for Mail.app archives."""

    kind = "apple_mail"
    tier = "pro"
    # Ingestion runs through the ceridmail desktop helper; /sources/kinds
    # reports availability="requires_desktop" when it is absent.
    requires_desktop = True

    def desktop_available(self) -> bool:
        """Availability probe for /sources/kinds — is the helper on PATH?"""
        return _helper_path() is not None

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        bin_path = _helper_path()
        if bin_path is None:
            raise ValueError(
                "ceridmail helper not found on PATH. "
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
            raise ValueError("ceridmail scan timed out") from exc

        if proc.returncode != 0:
            raise ValueError(f"ceridmail scan failed: {stderr.decode('utf-8', errors='replace')[:200]}")

        try:
            scan = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"ceridmail scan returned non-JSON: {exc}") from exc

        if not scan.get("ok"):
            raise ValueError(f"ceridmail scan reported failure: {scan.get('error', 'unknown')}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return ConnectResult(
            source_id=str(uuid.uuid4()),
            config={
                "name": config.get("name") or "Apple Mail",
                "account_count": scan.get("account_count", 0),
                "message_count_at_connect": scan.get("message_count", 0),
            },
            connection_time_ms=elapsed_ms,
            initial_cursor={"last_message_iso": None},
        )

    async def fetch_since(
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Run ``ceridmail since <cursor>``, ingest each returned message via the
        DI sink, and yield one :class:`SourceArtifactEvent` per artifact with the
        cursor advance embedded (``cursor_after``) so the poll worker persists it
        incrementally — crash-safe at-least-once (``ingest_content`` dedups
        re-delivery).

        Safe no-op (empty iterator) when the helper isn't installed or the ingest
        sink isn't wired, keeping registration + health-checks independent of the
        worker.
        """
        from core.ingest.sources.ingest_sink import get_source_ingest_fn
        from core.utils.swallowed import log_swallowed_error

        bin_path = _helper_path()
        ingest_fn = get_source_ingest_fn()
        if bin_path is None or ingest_fn is None:
            return

        since = (cursor or {}).get("last_message_iso") or ""
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
            logger.warning("ceridmail since timed out for %s", source_id)
            return

        if proc.returncode != 0:
            logger.warning(
                "ceridmail since failed for %s: %s",
                source_id,
                stderr.decode("utf-8", errors="replace")[:200],
            )
            return

        try:
            messages = _parse_messages(stdout.decode("utf-8", errors="replace"))
        except ValueError as exc:
            logger.warning("ceridmail since parse failed for %s: %s", source_id, exc)
            return

        domain = (config.get("domain") or "general").strip() or "general"

        for msg in messages:  # oldest-first → monotonic cursor advance
            subject = msg.get("subject", "")
            body = msg.get("body", "")
            content = (f"{subject}\n\n{body}" if subject else body).strip()
            if not content:
                continue
            t0 = time.monotonic()
            try:
                artifact_id = await ingest_fn(
                    content,
                    domain=domain,
                    metadata={
                        "source_id": source_id,
                        "source_type": "apple_mail",
                        "title": subject,
                        "from": msg.get("from", ""),
                        "message_id": msg.get("id", ""),
                        "received_at": msg.get("date", ""),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — one bad message must not abort the source
                log_swallowed_error(
                    "core.ingest.sources.connectors.apple_mail.fetch_since", exc
                )
                # Stop WITHOUT advancing past this message — cursor sits at the
                # last successful event, so this one retries next poll.
                return
            yield SourceArtifactEvent(
                source_id=source_id,
                artifact_id=str(artifact_id or ""),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                cursor_after={"last_message_iso": msg.get("date", "")},
                title=subject,
                domain=domain,
            )

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        if _helper_path() is None:
            return HealthStatus(
                ok=False,
                detail="ceridmail helper not on PATH",
            )
        return HealthStatus(ok=True, detail="ceridmail helper available")

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
