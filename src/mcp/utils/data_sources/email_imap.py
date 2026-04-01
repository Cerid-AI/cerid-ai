# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""IMAP email poller data source — polls a mailbox for new messages and ingests them.

Also includes an Apple Mail .emlx file reader for one-shot local import.

Dependencies: stdlib imaplib + email (no external packages).
"""
from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import json
import logging
import os
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .base import DataSource, DataSourceResult

logger = logging.getLogger("ai-companion.data_sources.email")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_header_value(value: str | None) -> str:
    """Decode RFC 2047 encoded header into a plain string."""
    if not value:
        return ""
    parts: list[str] = []
    for fragment, charset in decode_header(value):
        if isinstance(fragment, bytes):
            parts.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(fragment)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Sender-domain heuristic categorization
# ---------------------------------------------------------------------------

_NEWSLETTER_DOMAINS = frozenset({
    "substack.com", "mailchimp.com", "convertkit.com", "beehiiv.com",
    "buttondown.email", "revue.email", "ghost.io", "campaign-archive.com",
    "sendgrid.net", "constantcontact.com",
})

_RECEIPT_DOMAINS = frozenset({
    "amazon.com", "apple.com", "paypal.com", "stripe.com", "square.com",
    "shopify.com", "ebay.com", "doordash.com", "uber.com", "lyft.com",
    "grubhub.com", "instacart.com",
})

_WORK_DOMAINS = frozenset({
    "slack.com", "atlassian.com", "jira.com", "notion.so", "linear.app",
    "github.com", "gitlab.com", "asana.com", "monday.com", "zoom.us",
    "confluence.com", "teams.microsoft.com",
})

_DOMAIN_RE = re.compile(r"@([\w.-]+)")


def _categorize_sender(from_addr: str) -> str:
    """Auto-categorize email by sender domain heuristics.

    Returns one of: personal, work, newsletters, receipts
    """
    match = _DOMAIN_RE.search(from_addr.lower())
    if not match:
        return "personal"
    domain = match.group(1)
    # Check domain and parent domain (e.g. mail.substack.com -> substack.com)
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        check = ".".join(parts[i:])
        if check in _NEWSLETTER_DOMAINS:
            return "newsletters"
        if check in _RECEIPT_DOMAINS:
            return "receipts"
        if check in _WORK_DOMAINS:
            return "work"
    return "personal"


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Crude HTML tag stripping for email bodies without external deps."""
    text = _TAG_RE.sub("", html)
    # Collapse whitespace runs
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body from an email message, preferring text/plain."""
    if msg.is_multipart():
        plain: str | None = None
        html: str | None = None
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    plain = payload.decode(charset, errors="replace")
            elif ct == "text/html" and html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
        if plain:
            return plain.strip()
        if html:
            return _strip_html(html)
        return ""
    # Not multipart
    ct = msg.get_content_type()
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    text = payload.decode(charset, errors="replace")
    if ct == "text/html":
        return _strip_html(text)
    return text.strip()


def _list_attachments(msg: email.message.Message) -> list[str]:
    """Return filenames of all attachments (without downloading them)."""
    names: list[str] = []
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if "attachment" in cd:
            fn = part.get_filename()
            if fn:
                names.append(_decode_header_value(fn))
    return names


def _parse_email_message(msg: email.message.Message) -> dict[str, Any]:
    """Extract structured fields from a parsed email.message.Message."""
    subject = _decode_header_value(msg.get("Subject"))
    from_addr = _decode_header_value(msg.get("From"))
    to_addr = _decode_header_value(msg.get("To"))
    date_str = msg.get("Date", "")
    message_id = msg.get("Message-ID", "")

    # Parse date
    date_iso = ""
    try:
        dt = parsedate_to_datetime(date_str)
        date_iso = dt.isoformat()
    except (ValueError, TypeError):
        date_iso = date_str

    body = _extract_body(msg)
    attachments = _list_attachments(msg)

    return {
        "subject": subject,
        "from": from_addr,
        "to": to_addr,
        "date": date_iso,
        "message_id": message_id,
        "body": body,
        "attachments": attachments,
        "category": _categorize_sender(from_addr),
    }


# ---------------------------------------------------------------------------
# IMAP polling (sync, wrapped in asyncio.to_thread)
# ---------------------------------------------------------------------------


def _imap_fetch_unseen(
    host: str,
    port: int,
    user: str,
    password: str,
    folder: str,
    last_uid: str | None,
) -> list[dict[str, Any]]:
    """Connect via IMAP4_SSL, fetch UNSEEN messages, return parsed dicts.

    This is a synchronous function — call via ``asyncio.to_thread()``.
    """
    conn = imaplib.IMAP4_SSL(host, port)
    try:
        conn.login(user, password)
        status, _data = conn.select(folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Failed to select folder '{folder}': {status}")

        # Build search criteria
        criteria = "(UNSEEN)"
        if last_uid:
            criteria = f"(UNSEEN UID {int(last_uid) + 1}:*)"

        # Use UID SEARCH
        status, msg_nums = conn.uid("search", None, criteria)
        if status != "OK" or not msg_nums or not msg_nums[0]:
            return []

        uids = msg_nums[0].split()
        results: list[dict[str, Any]] = []
        for uid_bytes in uids:
            uid_str = uid_bytes.decode() if isinstance(uid_bytes, bytes) else str(uid_bytes)
            # Skip UIDs we already processed (belt-and-suspenders with Redis set)
            if last_uid and int(uid_str) <= int(last_uid):
                continue

            status, msg_data = conn.uid("fetch", uid_str, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            if isinstance(raw, bytes):
                msg = email.message_from_bytes(raw, policy=email.policy.default)
            else:
                continue

            parsed = _parse_email_message(msg)
            parsed["uid"] = uid_str
            results.append(parsed)

        return results
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


# ---------------------------------------------------------------------------
# Apple Mail .emlx reader
# ---------------------------------------------------------------------------


def parse_emlx_file(path: str | Path) -> dict[str, Any]:
    """Parse an Apple Mail .emlx file.

    .emlx format:
      - Line 1: byte count of the RFC 2822 message
      - Lines 2..N: raw RFC 2822 message (``byte_count`` bytes)
      - Remaining: Apple plist metadata (ignored)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"emlx file not found: {path}")
    if not p.suffix == ".emlx":
        raise ValueError(f"Not an .emlx file: {path}")

    raw = p.read_bytes()
    # First line is the byte count
    newline_idx = raw.index(b"\n")
    byte_count = int(raw[:newline_idx].strip())
    # RFC 2822 message starts after the first newline
    message_bytes = raw[newline_idx + 1 : newline_idx + 1 + byte_count]

    msg = email.message_from_bytes(message_bytes, policy=email.policy.default)
    parsed = _parse_email_message(msg)
    parsed["source_file"] = str(p)
    return parsed


