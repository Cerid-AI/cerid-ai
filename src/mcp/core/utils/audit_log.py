# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Append-only, hash-chained audit log (Enterprise ``audit_logging``).

Security-relevant events — who did what, to what, and whether it worked —
written as one JSON object per line, each carrying the hash of the record
before it. A modified, reordered, or removed record breaks the chain from that
point on, which :func:`verify` reports with the sequence number where it
happened.

**Why a chain and not a table.** The value of an audit log is that it can be
trusted after the fact, and a plain log offers nothing against someone who can
edit the file. Hash-chaining does not prevent tampering — an attacker with
write access can rewrite the whole chain — but it makes *selective* tampering
detectable, which is the realistic threat: quietly removing the one line that
records what you did. That is a real, bounded guarantee, and it is the one this
claims.

**On failure this is loud, not fatal.** :func:`record` raises
:class:`AuditLogError` when it cannot write; :func:`audit` swallows that into
``log_swallowed_error`` plus a counter that ``/health`` surfaces, so a broken
audit log shows up as a degraded system rather than either a silent gap or a
product that stops working. Fail-closed (refuse the action when it cannot be
audited) is the stricter posture and is deliberately NOT the default here: this
is a self-hosted single-tenant product where an unwritable log would otherwise
brick the install. Call sites that want fail-closed call :func:`record`
directly and let it raise.

**Retention.** Segments rotate by size and are never deleted. An audit trail
that quietly prunes itself is worth less than no audit trail, because the
absence of a record stops meaning anything. Removing old segments is an
operator decision, taken with a shell.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("ai-companion.audit_log")

UTC = timezone.utc

Outcome = Literal["success", "failure", "denied"]

#: Roll to a new segment past this size. Keeps a single file readable and
#: bounds the cost of :func:`verify`, which must hash every record it checks.
DEFAULT_MAX_SEGMENT_BYTES = 32 * 1024 * 1024

#: Genesis marker. An empty ``prev`` is only valid on the first record of the
#: first segment; anywhere else it means a record was inserted or the chain was
#: restarted, and verification says so.
GENESIS = ""

_lock = threading.Lock()

#: Count of writes that failed since process start. Surfaced by /health so a
#: log nobody can write to is visible without reading the log.
_write_failures = 0


class AuditLogError(RuntimeError):
    """The audit log could not be written."""


def _log_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data")) / "audit"


def _segment_path(index: int) -> Path:
    return _log_dir() / f"audit-{index:05d}.jsonl"


def _head_path() -> Path:
    """Sidecar recording where the chain currently ends.

    Without it, removing lines from the END of the newest segment is
    undetectable: what is left is a perfectly valid shorter chain. Chaining
    only protects a record from the record after it, and the last record has
    none. The head is that missing successor.

    It is not tamper-proof — someone who can rewrite the log can rewrite this —
    but it is the same bar as everywhere else here: hiding now requires editing
    two things that must agree, instead of truncating one.
    """
    return _log_dir() / "audit-head.json"


def _read_head() -> dict[str, Any] | None:
    path = _head_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_head(seq: int, digest: str) -> None:
    path = _head_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a half-written head after a crash would fail verification on a
    # log that is actually intact.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"seq": seq, "hash": digest}), encoding="utf-8")
    os.replace(tmp, path)


def _max_segment_bytes() -> int:
    raw = os.getenv("CERID_AUDIT_LOG_MAX_SEGMENT_BYTES", "")
    if not raw:
        return DEFAULT_MAX_SEGMENT_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_SEGMENT_BYTES
    return value if value > 0 else DEFAULT_MAX_SEGMENT_BYTES


def segments() -> list[Path]:
    """Every segment, oldest first. Empty when the log has never been written."""
    directory = _log_dir()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("audit-*.jsonl") if p.is_file())


def canonical_bytes(record: dict[str, Any]) -> bytes:
    """Serialise a record for hashing, minus its own ``hash`` field.

    Sorted keys and no whitespace: the hash has to be reproducible from the
    file, and json.dumps' default key order and spacing are not guaranteed
    across versions.
    """
    body = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


