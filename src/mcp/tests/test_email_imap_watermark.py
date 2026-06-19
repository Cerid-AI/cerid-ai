# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""IMAP poller dedup is driven by the processed-UID set, not a UID high-water
mark. Regression guard: a message that was \\Seen at first poll and later
flagged unread (so its UID sits below higher, already-processed UIDs) must
still be fetched — the old `(UNSEEN UID floor:*)` criteria dropped it forever."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.data_sources import email_imap

_RAW = (
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: Hello\r\n"
    b"Date: Mon, 1 Jun 2026 10:00:00 +0000\r\n"
    b"Message-ID: <m1@example.com>\r\n\r\n"
    b"Body text"
)


def _fake_conn(search_uids: bytes) -> MagicMock:
    conn = MagicMock()
    conn.login.return_value = ("OK", [b""])
    conn.select.return_value = ("OK", [b"1"])

    def _uid(cmd, *args):
        if cmd == "search":
            return ("OK", [search_uids])
        if cmd == "fetch":
            return ("OK", [(b"x (RFC822 {n}", _RAW)])
        return ("OK", [b""])

    conn.uid.side_effect = _uid
    return conn


def _fetch(conn: MagicMock, processed: set[str]) -> list[dict]:
    with patch.object(email_imap.imaplib, "IMAP4_SSL", return_value=conn):
        return email_imap._imap_fetch_unseen("h", 993, "u", "p", "INBOX", processed)


def _search_criteria(conn: MagicMock) -> list[str]:
    return [c.args[2] for c in conn.uid.call_args_list if c.args[0] == "search"]


def _fetched_uids(conn: MagicMock) -> set[str]:
    return {c.args[1] for c in conn.uid.call_args_list if c.args[0] == "fetch"}


def test_search_uses_plain_unseen_with_no_uid_floor() -> None:
    conn = _fake_conn(b"95 100 200")
    _fetch(conn, processed={"100"})
    crit = _search_criteria(conn)
    assert crit == ["(UNSEEN)"]
    assert "UID" not in crit[0]  # no high-water-mark floor


def test_skips_processed_uids_before_fetch() -> None:
    conn = _fake_conn(b"95 100 200")
    results = _fetch(conn, processed={"100"})
    assert {r["uid"] for r in results} == {"95", "200"}
    # The processed UID is never fetched (skipped before the costly RFC822 read).
    assert _fetched_uids(conn) == {"95", "200"}


def test_low_unprocessed_uid_below_higher_processed_is_still_fetched() -> None:
    # 200 already ingested; 95 was \Seen-then-unread and never processed.
    conn = _fake_conn(b"95 200")
    results = _fetch(conn, processed={"200"})
    assert {r["uid"] for r in results} == {"95"}


def test_all_processed_yields_nothing_fetched() -> None:
    conn = _fake_conn(b"95 100")
    results = _fetch(conn, processed={"95", "100"})
    assert results == []
    assert _fetched_uids(conn) == set()