# ---------------------------------------------------------------------------
# DataSource subclass
# ---------------------------------------------------------------------------


class EmailImapSource(DataSource):
    """IMAP email poller — fetches unseen messages and ingests them into the KB."""

    name = "email-imap"
    description = "IMAP email poller — ingests new emails from a configured mailbox."
    requires_api_key = False  # configured via Redis, not env var API key
    domains: list[str] = ["email"]

    def is_configured(self) -> bool:
        """Check if IMAP credentials are available (env vars or Redis config)."""
        return bool(os.getenv("CERID_EMAIL_IMAP_HOST"))

    async def query(self, query: str, **kwargs: Any) -> list[DataSourceResult]:
        """Not used for email — email is polled, not queried on-demand."""
        return []


# ---------------------------------------------------------------------------
# Core polling function
# ---------------------------------------------------------------------------


async def poll_email() -> dict[str, Any]:
    """Poll the configured IMAP mailbox for new messages and ingest them.

    Returns a summary dict with counts and any errors.
    """
    from utils.circuit_breaker import CircuitOpenError, get_breaker

    # Load config — prefer Redis, fall back to env vars
    config = await _load_email_config()
    if not config.get("host"):
        return {"status": "not_configured", "messages": 0}

    breaker = get_breaker("email-imap")

    try:

        async def _do_poll() -> list[dict[str, Any]]:
            return await asyncio.to_thread(
                _imap_fetch_unseen,
                config["host"],
                config["port"],
                config["user"],
                config["password"],
                config["folder"],
                config.get("last_uid"),
            )

        messages = await breaker.call(_do_poll)
    except CircuitOpenError:
        logger.warning("Email IMAP circuit breaker is open — skipping poll")
        return {"status": "circuit_open", "messages": 0}
    except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
        logger.error("IMAP poll failed: %s", exc)
        return {"status": "error", "error": str(exc), "messages": 0}

    if not messages:
        await _update_poll_status(0)
        return {"status": "ok", "messages": 0}

    # Ingest each message
    ingested = 0
    errors: list[str] = []
    from services.ingestion import ingest_content

    for msg_data in messages:
        try:
            uid = msg_data["uid"]
            # Skip already-processed UIDs
            if await _is_uid_processed(uid):
                continue

            # Build content for ingestion
            content = _format_email_for_ingestion(msg_data)
            metadata = {
                "source": "email-imap",
                "email_subject": msg_data["subject"],
                "email_from": msg_data["from"],
                "email_to": msg_data["to"],
                "email_date": msg_data["date"],
                "email_message_id": msg_data["message_id"],
                "sub_category": msg_data.get("category", "personal"),
                "filename": f"email_{uid}.eml",
            }
            if msg_data["attachments"]:
                metadata["email_attachments"] = json.dumps(msg_data["attachments"])

            ingest_content(content, domain="email", metadata=metadata)
            await _mark_uid_processed(uid)
            ingested += 1

        except (ValueError, OSError, RuntimeError) as exc:
            errors.append(f"UID {msg_data.get('uid', '?')}: {exc}")
            logger.error("Failed to ingest email UID %s: %s", msg_data.get("uid"), exc)

    # Update last UID watermark
    if messages:
        max_uid = max(int(m["uid"]) for m in messages)
        await _set_last_uid(str(max_uid))

    await _update_poll_status(ingested, errors)

    return {
        "status": "ok",
        "messages": ingested,
        "errors": errors if errors else None,
    }


