# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Apple iMessage DataSource — Phase 4.2.

.. warning::

   **This path is inert: the ``ceridimessage`` helper does not exist.**
   ``packages/desktop/swift/`` contains five packages — EventKit, Photos,
   Spotlight, Reminders, Mail — and no iMessage target has ever been written,
   so the binary is never built by ``make``, never bundled into the .app, and
   ``is_configured()`` cannot return True on any machine. The manifest claimed
   it under ``requires.swift_helpers`` until 2026-08-10; nothing reads that
   field, so the claim could not drift loudly.

   iMessage still works for users, by a different route entirely: it is a
   *bridge* kind (``source-rows.ts :: APPLE_BRIDGE_KINDS``) and the desktop
   app reads ``chat.db`` directly through better-sqlite3 in
   ``packages/desktop/src/main/connectors/imessage.ts`` — no Swift helper
   involved. That is the implementation; this module is a vestige of a
   REST-side design that was never finished.

   **Operator decision:** write ``CeridIMessage`` (and add it to ``HELPERS``
   in the swift ``Makefile``), or delete this plugin. Leaving it costs a
   permanently-unconfigurable data source. ``scripts/lint-swift-helper-manifests.py``
   now blocks any *new* instance of the same claim.

Wraps a Swift helper via subprocess + JSON-over-stdout, mirroring the
AppleMailDataSource pattern. The helper would read the local iMessage SQLite
store (``~/Library/Messages/chat.db``); reading it requires **Full Disk
Access** for the Cerid desktop app (a System Settings toggle).

**Privacy gate:** iMessage content is the most sensitive Apple surface, so the
connector additionally requires the dedicated **SENSITIVE_DOMAIN_RETRIEVAL_ENABLED**
opt-in (``docs/PRO_MESSAGES.md``), independent of the private-mode isolation
level. When the opt-in is off (the default) the connector returns nothing and
never spawns the helper — no chat.db access at all. This complements
``utils.domain_privacy`` (which gates the ingested ``messages`` domain in
retrieval); this check guards the live data-source path.

Helper contract (``packages/desktop/swift/CeridIMessage``, **not yet written**):
  - ``ceridimessage scan`` → ``{"ok": bool, "message_count": int,
    "messages": [{"text": str, "sender": str, "date": str, "chat": str}]}``
  - Full-Disk-Access denial → **exit code 77** (parity with CeridMail).
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

logger = logging.getLogger("ai-companion.data_sources.apple_imessage")

DEFAULT_HELPER_NAME = "ceridimessage"
_TCC_DENIED_CODES = (3, 77)  # CeridIMessage exits 77 on Full-Disk-Access denial; 3 kept for parity


def _resolve_helper_path() -> str | None:
    override = os.getenv("CERID_HELPER_CERIDIMESSAGE")
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


class AppleIMessageDataSource(DataSource):
    name = "apple_imessage"
    description = "Apple iMessage via Swift chat.db reader (sensitive-domain opt-in required)"
    requires_api_key = False  # TCC-gated (Full Disk Access) + sensitive-domain-opt-in-gated

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
                    logger.info("ceridimessage TCC denied: %s", stderr_text)
                else:
                    logger.warning("ceridimessage exited %d: %s", proc.returncode, stderr_text)
                return None
            return json.loads(stdout.decode("utf-8"))
        except (TimeoutError, OSError, ValueError) as exc:
            log_swallowed_error("apple_imessage._invoke_helper", exc)
            return None

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        # Privacy gate: never touch chat.db unless the dedicated
        # SENSITIVE_DOMAIN_RETRIEVAL_ENABLED opt-in is on. Fail-closed
        # (defaults to suppressed on any config-read error).
        from utils.domain_privacy import sensitive_domains_opted_in

        if not sensitive_domains_opted_in():
            logger.info("apple_imessage suppressed — sensitive-domain retrieval opt-in is off")
            return []

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
            text = str(msg.get("text", ""))
            sender = str(msg.get("sender", "unknown"))
            chat = str(msg.get("chat", ""))
            date = str(msg.get("date", ""))
            out.append(
                DataSourceResult(
                    title=f"iMessage with {chat}".strip(),
                    content=f"{text}\n— {sender} · {date}".strip(" ·\n"),
                    source_url="imessage://",
                    source_name="iMessage",
                    confidence=0.5,
                ),
            )
        return out
