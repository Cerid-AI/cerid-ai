#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Read-only IMAP review. Prints one classification line per message.

Host, user, and password come from CERID_EMAIL_IMAP_HOST,
CERID_EMAIL_IMAP_USER, and CERID_EMAIL_IMAP_PASSWORD. The password is
not stored. CERID_RSPAMD_URL is used only when it is already exported.
CERID_INBOX_REVIEW_REDACT lists substrings that keep a message out of the
printout.
"""
from __future__ import annotations

import argparse
import asyncio
import email
import email.policy
import imaplib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mcp"))

from core.agents.inbox_review import is_sensitive, render_record, review_line, review_message  # noqa: E402
from core.agents.inbox_rspamd import scan_if_configured  # noqa: E402

_BODY_CAP = 24000
_HEADER_FIELDS = {
    "List-Id": "list_id",
    "List-Unsubscribe": "list_unsubscribe",
    "Authentication-Results": "authentication_results",
    "X-Spam-Flag": "spam_flag",
    "X-Spam-Status": "spam_status",
    "In-Reply-To": "in_reply_to",
    "References": "references",
}


def _junkish(mailbox: str) -> bool:
    token = mailbox.casefold().strip()
    head = token.split(" ", 1)[0]
    return head in {"junk", "spam", "trash"} or token.startswith(("junk", "spam", "trash"))


def _plain_body(message: email.message.EmailMessage) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain" or part.get_filename():
                continue
            payload = part.get_content()
            if isinstance(payload, str):
                return payload
        return ""
    payload = message.get_content()
    return payload if isinstance(payload, str) else ""


def _headers(message: email.message.EmailMessage) -> dict[str, str]:
    bag: dict[str, str] = {}
    for source, dest in _HEADER_FIELDS.items():
        value = str(message.get(source) or "").strip()
        if value:
            bag[dest] = value
    return bag


def _search(client: imaplib.IMAP4_SSL, criteria: str) -> list[bytes]:
    status, data = client.search(None, criteria)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fetch(client: imaplib.IMAP4_SSL, number: bytes) -> bytes:
    status, data = client.fetch(number, "(BODY.PEEK[])")
    if status != "OK" or not data:
        return b""
    for item in data:
        if isinstance(item, tuple) and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return b""


def _require_env() -> tuple[str, str, str]:
    import os

    host = os.environ.get("CERID_EMAIL_IMAP_HOST", "").strip()
    user = os.environ.get("CERID_EMAIL_IMAP_USER", "").strip()
    password = os.environ.get("CERID_EMAIL_IMAP_PASSWORD", "").strip()
    missing = [
        name
        for name, value in (
            ("CERID_EMAIL_IMAP_HOST", host),
            ("CERID_EMAIL_IMAP_USER", user),
            ("CERID_EMAIL_IMAP_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        print("missing " + ", ".join(missing), file=sys.stderr)
        raise SystemExit(2)
    return host, user, password


def _print_fixture(path: str, mailbox: str) -> int:
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        print("fixture must be a JSON list", file=sys.stderr)
        return 2
    skipped = 0
    shown = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        line = render_record(record, mailbox=mailbox)
        if line is None:
            skipped += 1
            continue
        print(line)
        shown += 1
    print(f"shown={shown} skipped={skipped}", file=sys.stderr)
    return 0


async def _classify(raw: bytes, mailbox: str) -> str | None:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    sender = str(message.get("From") or "")
    recipient = str(message.get("To") or "")
    subject = str(message.get("Subject") or "")
    if is_sensitive(sender, recipient, subject):
        return None
    payload = await scan_if_configured(raw)
    decision = review_message(
        sender=sender,
        subject=subject,
        body=_plain_body(message)[:_BODY_CAP],
        headers=_headers(message),
        rspamd=payload if isinstance(payload, dict) else None,
    )
    return review_line(mailbox, decision, sender=sender, recipient=recipient, subject=subject)


def _review_mailbox(mailbox: str, *, unseen: bool, newest: int) -> int:
    host, user, password = _require_env()
    client = imaplib.IMAP4_SSL(host)
    try:
        try:
            client.login(user, password)
        except imaplib.IMAP4.error:
            print("IMAP login failed", file=sys.stderr)
            return 1
        status, _selected = client.select(mailbox, readonly=True)
        if status != "OK":
            print("mailbox could not be selected", file=sys.stderr)
            return 1
        before = _search(client, "UNSEEN")
        numbers = before if unseen else _search(client, "ALL")
        if newest:
            numbers = numbers[-newest:]
        shown = 0
        skipped = 0
        for number in numbers:
            raw = _fetch(client, number)
            if not raw:
                continue
            line = asyncio.run(_classify(raw, mailbox))
            if line is None:
                skipped += 1
                continue
            print(line)
            shown += 1
        after = _search(client, "UNSEEN")
    finally:
        try:
            client.logout()
        except imaplib.IMAP4.error:
            pass
    print(
        f"shown={shown} skipped={skipped} unseen_before={len(before)} unseen_after={len(after)}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only mail review")
    parser.add_argument("--mailbox", default="INBOX")
    parser.add_argument("--unseen", action="store_true")
    parser.add_argument("--newest", type=int, default=0)
    parser.add_argument("--allow-junk", action="store_true")
    parser.add_argument("--fixture", default="")
    args = parser.parse_args(argv)
    if _junkish(args.mailbox) and not args.allow_junk:
        print("Junk, Spam, and Trash need --allow-junk", file=sys.stderr)
        return 2
    if args.fixture:
        return _print_fixture(args.fixture, args.mailbox)
    if not args.unseen and not args.newest:
        print("pass --unseen, --newest, or --fixture", file=sys.stderr)
        return 2
    if args.newest < 0:
        print("--newest must be positive", file=sys.stderr)
        return 2
    return _review_mailbox(args.mailbox, unseen=args.unseen, newest=args.newest)


if __name__ == "__main__":
    raise SystemExit(main())
