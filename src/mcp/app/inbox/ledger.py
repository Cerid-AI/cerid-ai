# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""SQLite ledger for inbox accounts, rules, sender memory, and decisions.

The knowledge base is not this store. Rows here are the operational record.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.agents.inbox_actions import PIN_HITS, PROVIDER_TOOLS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    provider TEXT NOT NULL,
    address TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    included INTEGER NOT NULL DEFAULT 1,
    folder_sort INTEGER NOT NULL DEFAULT 0,
    auto_apply TEXT NOT NULL DEFAULT '[]',
    consent TEXT NOT NULL DEFAULT 'readonly',
    removed INTEGER NOT NULL DEFAULT 0,
    last_read TEXT NOT NULL DEFAULT '',
    last_apply TEXT NOT NULL DEFAULT '',
    last_rejection TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (provider, address)
);
CREATE TABLE IF NOT EXISTS sender_memory (
    source TEXT NOT NULL,
    sender TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1,
    pinned INTEGER NOT NULL DEFAULT 0,
    updated TEXT NOT NULL DEFAULT '',
    last_decision TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source, sender)
);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '*',
    condition_json TEXT NOT NULL,
    action TEXT NOT NULL,
    category TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    account_address TEXT NOT NULL,
    source TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    message_ids TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL,
    utility TEXT NOT NULL,
    action TEXT NOT NULL,
    band TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    receipt_json TEXT NOT NULL DEFAULT '{}',
    mailbox_before TEXT NOT NULL DEFAULT '',
    rag_artifact_ids TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    command_json TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_cursors (
    provider TEXT NOT NULL,
    address TEXT NOT NULL,
    cursor TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (provider, address)
);
"""


class InboxLedger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add account columns that CREATE TABLE IF NOT EXISTS will not alter."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
        extras = (
            ("removed", "INTEGER NOT NULL DEFAULT 0"),
            ("last_read", "TEXT NOT NULL DEFAULT ''"),
            ("last_apply", "TEXT NOT NULL DEFAULT ''"),
            ("last_rejection", "TEXT NOT NULL DEFAULT ''"),
        )
        for name, decl in extras:
            if name not in cols:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {decl}")
        # A per-account utilities list was stored and edited but never read.
        if "utilities" in cols:
            conn.execute("ALTER TABLE accounts DROP COLUMN utilities")
        memory_cols = {row[1] for row in conn.execute("PRAGMA table_info(sender_memory)")}
        if "last_decision" not in memory_cols:
            conn.execute(
                "ALTER TABLE sender_memory ADD COLUMN last_decision TEXT NOT NULL DEFAULT ''",
            )

    def upsert_account(
        self,
        *,
        provider: str,
        address: str,
        display_name: str = "",
        included: bool = True,
        folder_sort: bool = False,
        auto_apply: list[str] | None = None,
        consent: str = "readonly",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    provider, address, display_name, included, folder_sort,
                    auto_apply, consent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, address) DO UPDATE SET
                    display_name=excluded.display_name,
                    included=excluded.included,
                    folder_sort=excluded.folder_sort,
                    auto_apply=excluded.auto_apply,
                    consent=excluded.consent
                """,
                (
                    provider,
                    address,
                    display_name,
                    int(included),
                    int(folder_sort),
                    json.dumps(auto_apply or []),
                    consent,
                ),
            )
            conn.commit()

    def get_account(self, provider: str, address: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE provider=? AND address=?",
                (provider, address),
            ).fetchone()
        return _account(row) if row is not None else None

    def propose(self, **fields: object) -> str:
        """Insert a proposal. An open proposal for the same thread is superseded."""
        source = str(fields["source"])
        thread_id = str(fields["provider_thread_id"])
        decision_id = str(fields.get("id") or uuid.uuid4().hex)
        raw_confidence = fields.get("confidence") or 0
        if not isinstance(raw_confidence, (int, float, str)):
            raise TypeError("confidence must be numeric")
        confidence = float(raw_confidence)
        labels = fields.get("current_labels")
        receipt: dict[str, object] = {}
        if isinstance(labels, list):
            receipt["current_labels"] = [str(item) for item in labels]
        draft_body = str(fields.get("draft_body") or "").strip()
        subject = str(fields.get("subject") or "").strip()
        classification_reason = str(fields.get("classification_reason") or "").strip()
        if draft_body:
            receipt["draft_body"] = draft_body
        if subject:
            receipt["subject"] = subject
        if classification_reason:
            receipt["classification_reason"] = classification_reason
        artifacts = fields.get("rag_artifact_ids") or []
        if not isinstance(artifacts, list):
            raise TypeError("rag_artifact_ids must be a list")
        created = str(fields.get("created") or "")
        if not created:
            created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE decisions SET status='superseded'
                WHERE source=? AND provider_thread_id=? AND status='proposed'
                """,
                (source, thread_id),
            )
            conn.execute(
                """
                INSERT INTO decisions (
                    id, account_address, source, provider_thread_id, message_ids,
                    category, utility, action, band, model, confidence, status,
                    receipt_json, mailbox_before, rag_artifact_ids, created
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    fields.get("account_address", ""),
                    source,
                    thread_id,
                    json.dumps(fields.get("message_ids") or []),
                    fields["category"],
                    fields["utility"],
                    fields["action"],
                    fields["band"],
                    fields.get("model", ""),
                    confidence,
                    json.dumps(receipt),
                    fields.get("mailbox_before", ""),
                    json.dumps(artifacts),
                    created,
                ),
            )
            conn.commit()
        return decision_id

    def get_decision(self, decision_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id=?",
                (decision_id,),
            ).fetchone()
        return dict(row) if row else None

    def open_proposal(self, source: str, provider_thread_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM decisions
                WHERE source=? AND provider_thread_id=? AND status='proposed'
                """,
                (source, provider_thread_id),
            ).fetchone()
        return dict(row) if row else None

    def record(self, decision_id: str, *, status: str, receipt: dict) -> None:
        """Store the outcome of an apply. A missing row is left absent."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE decisions SET status=?, receipt_json=? WHERE id=?",
                (status, json.dumps(receipt), decision_id),
            )
            conn.commit()

    def set_mailbox_before(self, decision_id: str, mailbox: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE decisions SET mailbox_before=? WHERE id=?",
                (mailbox, decision_id),
            )
            conn.commit()

    def set_sync_cursor(self, provider: str, address: str, cursor: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_cursors (provider, address, cursor)
                VALUES (?, ?, ?)
                ON CONFLICT(provider, address) DO UPDATE SET cursor=excluded.cursor
                """,
                (provider, address, cursor),
            )
            conn.commit()

    def get_sync_cursor(self, provider: str, address: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM sync_cursors WHERE provider=? AND address=?",
                (provider, address),
            ).fetchone()
        return str(row["cursor"]) if row else ""

    def enqueue_outbox(self, decision_id: str, command: dict) -> str:
        """Queue one Apple action. A second queue for the same decision replaces it."""
        outbox_id = uuid.uuid4().hex
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._connect() as conn:
            conn.execute("DELETE FROM outbox WHERE decision_id=?", (decision_id,))
            conn.execute(
                "INSERT INTO outbox (id, decision_id, command_json, created) VALUES (?, ?, ?, ?)",
                (outbox_id, decision_id, json.dumps(command), created),
            )
            conn.commit()
        return outbox_id

    def list_outbox(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM outbox ORDER BY created, id").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["command"] = json.loads(item.pop("command_json"))
            out.append(item)
        return out

    def delete_outbox(self, outbox_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM outbox WHERE id=?", (outbox_id,))
            conn.commit()

    def list_accounts(self, *, include_removed: bool = False) -> list[dict]:
        sql = "SELECT * FROM accounts"
        if not include_removed:
            sql += " WHERE removed=0"
        sql += " ORDER BY provider, address"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [_account(row) for row in rows]

    def update_account(self, provider: str, address: str, **fields: object) -> bool:
        """Update an existing row. Column names come only from the allowlist."""
        chosen = [name for name in _ACCOUNT_FIELDS if name in fields]
        if not chosen:
            return False
        assignments: list[str] = []
        values: list[object] = []
        for name in chosen:
            value: object = fields[name]
            if name in ("included", "folder_sort", "removed"):
                value = int(bool(value))
            elif name == "auto_apply":
                if not isinstance(value, list):
                    raise TypeError(f"{name} must be a list")
                value = json.dumps(value)
            else:
                value = "" if value is None else str(value)
            assignments.append(f"{name}=?")
            values.append(value)
        values.extend([provider, address])
        sql = f"UPDATE accounts SET {', '.join(assignments)} WHERE provider=? AND address=?"  # nosec B608 — column names come only from _ACCOUNT_FIELDS
        with self._connect() as conn:
            cursor = conn.execute(sql, values)
            conn.commit()
            return cursor.rowcount > 0

    def remove_account(self, provider: str, address: str) -> bool:
        """Stop future reads and applies. The row stays so undo can find it."""
        return self.update_account(provider, address, removed=True, included=False)

    def list_decisions(
        self,
        *,
        status: str = "",
        limit: int = 100,
        newest_first: bool = False,
    ) -> list[dict]:
        direction = "DESC" if newest_first else "ASC"
        sql = "SELECT * FROM decisions"
        params: list[object] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += f" ORDER BY rowid {direction} LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decision(row) for row in rows]

    def list_sender_pins(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sender_memory WHERE pinned=1 ORDER BY source, sender",
            ).fetchall()
        return [dict(row) for row in rows]

    def get_sender(self, source: str, sender: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sender_memory WHERE source=? AND sender=?",
                (source, sender.casefold().strip()),
            ).fetchone()
        return dict(row) if row else None

    def note_correction(
        self,
        *,
        source: str,
        sender: str,
        category: str,
        action: str,
        domain: str = "",
        decision_id: str = "",
    ) -> dict:
        """Count one operator reversal. The same decision id does not count twice."""
        sender = sender.casefold().strip()
        source = source.strip()
        if not sender:
            raise ValueError("sender is required")
        existing = self.get_sender(source, sender)
        if decision_id and existing is not None and str(existing.get("last_decision") or "") == decision_id:
            return existing
        if not domain and "@" in sender:
            domain = sender.split("@", 1)[1]
        same = (
            existing is not None
            and str(existing.get("action") or "") == action
            and str(existing.get("category") or "") == category
        )
        hits = int(existing["hits"]) + 1 if same and existing is not None else 1
        pinned = hits >= PIN_HITS
        self._write_sender(
            source=source,
            sender=sender,
            domain=domain or str((existing or {}).get("domain") or ""),
            category=category,
            action=action,
            hits=hits,
            pinned=pinned,
            decision_id=decision_id,
        )
        row = self.get_sender(source, sender)
        if row is None:
            raise RuntimeError("sender memory was not stored")
        return row

    def pin_sender(
        self,
        *,
        source: str,
        sender: str,
        category: str,
        action: str,
        domain: str = "",
    ) -> dict:
        """Pin immediately. A later different outcome clears the pin."""
        sender = sender.casefold().strip()
        source = source.strip()
        if not sender:
            raise ValueError("sender is required")
        if not domain and "@" in sender:
            domain = sender.split("@", 1)[1]
        existing = self.get_sender(source, sender)
        hits = max(int(existing["hits"]) if existing else 0, 3)
        self._write_sender(
            source=source,
            sender=sender,
            domain=domain or str((existing or {}).get("domain") or ""),
            category=category,
            action=action,
            hits=hits,
            pinned=True,
            decision_id=str((existing or {}).get("last_decision") or ""),
        )
        row = self.get_sender(source, sender)
        if row is None:
            raise RuntimeError("sender memory was not stored")
        return row

    def _write_sender(
        self,
        *,
        source: str,
        sender: str,
        domain: str,
        category: str,
        action: str,
        hits: int,
        pinned: bool,
        decision_id: str,
    ) -> None:
        updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sender_memory (
                    source, sender, domain, category, action, hits, pinned, updated, last_decision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, sender) DO UPDATE SET
                    domain=excluded.domain,
                    category=excluded.category,
                    action=excluded.action,
                    hits=excluded.hits,
                    pinned=excluded.pinned,
                    updated=excluded.updated,
                    last_decision=excluded.last_decision
                """,
                (source, sender, domain, category, action, hits, int(pinned), updated, decision_id),
            )
            conn.commit()

    def upsert_rule(
        self,
        *,
        rule_id: str,
        source: str,
        condition: dict,
        action: str,
        category: str,
        enabled: bool = True,
    ) -> dict:
        if not isinstance(condition, dict):
            raise ValueError("condition must be an object")
        unknown = set(condition) - {"from", "domain", "subject_prefix", "list_id"}
        if unknown:
            raise ValueError("unknown condition key")
        stored = {
            key: str(condition[key]).strip()
            for key in ("from", "domain", "subject_prefix", "list_id")
            if str(condition.get(key) or "").strip()
        }
        if not stored:
            raise ValueError("condition needs a field")
        source = source.strip() or "*"
        if source != "*" and source not in PROVIDER_TOOLS:
            raise ValueError("source is not a mail provider")
        rule_id = rule_id.strip() or uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rules (id, source, condition_json, action, category, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    condition_json=excluded.condition_json,
                    action=excluded.action,
                    category=excluded.category,
                    enabled=excluded.enabled
                """,
                (rule_id, source, json.dumps(stored), action, category, int(enabled)),
            )
            conn.commit()
        rules = [row for row in self.list_rules() if row["id"] == rule_id]
        if not rules:
            raise RuntimeError("rule was not stored")
        return rules[0]

    def list_rules(self, source: str = "") -> list[dict]:
        sql = "SELECT * FROM rules"
        params: list[object] = []
        if source:
            sql += " WHERE source=? OR source='*'"
            params.append(source)
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        listed: list[dict] = []
        for row in rows:
            data = dict(row)
            data["enabled"] = bool(data["enabled"])
            try:
                parsed = json.loads(data["condition_json"])
            except ValueError:
                parsed = {}
            data["condition"] = parsed if isinstance(parsed, dict) else {}
            listed.append(data)
        return listed

    def latest_applied(self, source: str, provider_thread_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM decisions
                WHERE source=? AND provider_thread_id=? AND status='applied'
                ORDER BY rowid DESC LIMIT 1
                """,
                (source, provider_thread_id),
            ).fetchone()
        return dict(row) if row else None


_ACCOUNT_FIELDS = (
    "display_name",
    "included",
    "folder_sort",
    "auto_apply",
    "consent",
    "removed",
    "last_read",
    "last_apply",
    "last_rejection",
)


def _account(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {}
    data = dict(row)
    data["included"] = bool(data["included"])
    data["folder_sort"] = bool(data["folder_sort"])
    data["removed"] = bool(data.get("removed") or 0)
    data["auto_apply"] = json.loads(data["auto_apply"])
    return data


def _decision(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["message_ids"] = _json_list(data.get("message_ids"))
    data["rag_artifact_ids"] = _json_list(data.get("rag_artifact_ids"))
    receipt = _receipt_dict(data.get("receipt_json"))
    draft_body = receipt.get("draft_body")
    if isinstance(draft_body, str) and draft_body.strip():
        data["draft_body"] = draft_body.strip()
    classification_reason = receipt.get("classification_reason")
    if isinstance(classification_reason, str) and classification_reason.strip():
        data["classification_reason"] = classification_reason.strip()
    return data


def _receipt_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _json_list(raw: object) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return [raw]
        if isinstance(parsed, list):
            return parsed
    return []
