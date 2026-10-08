# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The forget registry.

Append-only JSON lines in ``{SYNC_DIR}/forget/``, one file per machine
(``registry-{machine_id}.jsonl``), so two machines never write the same file
and a shared Dropbox folder cannot produce conflict copies of it. Readers glob
``registry*.jsonl`` (a stray conflict copy is read like any other file) and
keep, per subject, the entry with the latest ``at`` — except that ``purged``
always wins, so a restore written later on a machine with a skewed clock can
never revive purged data. The index reloads whenever any file's size or mtime
changes, so the server and the processor worker see each other's entries
without a restart.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.forget")

KINDS = frozenset({"conversation", "artifact", "chunk", "memory"})
STATES = frozenset({"trashed", "restored", "purged", "readded"})
FORGOTTEN_STATES = frozenset({"trashed", "purged"})


@dataclass(frozen=True)
class Subject:
    kind: str
    id: str


@dataclass(frozen=True)
class Entry:
    forget_id: str
    subject: Subject
    state: str
    at: str
    machine_id: str
    requested_by: str
    user_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "forget_id": self.forget_id,
            "subject": {"kind": self.subject.kind, "id": self.subject.id},
            "state": self.state,
            "at": self.at,
            "machine_id": self.machine_id,
            "requested_by": self.requested_by,
            "user_id": self.user_id,
        }

    @staticmethod
    def from_json(row: Any) -> Entry | None:
        try:
            subj = row["subject"]
            entry = Entry(
                forget_id=str(row["forget_id"]),
                subject=Subject(str(subj["kind"]), str(subj["id"])),
                state=str(row["state"]),
                at=str(row["at"]),
                machine_id=str(row.get("machine_id", "")),
                requested_by=str(row.get("requested_by", "")),
                user_id=str(row.get("user_id", "")),
            )
        except (KeyError, TypeError, AttributeError):
            return None
        if entry.subject.kind not in KINDS or entry.state not in STATES or not entry.subject.id:
            return None
        return entry


def new_forget_id() -> str:
    return "fg_" + secrets.token_hex(8)


def _wins(new: Entry, old: Entry | None) -> bool:
    if old is None:
        return True
    if old.state == "purged" and new.state != "purged":
        return False
    if new.state == "purged" and old.state != "purged":
        return True
    return new.at >= old.at


