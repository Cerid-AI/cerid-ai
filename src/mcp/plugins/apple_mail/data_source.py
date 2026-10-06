# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Apple Mail DataSource — Phase 4.1.

Wraps the ``ceridmail`` Swift helper via subprocess + JSON-over-stdout, mirroring
the AppleCalendarDataSource pattern. The helper is an ``.emlx`` walker over
``~/Library/Mail``; it inherits TCC grants from the Electron parent app's signed
bundle (Mail requires **Full Disk Access**, a System Settings toggle — not a
per-app usage-description key).

Helper contract (``packages/desktop/swift/CeridMail``):
  - ``ceridmail scan`` → account and message counts. Health check only.
  - ``ceridmail since <iso8601>`` → ``{"ok": bool, "messages": [{"id", "date",
    "subject", "from", "to", "mailbox", "body"}]}``. Optional ``list_id``,
    ``list_unsubscribe``, ``authentication_results``, ``spam_flag``,
    ``spam_status``, ``rfc_message_id``, ``in_reply_to``, and ``references``
    are copied onto the row when the helper sends them. ``to`` and ``date``
    are copied when they are non-empty.
    ``query`` uses this.
  - Full-Disk-Access denial → **exit code 77** (parity with CeridReminders).
  - Writes use Mail.app Apple events, by Message-ID: ``flag``, ``read``,
    ``move``, ``draft``, ``find``. Exit 75 when Mail is not running (the
    helper does not launch it). Exit 78 when Automation is denied.

Junk and Trash are dropped. Message-ID and mailbox travel on each row so a
later apply can find the message. ``scan`` stays the count check.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.apple_mail")

DEFAULT_HELPER_NAME = "ceridmail"
_TCC_DENIED_CODES = (3, 77)  # CeridMail exits 77 on Full-Disk-Access denial; 3 kept for parity
_SKIPPED_MAILBOX = re.compile(
    r"(^|/)(junk|junk email|junk e-mail|spam|trash|deleted messages|deleted items)(/|$)",
    re.IGNORECASE,
)


def _provider_junk(mailbox: str) -> bool:
    """Provider Junk and Trash. A Cerid/Spam sort folder is still readable."""
    text = mailbox.strip()
    folded = text.casefold()
    if folded.startswith("cerid/") or "/cerid/" in folded:
        return False
    return bool(_SKIPPED_MAILBOX.search(text))
_ISO_CURSOR = re.compile(r"^\d{4}-\d{2}-\d{2}T")


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

    async def invoke(self, args: list[str]) -> tuple[int, dict]:
        """Run a ceridmail subcommand and keep the exit code.

        ``query`` swallows a non-zero exit so one source cannot break a
        search. Apply cannot: Mail not running and an Automation denial are
        different outcomes, and both are non-zero.
        """
        if not self._helper_path:
            return 74, {"ok": False, "error": "helper_missing"}
        try:
            proc = await asyncio.create_subprocess_exec(
                self._helper_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except (TimeoutError, OSError) as exc:
            log_swallowed_error("apple_mail.invoke", exc)
            return 74, {"ok": False, "error": "io"}
        payload: dict[str, Any] = {}
        text = stdout.decode("utf-8", errors="replace").strip()
        if text:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
        code = int(proc.returncode or 0)
        if code == 0 and not payload:
            return 74, {"ok": False, "error": "io"}
        return code, payload

    async def apply(self, decision: dict, *, dry_run: bool = True, ledger: Any = None) -> dict:
        """Apply one Apple Mail decision. Dry-run does not spawn the helper."""
        from app.inbox.executor import apply_decision

        class _Transport:
            async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
                raise RuntimeError("apple mail has no sibling tool")

            async def apple(self, argv: list[str]) -> tuple[int, dict]:
                return await self_source.invoke(argv)

        self_source = self
        prepared = dict(decision)
        prepared.setdefault("provider", "apple_mail")
        return await apply_decision(prepared, dry_run=dry_run, ledger=ledger, transport=_Transport())

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        """Recent mail via ``ceridmail since``. ``query`` is not Gmail syntax.

        ``kwargs['since']`` is an ISO-8601 cursor. The default is one day ago,
        which matches the triage window. ``scan`` is not the read path.
        """
        cursor = kwargs.get("since")
        if not isinstance(cursor, str) or not cursor.strip():
            # An ISO string is a cursor. Gmail search syntax is not, and the
            # triage default ("is:unread newer_than:1d") means the last day.
            stripped = query.strip()
            cursor = stripped if _ISO_CURSOR.match(stripped) else ""
        if not cursor:
            cursor = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = await self._invoke_helper(["since", cursor])
        if not isinstance(raw, dict) or not raw.get("ok"):
            return []
        messages = raw.get("messages", [])
        if not isinstance(messages, list):
            return []
        max_results = int(kwargs.get("max_results", 25))
        out: list[DataSourceResult] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if len(out) >= max_results:
                break
            mailbox = str(msg.get("mailbox") or "")
            if mailbox and _provider_junk(mailbox):
                continue
            subject = str(msg.get("subject") or "(no subject)")
            sender = str(msg.get("from") or msg.get("sender") or "unknown")
            date = str(msg.get("date") or "")
            body = str(msg.get("body") or "")
            message_id = str(msg.get("id") or "")
            lines = [f"From: {sender}", f"Subject: {subject}"]
            if mailbox:
                lines.append(f"Mailbox: {mailbox}")
            if date:
                lines.append(f"Date: {date}")
            if body:
                lines.extend(["", body])
            metadata: dict[str, str] = {}
            if message_id:
                metadata["provider_message_id"] = message_id
                metadata["provider_thread_id"] = message_id
                metadata["message_id"] = message_id
            if mailbox:
                metadata["mailbox"] = mailbox
            for source_key, dest in (
                ("list_id", "list_id"),
                ("list_unsubscribe", "list_unsubscribe"),
                ("authentication_results", "authentication_results"),
                ("spam_flag", "spam_flag"),
                ("spam_status", "spam_status"),
                ("labels", "labels"),
                ("rfc_message_id", "rfc_message_id"),
                ("to", "to"),
                ("date", "date"),
                ("in_reply_to", "in_reply_to"),
                ("references", "references"),
            ):
                value = str(msg.get(source_key) or "").strip()
                if value:
                    metadata[dest] = value
            out.append(
                DataSourceResult(
                    title=f"Mail: {subject}",
                    content="\n".join(lines),
                    source_url=f"message://{message_id}" if message_id else "message://",
                    source_name="Apple Mail",
                    confidence=0.5,
                    metadata=metadata,
                ),
            )
        return out