# ---------------------------------------------------------------------------
# .emlx import
# ---------------------------------------------------------------------------


async def import_emlx(path: str) -> dict[str, Any]:
    """Import a single .emlx file into the KB."""
    from services.ingestion import ingest_content

    parsed = await asyncio.to_thread(parse_emlx_file, path)
    content = _format_email_for_ingestion(parsed)
    metadata = {
        "source": "email-emlx",
        "email_subject": parsed["subject"],
        "email_from": parsed["from"],
        "email_to": parsed["to"],
        "email_date": parsed["date"],
        "email_message_id": parsed.get("message_id", ""),
        "filename": Path(path).name,
        "source_file": parsed.get("source_file", path),
    }
    if parsed["attachments"]:
        metadata["email_attachments"] = json.dumps(parsed["attachments"])

    result = ingest_content(content, domain="email", metadata=metadata)
    return {"status": "ok", "file": path, "ingestion": result}


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

_REDIS_CONFIG_KEY = "cerid:email:config"
_REDIS_LAST_UID_KEY = "cerid:email:last_uid"
_REDIS_PROCESSED_KEY = "cerid:email:processed"
_REDIS_STATUS_KEY = "cerid:email:status"


async def _load_email_config() -> dict[str, Any]:
    """Load IMAP config from Redis, falling back to env vars."""
    try:
        from deps import get_redis
        r = get_redis()
        stored = r.get(_REDIS_CONFIG_KEY)
        if stored:
            cfg = json.loads(stored)
            # Also load the last UID watermark
            cfg["last_uid"] = r.get(_REDIS_LAST_UID_KEY)
            return cfg
    except (OSError, RuntimeError):
        pass

    # Fall back to env vars
    host = os.getenv("CERID_EMAIL_IMAP_HOST", "")
    if not host:
        return {}
    return {
        "host": host,
        "port": int(os.getenv("CERID_EMAIL_IMAP_PORT", "993")),
        "user": os.getenv("CERID_EMAIL_IMAP_USER", ""),
        "password": os.getenv("CERID_EMAIL_IMAP_PASSWORD", ""),
        "folder": os.getenv("CERID_EMAIL_FOLDER", "INBOX"),
        "poll_interval": int(os.getenv("CERID_EMAIL_POLL_INTERVAL", "15")),
        "last_uid": None,
    }