def _tail() -> tuple[int, str, Path]:
    """``(next_seq, prev_hash, segment_to_append_to)``.

    Reads the last line of the newest segment. A log whose newest segment is
    unreadable is not treated as an empty log — that would silently restart the
    chain and orphan everything already recorded.
    """
    existing = segments()
    if not existing:
        return 0, GENESIS, _segment_path(0)

    newest = existing[-1]
    last_line = ""
    with newest.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last_line = line
    if not last_line:
        # An empty newest segment is only legitimate when it is also the first.
        if len(existing) == 1:
            return 0, GENESIS, newest
        raise AuditLogError(f"audit segment {newest.name} is empty but is not the first")

    try:
        last = json.loads(last_line)
        seq = int(last["seq"])
        prev = str(last["hash"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AuditLogError(f"audit segment {newest.name} ends in an unreadable record") from exc

    target = newest
    if newest.stat().st_size >= _max_segment_bytes():
        target = _segment_path(len(existing))
    return seq + 1, prev, target


def record(
    action: str,
    *,
    actor: str = "system",
    target: str | None = None,
    outcome: Outcome = "success",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one event. Returns the written record. Raises on failure.

    ``action`` is dotted and coarse (``license.activate``, ``artifact.delete``)
    so the log can be filtered by prefix without parsing free text.
    """
    if not action:
        raise ValueError("audit action is required")

    with _lock:
        try:
            seq, prev, path = _tail()
            entry: dict[str, Any] = {
                "seq": seq,
                "ts": datetime.now(UTC).isoformat(),
                "actor": actor,
                "action": action,
                "target": target,
                "outcome": outcome,
                "detail": detail or {},
                "prev": prev,
            }
            entry["hash"] = compute_hash(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str))
                handle.write("\n")
                # An audit record that is only in the page cache when the host
                # loses power is not a record. The whole point is that it
                # survives the event it describes.
                handle.flush()
                os.fsync(handle.fileno())
            _write_head(seq, entry["hash"])
        except AuditLogError:
            raise
        except OSError as exc:
            raise AuditLogError(f"could not write the audit log: {exc}") from exc
    return entry


def audit(
    action: str,
    *,
    actor: str = "system",
    target: str | None = None,
    outcome: Outcome = "success",
    detail: dict[str, Any] | None = None,
) -> bool:
    """:func:`record`, but a write failure is reported rather than raised.

    Returns whether the record landed. Call sites that must not proceed
    unaudited use :func:`record` and handle :class:`AuditLogError` themselves.
    """
    global _write_failures
    try:
        record(action, actor=actor, target=target, outcome=outcome, detail=detail)
        return True
    except (AuditLogError, ValueError, OSError) as exc:
        with _lock:
            _write_failures += 1
        # Not a bare log call: this has to reach /health.swallowed_errors_last_hour
        # too, or a log that stopped recording looks exactly like a quiet system.
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("core.utils.audit_log.audit", exc)
        logger.error("audit record dropped (%s): %s", action, exc)
        return False


def write_failure_count() -> int:
    """Writes dropped since process start. Read by the health payload."""
    return _write_failures


def read(
    *,
    limit: int = 100,
    offset: int = 0,
    action_prefix: str | None = None,
    outcome: Outcome | None = None,
) -> list[dict[str, Any]]:
    """Records newest-first, optionally filtered.

    A line that will not parse is returned as ``{"seq": None, "malformed": ...}``
    rather than skipped. Dropping it would make a corrupted log read as a
    shorter clean one — the reader's version of the same substitution
    :func:`verify` exists to catch.
    """
    limit = max(0, min(limit, 1000))
    rows: list[dict[str, Any]] = []
    for path in segments():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    rows.append({"seq": None, "malformed": line.strip()[:200], "segment": path.name})
                    continue
                if not isinstance(entry, dict):
                    rows.append({"seq": None, "malformed": line.strip()[:200], "segment": path.name})
                    continue
                rows.append(entry)

    if action_prefix:
        rows = [r for r in rows if str(r.get("action", "")).startswith(action_prefix) or "malformed" in r]
    if outcome:
        rows = [r for r in rows if r.get("outcome") == outcome or "malformed" in r]

    rows.reverse()
    return rows[offset : offset + limit]


def count() -> int:
    """Total records on disk, malformed lines included."""
    total = 0
    for path in segments():
        with path.open("r", encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


class VerifyResult(dict):
    """``{ok, checked, records, broken_at, reason}``. A dict so it serialises."""


def verify() -> VerifyResult:
    """Walk the chain and report the first place it stops holding.

    Every way this can answer "I did not check" is a failure rather than a
    pass. A malformed line, a sequence gap, a ``prev`` that does not match the
    previous record's hash, a recomputed hash that differs, a genesis marker in
    the middle — each is reported with the sequence number, and an empty log is
    reported as ``records: 0`` rather than as a verified one.
    """
    checked = 0
    expected_seq = 0
    expected_prev = GENESIS

    for path in segments():
        with path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    return VerifyResult(
                        ok=False,
                        checked=checked,
                        records=checked,
                        broken_at=expected_seq,
                        reason=f"{path.name} line {lineno} is not valid JSON",
                    )
                if not isinstance(entry, dict):
                    return VerifyResult(
                        ok=False,
                        checked=checked,
                        records=checked,
                        broken_at=expected_seq,
                        reason=f"{path.name} line {lineno} is not a record",
                    )

                seq = entry.get("seq")
                if seq != expected_seq:
                    return VerifyResult(
                        ok=False,
                        checked=checked,
                        records=checked,
                        broken_at=expected_seq,
                        reason=(
                            f"{path.name} line {lineno}: expected seq {expected_seq}, "
                            f"found {seq!r} — a record was removed or inserted"
                        ),
                    )

                if entry.get("prev") != expected_prev:
                    return VerifyResult(
                        ok=False,
                        checked=checked,
                        records=checked,
                        broken_at=seq,
                        reason=f"seq {seq} does not chain to the record before it",
                    )

                stored = entry.get("hash")
                if not isinstance(stored, str) or compute_hash(entry) != stored:
                    return VerifyResult(
                        ok=False,
                        checked=checked,
                        records=checked,
                        broken_at=seq,
                        reason=f"seq {seq} has been modified since it was written",
                    )

                checked += 1
                expected_seq = seq + 1
                expected_prev = stored

    # The chain is internally consistent. That is not the same as complete:
    # lopping records off the END leaves a valid shorter chain, because the last
    # record has no successor to vouch for it. The head sidecar is that
    # successor.
    head = _read_head()
    if head is None:
        if checked > 0:
            return VerifyResult(
                ok=False,
                checked=checked,
                records=checked,
                broken_at=expected_seq - 1,
                reason="the head marker is missing, so the log cannot be shown to be complete",
            )
    else:
        head_seq = head.get("seq")
        if head_seq != expected_seq - 1 or head.get("hash") != expected_prev:
            return VerifyResult(
                ok=False,
                checked=checked,
                records=checked,
                broken_at=expected_seq - 1,
                reason=(
                    f"the log ends at seq {expected_seq - 1} but the head records "
                    f"{head_seq!r} — records were removed from the end"
                ),
            )

    return VerifyResult(ok=True, checked=checked, records=checked, broken_at=None, reason=None)
