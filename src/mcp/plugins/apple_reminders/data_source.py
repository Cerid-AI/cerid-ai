# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apple Reminders DataSource — Phase 4.3.

Wraps the ``ceridreminders`` Swift helper via subprocess + JSON-over-stdout,
mirroring the AppleCalendarDataSource pattern. The helper inherits TCC grants
from the Electron parent app's signed bundle.

Helper contract (``packages/desktop/swift/CeridReminders``):
  - ``ceridreminders scan`` → ``{"ok": bool, "list_count": int, "list_names": [str]}``
  - TCC denial → **exit code 77** (NOT 3 like CeridEventKit/CeridPhotos).

The ``scan`` subcommand returns reminder-list metadata; full reminder content
awaits the helper's predicate-based ``since`` fetch (host-side, not yet authored
— see docs/PRO_APPLE_REMINDERS.md), so ``query`` surfaces list-level results.
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

logger = logging.getLogger("ai-companion.data_sources.apple_reminders")

DEFAULT_HELPER_NAME = "ceridreminders"
_TCC_DENIED_CODES = (3, 77)  # CeridReminders exits 77 on TCC denial; 3 kept for parity


def _resolve_helper_path() -> str | None:
    override = os.getenv("CERID_HELPER_CERIDREMINDERS")
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


class AppleRemindersDataSource(DataSource):
    name = "apple_reminders"
    description = "Apple Reminders via Swift EventKit helper"
    requires_api_key = False  # TCC-gated, not API-key-gated

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
                    logger.info("ceridreminders TCC denied: %s", stderr_text)
                else:
                    logger.warning("ceridreminders exited %d: %s", proc.returncode, stderr_text)
                return None
            return json.loads(stdout.decode("utf-8"))
        except (TimeoutError, OSError, ValueError) as exc:
            log_swallowed_error("apple_reminders._invoke_helper", exc)
            return None

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        raw = await self._invoke_helper(["scan"])
        if not isinstance(raw, dict) or not raw.get("ok"):
            return []
        names = raw.get("list_names", [])
        if not isinstance(names, list):
            return []
        max_results = int(kwargs.get("max_results", 25))
        out: list[DataSourceResult] = []
        for name in names[:max_results]:
            list_name = str(name)
            out.append(
                DataSourceResult(
                    title=f"Reminders: {list_name}",
                    content=f"Apple Reminders list '{list_name}'.",
                    source_url="x-apple-reminderkit://",
                    source_name="Apple Reminders",
                    confidence=0.5,
                ),
            )
        return out
