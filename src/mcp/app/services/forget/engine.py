# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Trash, restore, purge and receipts over the forget registry and adapters.

A purge writes its receipt before the first adapter runs. From then on the
forget only moves forward: a restore is refused (an adapter may already have
erased part of it, and a half-restored conversation is worse than none), and
the maintenance job retries it until every adapter is done.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from app.services.forget.adapters import ADAPTERS, ForgetAdapter
from core.forget.registry import Entry, Registry, Subject, get_registry, new_forget_id
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.forget")

OUT_OF_REACH_NOTES = [
    "KB backups made before this forget",
    "anything already sent to a cloud model provider",
    "analytics lists (ingest:log, verify:*) until their 30-day expiry",
]


class ForgetConflict(Exception):
    """A restore was asked for a subject whose purge has started or finished."""


class ForgetUnavailable(Exception):
    """No sync dir is configured, so there is nowhere to record a forget."""


def _audit(action: str, *, actor: str, target: str, detail: dict[str, Any]) -> None:
    """One enterprise audit record per forget action: the forget id and counts, never content or ids."""
    from core.utils import audit_log
    audit_log.audit(action, actor=actor, target=target, detail=detail)


def _kind_counts(subjects: list[Subject]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in subjects:
        counts[s.kind] = counts.get(s.kind, 0) + 1
    return counts


# Derived subjects go first: the verified-memory lookup and the :Conversation
# node check need the conversation's links while they still exist.
PURGE_ORDER = {"artifact": 0, "chunk": 1, "memory": 2, "conversation": 3}


def _ordered(subjects: list[Subject]) -> list[Subject]:
    return sorted(subjects, key=lambda s: PURGE_ORDER.get(s.kind, 99))


def _adapters_for(kind: str) -> list[ForgetAdapter]:
    return [a for a in ADAPTERS if kind in a.kinds]


def _receipts_dir() -> Path:
    return Path(config.SYNC_DIR) / "forget" / "receipts"


def _applied_path() -> Path:
    return Path(os.getenv("DATA_DIR", "data")) / "forget_applied.jsonl"


def _age_days(at: str) -> float:
    then = datetime.fromisoformat(at.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def _require_registry() -> Registry:
    if not config.SYNC_DIR:
        raise ForgetUnavailable("sync dir not configured")
    return get_registry()


def _record(subjects: list[Subject], forget_id: str, state: str, requested_by: str, user_id: str) -> None:
    now = utcnow_iso()
    _require_registry().append([
        Entry(forget_id, s, state, now, config.MACHINE_ID, requested_by, user_id) for s in subjects
    ])


def _mark_applied(forget_id: str, subject: Subject, state: str) -> None:
    path = _applied_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"forget_id": forget_id, "kind": subject.kind, "id": subject.id, "state": state}) + "\n")


