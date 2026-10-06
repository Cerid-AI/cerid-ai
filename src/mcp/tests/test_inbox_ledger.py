# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Inbox ledger schema and the open-proposal rule."""
from __future__ import annotations

from pathlib import Path

from app.inbox.ledger import InboxLedger


def test_account_roundtrip_defaults_folder_sort_off(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    ledger.upsert_account(provider="gmail", address="a@example.com", display_name="A")
    account = ledger.get_account("gmail", "a@example.com")
    assert account is not None
    assert account["folder_sort"] is False
    assert account["included"] is True
    assert account["utilities"] == ["correspondence", "financial"]
    assert account["auto_apply"] == []
    assert account["consent"] == "readonly"
    assert account["removed"] is False
    assert account["last_apply"] == ""
    assert account["last_rejection"] == ""


def test_new_proposal_supersedes_the_open_one(tmp_path: Path):
    ledger = InboxLedger(tmp_path / "inbox.sqlite")
    first = ledger.propose(
        account_address="a@example.com",
        source="gmail",
        provider_thread_id="t1",
        category="promo",
        utility="none",
        action="archive",
        band="local-small",
    )
    second = ledger.propose(
        account_address="a@example.com",
        source="gmail",
        provider_thread_id="t1",
        category="actionable",
        utility="correspondence",
        action="keep",
        band="local-chat",
    )
    assert ledger.get_decision(first)["status"] == "superseded"
    assert ledger.open_proposal("gmail", "t1")["id"] == second
    assert ledger.get_decision(second)["created"]


def test_migrate_adds_account_columns_on_an_existing_file(tmp_path: Path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE accounts (
            provider TEXT NOT NULL,
            address TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            included INTEGER NOT NULL DEFAULT 1,
            folder_sort INTEGER NOT NULL DEFAULT 0,
            auto_apply TEXT NOT NULL DEFAULT '[]',
            utilities TEXT NOT NULL DEFAULT '["correspondence","financial"]',
            consent TEXT NOT NULL DEFAULT 'readonly',
            PRIMARY KEY (provider, address)
        )
        """,
    )
    conn.execute("INSERT INTO accounts (provider, address) VALUES ('gmail', 'a@example.com')")
    conn.commit()
    conn.close()

    ledger = InboxLedger(path)
    account = ledger.get_account("gmail", "a@example.com")
    assert account is not None
    assert account["removed"] is False
    assert account["last_read"] == ""
    assert account["included"] is True