class Registry:
    def __init__(self, root: Path | None, machine_id: str) -> None:
        self._root = Path(root) if root else None
        self._machine_id = machine_id
        self._lock = threading.Lock()
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._by_subject: dict[Subject, Entry] = {}
        self._all: list[Entry] = []
        self._live: dict[Subject, set[str]] = {}

    def _files(self) -> list[tuple[Path, os.stat_result]]:
        if self._root is None or not self._root.is_dir():
            return []
        found = []
        for path in sorted(self._root.glob("registry*.jsonl")):
            try:
                found.append((path, path.stat()))
            except OSError:
                # A sync client can rename or remove a conflict copy between glob and stat.
                continue
        return found

    def _refresh(self) -> None:
        files = self._files()
        sig = tuple((p.name, st.st_size, st.st_mtime_ns) for p, st in files)
        if sig == self._signature:
            return
        everything: list[Entry] = []
        for path, _st in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.warning("forget registry: cannot read %s: %s", path.name, exc)
                continue
            for line in lines:
                try:
                    entry = Entry.from_json(json.loads(line))
                except json.JSONDecodeError:
                    entry = None
                if entry is not None:
                    everything.append(entry)
        # A ``readded`` entry cancels every entry of the forget it names, for that
        # subject only. The cancellation is causal (it names the forget), never by
        # time, so a skewed clock cannot decide it, and a later forget has a new id
        # the re-add does not name.
        cancelled = {(e.subject, e.forget_id) for e in everything if e.state == "readded"}
        per_forget: dict[tuple[Subject, str], Entry] = {}
        readds: dict[Subject, Entry] = {}
        for entry in everything:
            key = (entry.subject, entry.forget_id)
            if entry.state == "readded":
                if entry.subject not in readds or entry.at >= readds[entry.subject].at:
                    readds[entry.subject] = entry
            elif key not in cancelled and _wins(entry, per_forget.get(key)):
                per_forget[key] = entry
        # A subject is forgotten while any of its forgets is live, so its state comes
        # from the live forgets first and only then from restores.
        forgotten: dict[Subject, Entry] = {}
        other: dict[Subject, Entry] = {}
        live: dict[Subject, set[str]] = {}
        for (subject, forget_id), entry in per_forget.items():
            bucket = forgotten if entry.state in FORGOTTEN_STATES else other
            if _wins(entry, bucket.get(subject)):
                bucket[subject] = entry
            if entry.state in FORGOTTEN_STATES:
                live.setdefault(subject, set()).add(forget_id)
        by_subject: dict[Subject, Entry] = {**other, **forgotten}
        for subject, entry in readds.items():
            by_subject.setdefault(subject, entry)
        self._by_subject, self._all, self._signature = by_subject, everything, sig
        self._live = live

    def append(self, entries: list[Entry]) -> None:
        if self._root is None:
            raise RuntimeError("forget registry: no sync dir configured")
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"registry-{self._machine_id}.jsonl"
        payload = "".join(json.dumps(e.to_json(), sort_keys=True) + "\n" for e in entries)
        with self._lock:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                # A write cut short by a crash leaves no trailing newline; start on a
                # fresh line so the torn fragment cannot swallow the first new entry.
                size = os.fstat(fd).st_size
                if size and os.pread(fd, 1, size - 1) != b"\n":
                    payload = "\n" + payload
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            self._signature = ()

    def state_of(self, kind: str, id: str) -> str | None:
        with self._lock:
            self._refresh()
            entry = self._by_subject.get(Subject(kind, id))
        return entry.state if entry else None

    def is_forgotten(self, kind: str, id: str) -> bool:
        return self.state_of(kind, id) in FORGOTTEN_STATES

    def record_readd(self, kind: str, id: str, *, requested_by: str, machine_id: str | None = None) -> bool:
        """Cancel every live forget of a subject because its content was added again.

        Returns ``False`` (and writes nothing) when the subject is not forgotten.
        """
        from core.utils.time import utcnow_iso

        subject = Subject(kind, id)
        with self._lock:
            self._refresh()
            if self._by_subject.get(subject) is None or self._by_subject[subject].state not in FORGOTTEN_STATES:
                return False
            forget_ids = sorted(self._live.get(subject, set()) | {self._by_subject[subject].forget_id})
        now = utcnow_iso()
        self.append([
            Entry(fid, subject, "readded", now, machine_id or self._machine_id, requested_by) for fid in forget_ids
        ])
        return True

    def latest(self, since: str | None = None) -> list[Entry]:
        with self._lock:
            self._refresh()
            rows = list(self._by_subject.values())
        rows = [e for e in rows if since is None or e.at > since]
        return sorted(rows, key=lambda e: (e.at, e.subject.kind, e.subject.id))

    def forget_entries(self, forget_id: str) -> list[Entry]:
        with self._lock:
            self._refresh()
            return [e for e in self._by_subject.values() if e.forget_id == forget_id]


_REGISTRY: Registry | None = None
_REGISTRY_GUARD = threading.Lock()


def get_registry() -> Registry:
    global _REGISTRY
    with _REGISTRY_GUARD:
        if _REGISTRY is None:
            import config

            root = Path(config.SYNC_DIR) / "forget" if config.SYNC_DIR else None
            _REGISTRY = Registry(root, config.MACHINE_ID)
        return _REGISTRY


def is_forgotten(kind: str, id: str) -> bool:
    if not id:
        return False
    return get_registry().is_forgotten(kind, id)


def record_readd(kind: str, id: str, *, requested_by: str) -> bool:
    if not id:
        return False
    return get_registry().record_readd(kind, id, requested_by=requested_by)


def reset_registry_for_tests() -> None:
    global _REGISTRY
    with _REGISTRY_GUARD:
        _REGISTRY = None