async def validate_imap_connection(config_data: dict[str, Any]) -> bool:
    """Test IMAP connectivity with the given config. Raises on failure."""

    def _test() -> bool:
        conn = imaplib.IMAP4_SSL(config_data["host"], config_data.get("port", 993))
        try:
            conn.login(config_data["user"], config_data["password"])
            status, _data = conn.select(config_data.get("folder", "INBOX"), readonly=True)
            if status != "OK":
                raise RuntimeError(f"Cannot select folder: {status}")
            return True
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    return await asyncio.to_thread(_test)


async def save_email_config(config_data: dict[str, Any]) -> None:
    """Validate IMAP connectivity, then save config to Redis."""
    # Validate before saving
    await validate_imap_connection(config_data)

    from deps import get_redis
    r = get_redis()
    # Self-hosted privacy-first tool — Redis is on localhost behind auth.
    r.set(_REDIS_CONFIG_KEY, json.dumps(config_data))


async def delete_email_config() -> None:
    """Remove IMAP config and status from Redis."""
    from deps import get_redis
    r = get_redis()
    r.delete(_REDIS_CONFIG_KEY, _REDIS_LAST_UID_KEY, _REDIS_PROCESSED_KEY, _REDIS_STATUS_KEY)


async def get_email_status() -> dict[str, Any]:
    """Return current email polling status from Redis."""
    try:
        from deps import get_redis
        r = get_redis()
        raw = r.get(_REDIS_STATUS_KEY)
        if raw:
            return json.loads(raw)
    except (OSError, RuntimeError):
        pass
    return {"last_poll": None, "messages_ingested": 0, "errors": []}


async def _set_last_uid(uid: str) -> None:
    from deps import get_redis
    r = get_redis()
    r.set(_REDIS_LAST_UID_KEY, uid)


async def _is_uid_processed(uid: str) -> bool:
    try:
        from deps import get_redis
        r = get_redis()
        return r.sismember(_REDIS_PROCESSED_KEY, uid)
    except (OSError, RuntimeError):
        return False


async def _mark_uid_processed(uid: str) -> None:
    from deps import get_redis
    r = get_redis()
    r.sadd(_REDIS_PROCESSED_KEY, uid)


async def _update_poll_status(count: int, errors: list[str] | None = None) -> None:
    from deps import get_redis
    r = get_redis()
    existing = {}
    raw = r.get(_REDIS_STATUS_KEY)
    if raw:
        existing = json.loads(raw)
    existing["last_poll"] = datetime.now(timezone.utc).isoformat()
    existing["messages_ingested"] = existing.get("messages_ingested", 0) + count
    if errors:
        existing["last_errors"] = errors
    r.set(_REDIS_STATUS_KEY, json.dumps(existing))


# ---------------------------------------------------------------------------
# Content formatting
# ---------------------------------------------------------------------------


def _format_email_for_ingestion(msg_data: dict[str, Any]) -> str:
    """Format a parsed email dict into a text block suitable for KB ingestion."""
    parts = [
        f"Subject: {msg_data['subject']}",
        f"From: {msg_data['from']}",
        f"To: {msg_data['to']}",
        f"Date: {msg_data['date']}",
    ]
    if msg_data.get("attachments"):
        parts.append(f"Attachments: {', '.join(msg_data['attachments'])}")
    parts.append("")
    parts.append(msg_data.get("body", ""))
    return "\n".join(parts)
