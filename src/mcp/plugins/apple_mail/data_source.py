# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Apple Mail DataSource — Phase 4.1.

Wraps the ``ceridmail`` Swift helper via subprocess + JSON-over-stdout, mirroring
the AppleRemindersDataSource pattern. The helper is an ``.emlx`` walker over
``~/Library/Mail``; it inherits TCC grants from the Electron parent app's signed
bundle (Mail requires **Full Disk Access**, a System Settings toggle — not a
per-app usage-description key).

Helper contract (``packages/desktop/swift/CeridMail``):
  - ``ceridmail scan`` → ``{"ok": bool, "message_count": int,
    "messages": [{"subject": str, "sender": str, "date": str, "mailbox": str}]}``
  - Full-Disk-Access denial → **exit code 77** (parity with CeridReminders).

``scan`` returns recent message headers (the natural output of an .emlx header
walk); full-body search / query-side filtering is a host-side follow-up — see
docs/PRO_APPLE_MAIL.md — so ``query`` surfaces recent-message results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.apple_mail")

DEFAULT_HELPER_NAME = "ceridmail"
_TCC_DENIED_CODES = (3, 77)  # CeridMail exits 77 on Full-Disk-Access denial; 3 kept for parity


def _resolve_helper_path() -> str | None:
    override = os.getenv("CERID_HELPER_CERIDMAIL")
    if override and os.path.exists(override):
        return override
    on_path = shutil.which(DEFAULT_HELPER_NAME)
    if on_path:
        return on_path
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
    )
    dev_path = os.path.join(
        repo_root, "packages", "desktop", "swift", "build", DEFAULT_HELPER_NAME,
    )
    if os.path.exists(dev_path):
        return dev_path
    return None


class AppleMailDataSource(DataSource):
    name = "apple_mail"
    description = "Apple Mail via Swift .emlx-walker helper"
    requires_api_key = False  # TCC-gated (Full Disk Access), not API-key-gated

    def __init__(self, helper_path: str | None = None) -> None:
        self._helper_path = helper_path or _resolve_helper_path()

    def is_configured(self) -> bool:
        return platform.system() == "Darwin" and bool(self._helper_path)

    async def _invoke_helper(self, args: list[str]) -> Any:
        """Spawn the Swift helper and parse stdout as JSON.

        Returns None on TCC denial or helper crash; the caller treats None as a
        soft-skip so a single unconfigured source doesn't break a multi-source query.
        """
        if not self._helper_path:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._helper_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20.0)
            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                if proc.returncode in _TCC_DENIED_CODES:
                    logger.info("ceridmail TCC denied: %s", stderr_text)
                else:
                    logger.warning("ceridmail exited %d: %s", proc.returncode, stderr_text)
                return None
            return json.loads(stdout.decode("utf-8"))
        except (TimeoutError, OSError, ValueError) as exc:
            log_swallowed_error("apple_mail._invoke_helper", exc)
            return None

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        raw = await self._invoke_helper(["scan"])
        if not isinstance(raw, dict) or not raw.get("ok"):
            return []
        messages = raw.get("messages", [])
        if not isinstance(messages, list):
            return []
        max_results = int(kwargs.get("max_results", 25))
        out: list[DataSourceResult] = []
        for msg in messages[:max_results]:
            if not isinstance(msg, dict):
                continue
            subject = str(msg.get("subject", "(no subject)"))
            sender = str(msg.get("sender", "unknown"))
            mailbox = str(msg.get("mailbox", ""))
            date = str(msg.get("date", ""))
            out.append(
                DataSourceResult(
                    title=f"Mail: {subject}",
                    content=f"From {sender} · {mailbox} · {date}".strip(" ·"),
                    source_url="message://",
                    source_name="Apple Mail",
                    confidence=0.5,
                ),
            )
        return out