def _applied() -> set[tuple[str, str, str, str]]:
    path = _applied_path()
    if not path.exists():
        return set()
    seen: set[tuple[str, str, str, str]] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            seen.add((row["forget_id"], row["kind"], row["id"], row["state"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return seen


def _receipt_path(forget_id: str) -> Path:
    return _receipts_dir() / f"{forget_id}.json"


def _purge_started(forget_id: str) -> bool:
    return _receipt_path(forget_id).exists()


def _read_receipt(forget_id: str) -> dict[str, Any]:
    try:
        receipt = json.loads(_receipt_path(forget_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log_swallowed_error("forget.read_receipt", exc, context={"forget_id": forget_id})
        return {}
    return receipt if isinstance(receipt, dict) else {}


def _write_receipt(receipt: dict[str, Any]) -> None:
    out = _receipts_dir()
    out.mkdir(parents=True, exist_ok=True)
    _receipt_path(receipt["forget_id"]).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def trash(subjects: list[Subject], *, requested_by: str, user_id: str = "") -> str:
    forget_id = new_forget_id()
    subjects = _ordered(subjects)
    _record(subjects, forget_id, "trashed", requested_by, user_id)
    for subject in subjects:
        hidden = True
        for adapter in _adapters_for(subject.kind):
            try:
                adapter.hide(subject, forget_id)
            except Exception as exc:  # noqa: BLE001 — left unapplied, so apply_remote retries the hide
                log_swallowed_error("forget.trash.hide", exc, context={"adapter": adapter.name})
                hidden = False
        if hidden:
            _mark_applied(forget_id, subject, "trashed")
    _audit("forget.trash", actor=requested_by, target=forget_id, detail={"subjects": _kind_counts(subjects)})
    return forget_id


def restore(forget_id: str) -> int:
    reg = _require_registry()
    entries = reg.forget_entries(forget_id)
    if any(e.state == "purged" for e in entries) or _purge_started(forget_id):
        raise ForgetConflict(f"{forget_id} has been purged or its purge has started")
    trashed = [e.subject for e in entries if e.state == "trashed"]
    owner = next((e.user_id for e in entries if e.state == "trashed"), "")
    for subject in trashed:
        for adapter in _adapters_for(subject.kind):
            adapter.restore(subject, forget_id)
    if trashed:
        _record(trashed, forget_id, "restored", "ui", owner)
        for subject in trashed:
            _mark_applied(forget_id, subject, "restored")
        _audit("forget.restore", actor="ui", target=forget_id, detail={"subjects": _kind_counts(trashed)})
    return len(trashed)


def _purge_subjects(
    forget_id: str, subjects: list[Subject], requested_by: str, user_id: str = "",
) -> dict[str, Any]:
    subjects = _ordered(subjects)
    # A retry carries forward what earlier attempts removed and which subjects they covered.
    prior = _read_receipt(forget_id)
    prior_adapters = prior.get("adapters")
    if not isinstance(prior_adapters, dict):
        prior_adapters = {}
    current = [{"kind": s.kind, "id": s.id} for s in subjects]
    listed = [row for row in prior.get("subjects") or [] if row not in current] + current
    receipt: dict[str, Any] = {
        "forget_id": forget_id,
        "at": utcnow_iso(),
        "machine_id": config.MACHINE_ID,
        "requested_by": requested_by,
        "subjects": listed,
        # Every store's earlier result stays on the receipt; a store run again
        # in this attempt is re-marked from what it does now.
        "adapters": {n: dict(a) for n, a in prior_adapters.items() if isinstance(a, dict)},
        "out_of_reach": OUT_OF_REACH_NOTES,
    }
    _write_receipt(receipt)  # the purge has started: restore is refused from here on
    touched: set[str] = set()
    done: list[Subject] = []
    for subject in subjects:
        complete = True
        for adapter in _adapters_for(subject.kind):
            slot = receipt["adapters"].setdefault(adapter.name, {"status": "done", "removed": 0})
            if adapter.name not in touched:
                touched.add(adapter.name)
                slot["status"] = "done"
                slot.pop("error", None)
                slot["removed"] = int(slot.get("removed") or 0)
            try:
                slot["removed"] += adapter.purge(subject).removed
            except Exception as exc:  # noqa: BLE001 — recorded as pending; the subject stays trashed and is retried
                log_swallowed_error("forget.purge", exc, context={"adapter": adapter.name})
                slot["status"] = "pending"
                slot["error"] = type(exc).__name__
                complete = False
        if complete:
            done.append(subject)
    if done:
        _record(done, forget_id, "purged", requested_by, user_id)
        for subject in done:
            _mark_applied(forget_id, subject, "purged")
    _write_receipt(receipt)
    pending = sorted(n for n, a in receipt["adapters"].items() if a["status"] != "done")
    _audit("forget.purge", actor=requested_by, target=forget_id,
           detail={"subjects": _kind_counts(subjects), "purged": len(done), "pending_adapters": pending})
    return receipt


def purge(forget_id: str) -> dict[str, Any]:
    entries = [e for e in _require_registry().forget_entries(forget_id) if e.state == "trashed"]
    owner = entries[0].user_id if entries else ""
    return _purge_subjects(forget_id, [e.subject for e in entries], "ui", owner)


def forget_permanently(subjects: list[Subject], *, requested_by: str, user_id: str = "") -> dict[str, Any]:
    forget_id = trash(subjects, requested_by=requested_by, user_id=user_id)
    return _purge_subjects(forget_id, subjects, requested_by, user_id)


def purge_artifact(artifact_id: str, *, requested_by: str, user_id: str = "") -> bool:
    """Permanently forget one artifact: the delete routes. Returns whether every
    store finished; a store left pending is retried by the maintenance job. With
    no sync dir there is no registry to record it in, so it is removed directly."""
    if not config.SYNC_DIR:
        from app.services.content_lifecycle import remove_content
        remove_content(artifact_id)
        return True
    receipt = forget_permanently([Subject("artifact", artifact_id)], requested_by=requested_by, user_id=user_id)
    return all(a.get("status") == "done" for a in receipt["adapters"].values())


def empty_trash(*, older_than_days: int | None = None) -> list[str]:
    """Purge trashed subjects older than the window (all of them when it is
    ``None``), plus any whose purge already started and is still pending."""
    reg = _require_registry()
    by_forget: dict[str, list[Entry]] = {}
    for entry in reg.latest():
        if entry.state != "trashed":
            continue
        if older_than_days is None or _age_days(entry.at) >= older_than_days or _purge_started(entry.forget_id):
            by_forget.setdefault(entry.forget_id, []).append(entry)
    purged: list[str] = []
    for forget_id, entries in by_forget.items():
        receipt = _purge_subjects(
            forget_id, [e.subject for e in entries], "retention" if older_than_days else "ui", entries[0].user_id,
        )
        if all(a["status"] == "done" for a in receipt["adapters"].values()):
            purged.append(forget_id)
    return purged


def _labels(subjects: list[Subject]) -> dict[tuple[str, str], str]:
    """Display names read live for the Trash view; never stored."""
    out: dict[tuple[str, str], str] = {}
    if config.SYNC_DIR:
        from app.sync.user_state import read_conversation
        for s in subjects:
            if s.kind != "conversation":
                continue
            try:
                out[(s.kind, s.id)] = str(read_conversation(config.SYNC_DIR, s.id).get("title") or "")
            except Exception as exc:  # noqa: BLE001 — a missing label renders as blank
                log_swallowed_error("forget.trash_label", exc)
    graph = [s for s in subjects if s.kind in ("artifact", "memory")]
    if graph:
        try:
            from app.deps import get_neo4j
            with get_neo4j().session() as session:
                rows = session.run(
                    "UNWIND $ids AS i OPTIONAL MATCH (a:Artifact {id: i}) OPTIONAL MATCH (m:Memory {id: i}) "
                    "RETURN i AS id, coalesce(a.filename, left(m.text, 160), '') AS label",
                    ids=sorted({s.id for s in graph}),
                )
                found = {r["id"]: r["label"] or "" for r in rows}
            for s in graph:
                out[(s.kind, s.id)] = found.get(s.id, "")
        except Exception as exc:  # noqa: BLE001 — labels are cosmetic; the list still renders
            log_swallowed_error("forget.trash_label", exc)
    chunks = [s.id for s in subjects if s.kind == "chunk"]
    if chunks:
        try:
            from app.services.forget.preview import chunk_details
            for cid, d in chunk_details(chunks).items():
                out[("chunk", cid)] = f"{d['filename']}: {d['excerpt']}"
        except Exception as exc:  # noqa: BLE001 — labels are cosmetic; the list still renders
            log_swallowed_error("forget.trash_label", exc)
    return out


def list_trash() -> list[dict[str, Any]]:
    """Trashed forgets, newest first."""
    reg = _require_registry()
    groups: dict[str, list[Entry]] = {}
    for entry in reg.latest():
        if entry.state != "trashed":
            continue
        groups.setdefault(entry.forget_id, []).append(entry)
    labels = _labels([e.subject for entries in groups.values() for e in entries])
    out = []
    for forget_id, entries in groups.items():
        ordered = sorted(entries, key=lambda e: PURGE_ORDER.get(e.subject.kind, 99), reverse=True)
        out.append({
            "forget_id": forget_id,
            "at": min(e.at for e in entries),
            "requested_by": entries[0].requested_by,
            "purge_started": _purge_started(forget_id),
            "subjects": [
                {"kind": e.subject.kind, "id": e.subject.id, "label": labels.get((e.subject.kind, e.subject.id), "")}
                for e in ordered
            ],
        })
    return sorted(out, key=lambda g: g["at"], reverse=True)


def list_receipts(limit: int = 100) -> list[dict[str, Any]]:
    """Receipt summaries, newest first: counts per kind and whether every store is done."""
    root = _receipts_dir()
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("fg_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(out) >= limit:
            break
        receipt = _read_receipt(path.stem)
        if not receipt:
            continue
        kinds: dict[str, int] = {}
        for row in receipt.get("subjects") or []:
            kind = str((row or {}).get("kind") or "?")
            kinds[kind] = kinds.get(kind, 0) + 1
        adapters = receipt.get("adapters") or {}
        out.append({
            "forget_id": str(receipt.get("forget_id") or path.stem),
            "at": str(receipt.get("at") or ""),
            "requested_by": str(receipt.get("requested_by") or ""),
            "subjects": kinds,
            "status": "done" if all((a or {}).get("status") == "done" for a in adapters.values()) else "pending",
        })
    return out


def read_receipt(forget_id: str) -> dict[str, Any]:
    return _read_receipt(forget_id)


def retry_started_purges() -> list[str]:
    """Finish purges that started and left a store pending. Runs whatever the
    auto-empty window is: a permanent forget must not wait for Empty Trash."""
    reg = _require_registry()
    by_forget: dict[str, list[Entry]] = {}
    for entry in reg.latest():
        if entry.state == "trashed" and _purge_started(entry.forget_id):
            by_forget.setdefault(entry.forget_id, []).append(entry)
    finished: list[str] = []
    for forget_id, entries in by_forget.items():
        receipt = _purge_subjects(forget_id, [e.subject for e in entries], entries[0].requested_by, entries[0].user_id)
        if all(a.get("status") == "done" for a in receipt["adapters"].values()):
            finished.append(forget_id)
    return finished


def apply_remote() -> dict[str, int]:
    """Bring this machine's stores in line with forgets recorded elsewhere."""
    reg = _require_registry()
    applied = _applied()
    counts = {"hidden": 0, "restored": 0, "purged": 0}
    for entry in reg.latest():
        key = (entry.forget_id, entry.subject.kind, entry.subject.id, entry.state)
        if key in applied:
            continue
        adapters = _adapters_for(entry.subject.kind)
        try:
            if entry.state == "trashed":
                for adapter in adapters:
                    adapter.hide(entry.subject, entry.forget_id)
                counts["hidden"] += 1
            elif entry.state == "restored":
                for adapter in adapters:
                    adapter.restore(entry.subject, entry.forget_id)
                counts["restored"] += 1
            elif entry.state == "readded":
                pass  # content was added again; nothing to hide or erase
            elif entry.state == "purged":
                for adapter in adapters:
                    adapter.purge(entry.subject)
                counts["purged"] += 1
        except Exception as exc:  # noqa: BLE001 — left unapplied; the next run retries it
            log_swallowed_error("forget.apply_remote", exc, context={"forget_id": entry.forget_id})
            continue
        _mark_applied(entry.forget_id, entry.subject, entry.state)
    return counts
