# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Email parsers — .eml and .mbox formats."""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import Any

import config as _config
from app.parsers._utils import _strip_html_tags
from app.parsers.registry import _MAX_TEXT_CHARS, register_parser

_ANONYMIZE_KEYS = {"From", "To", "Cc"}


def _anonymize_header(value: str) -> str:
    """Replace email addresses with redacted form, preserving domain for context."""
    if not _config.ANONYMIZE_EMAIL_HEADERS:
        return value
    return _re.sub(
        r"[a-zA-Z0-9._%+\-]+@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
        r"[redacted]@\1",
        value,
    )


@register_parser([".eml"])
def parse_eml(file_path: str) -> dict[str, Any]:
    """Parse .eml — headers, body (prefers text/plain), attachment list."""
    import email
    import email.policy
    from email import message_from_bytes

    path = Path(file_path)
    raw = path.read_bytes()

    try:
        msg = message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        raise ValueError(
            f"Failed to parse email '{path.name}': {e}. "
            f"File may not be a valid .eml file."
        ) from e

    headers = {}
    for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID"):
        val = msg.get(key, "")
        if val:
            headers[key] = _anonymize_header(str(val)) if key in _ANONYMIZE_KEYS else str(val)

    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())

    body = ""
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                att_name = part.get_filename() or "(unnamed)"
                att_size = len(part.get_payload(decode=True) or b"")
                attachments.append(f"{att_name} ({att_size} bytes)")
                continue

            if content_type == "text/plain" and not body:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    html = payload.decode("utf-8", errors="replace")
                    body = _strip_html_tags(html)
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            raw_text = payload.decode("utf-8", errors="replace")
            if content_type == "text/html":
                body = _strip_html_tags(raw_text)
            else:
                body = raw_text

    parts = [header_text]
    if body:
        parts.append(f"\n--- Body ---\n{body.strip()}")
    if attachments:
        parts.append(f"\n--- Attachments ({len(attachments)}) ---\n" + "\n".join(attachments))

    text = "\n".join(parts)

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "eml",
        "page_count": None,
        "attachment_count": len(attachments),
        "subject": headers.get("Subject", ""),
    }


@register_parser([".mbox"])
def parse_mbox(file_path: str) -> dict[str, Any]:
    """Parse .mbox — extract messages as sections (max 100)."""
    import mailbox

    path = Path(file_path)
    try:
        mbox = mailbox.mbox(file_path)
    except Exception as e:
        raise ValueError(
            f"Failed to parse mbox '{path.name}': {e}. "
            f"File may not be a valid .mbox file."
        ) from e

    messages: list[str] = []
    max_messages = 100
    total_count = 0

    for msg in mbox:
        total_count += 1
        if len(messages) >= max_messages:
            continue  # count but don't extract

        subject = msg.get("Subject", "(no subject)")
        from_addr = _anonymize_header(msg.get("From", ""))
        date = msg.get("Date", "")

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body = payload.decode("utf-8", errors="replace")
                        break
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                body = payload.decode("utf-8", errors="replace")

        header = f"From: {from_addr}\nDate: {date}\nSubject: {subject}"
        messages.append(f"{header}\n\n{body.strip()}")

    mbox.close()

    sep = "\n\n" + "=" * 60 + "\n\n"
    text = sep.join(messages)
    if total_count > max_messages:
        text += f"\n\n[... {total_count - max_messages} more messages truncated ...]"

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "mbox",
        "page_count": total_count,
    }
