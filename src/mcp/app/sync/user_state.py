# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read/write user state files (settings, conversations, preferences) to the sync directory."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import random
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger("ai-companion.sync.user_state")

# Audit P1-11: macOS advisory lock collisions with Dropbox-synced
# settings.json surface as OSError(errno.EDEADLK, 'Resource deadlock
# avoided'). The condition clears within ~100 ms as Dropbox releases its
# lock; these constants tune the retry loop used by the async wrappers
# below.
_EDEADLK_RETRY_ATTEMPTS = 3
_EDEADLK_BACKOFF_BASE_S = 0.1

# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------


def _encrypt_value(value: str) -> str:
    """Encrypt a string value if encryption is configured."""
    try:
        import config as _cfg

        if not getattr(_cfg, "ENCRYPT_SYNC", False):
            return value
        from utils.encryption import encrypt_field

        return encrypt_field(value) if value else value
    except Exception:
        return value


def _decrypt_value(value: str) -> str:
    """Decrypt a string value if it looks encrypted."""
    try:
        from utils.encryption import decrypt_field

        return decrypt_field(value) if value else value
    except Exception:
        return value


# Keys that should NOT be encrypted (sync metadata / identifiers)
_SYNC_METADATA_KEYS = frozenset({
    "id", "updated_at", "machine_id", "_synced_at", "_machine_id",
    "createdAt", "updatedAt", "role",
})


def _encrypt_dict(d: dict) -> dict:
    """Encrypt string values in a dict, skipping metadata keys."""
    result: dict[str, object] = {}
    for k, v in d.items():
        if k in _SYNC_METADATA_KEYS:
            result[k] = v
        elif isinstance(v, str):
            result[k] = _encrypt_value(v)
        elif isinstance(v, list):
            result[k] = [
                _encrypt_dict(item) if isinstance(item, dict)
                else _encrypt_value(item) if isinstance(item, str)
                else item
                for item in v
            ]
        elif isinstance(v, dict):
            result[k] = _encrypt_dict(v)
        else:
            result[k] = v
    return result


def _decrypt_dict(d: dict) -> dict:
    """Decrypt string values in a dict."""
    result: dict[str, object] = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _decrypt_value(v)
        elif isinstance(v, list):
            result[k] = [
                _decrypt_dict(item) if isinstance(item, dict)
                else _decrypt_value(item) if isinstance(item, str)
                else item
                for item in v
            ]
        elif isinstance(v, dict):
            result[k] = _decrypt_dict(v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _user_dir(sync_dir: str) -> Path:
    """Return the ``{sync_dir}/user`` directory, creating it if needed."""
    p = Path(sync_dir) / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _conversations_dir(sync_dir: str) -> Path:
    """Return the ``{sync_dir}/user/conversations`` directory, creating it if needed."""
    p = _user_dir(sync_dir) / "conversations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning empty dict on missing/invalid files."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Cannot read %s: %s", path, exc)
        return {}


_write_locks: dict[str, threading.Lock] = {}
_write_locks_guard = threading.Lock()


def _get_write_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _write_locks_guard:
        if key not in _write_locks:
            _write_locks[key] = threading.Lock()
        return _write_locks[key]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write data as JSON atomically with retry on filesystem lock contention."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, default=str) + "\n"
    lock = _get_write_lock(path)

    for attempt in range(4):
        try:
            with lock:
                fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
                try:
                    os.write(fd, content.encode("utf-8"))
                    os.fsync(fd)
                    os.close(fd)
                    os.replace(tmp, str(path))
                except BaseException:
                    os.close(fd)
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)
                    raise
            return
        except OSError as e:
            if e.errno in (errno.EDEADLK, errno.EAGAIN, errno.EACCES) and attempt < 3:
                time.sleep(0.05 * (2 ** attempt) + random.uniform(0, 0.02))
                continue
            raise


def _is_conflict_copy(name: str) -> bool:
    """Return True if filename looks like a Dropbox conflict copy."""
    return "(conflicted copy" in name


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def write_settings(sync_dir: str, settings: dict[str, Any]) -> None:
    """Merge *settings* into ``user/settings.json``."""
    path = _user_dir(sync_dir) / "settings.json"
    existing = _decrypt_dict(_read_json(path))
    existing.update(settings)
    existing["updated_at"] = _now_iso()
    existing["machine_id"] = config.MACHINE_ID
    _write_json(path, _encrypt_dict(existing))
    logger.info("Wrote settings to %s", path)


async def _write_with_edeadlk_retry(
    writer: Callable[..., None],
    *args: Any,
    label: str,
    user_message: str,
) -> bool:
    """Run a synchronous file writer in a thread, retrying on EDEADLK.

    Audit P1-11 (generalised): on macOS, Dropbox holds advisory locks on
    synced files (``settings.json``, ``state.json``, etc.) while mirroring.
    A write during that window fails with
    ``OSError(errno.EDEADLK, 'Resource deadlock avoided')`` (errno 35) and
    — because the caller previously logged and moved on — the user's
    update would be silently lost.

    This helper retries up to ``_EDEADLK_RETRY_ATTEMPTS`` times with
    exponential backoff (100 ms, 200 ms, 400 ms) specifically for EDEADLK.
    Other ``OSError``s re-raise immediately so genuine permission / disk
    issues are not masked. After final exhaustion, emits a structured
    warning with a caller-supplied ``user_message`` so the GUI can surface
    a meaningful error to the user.

    Parameters
    ----------
    writer : callable
        The synchronous write function (e.g. :func:`write_settings`,
        :func:`write_preferences`) to invoke on each attempt.
    *args : Any
        Positional arguments forwarded to ``writer``.
    label : str
        Short identifier used as a log-event prefix (e.g. ``"settings"``,
        ``"preferences"``). Log events are named
        ``{label}.sync_write_{state}`` for easy filtering.
    user_message : str
        Human-readable message attached to the final-failure log event so
        the GUI can render it verbatim.

    Returns
    -------
    bool
        ``True`` on success (including recovered-after-retry), ``False``
        after exhausting all attempts.
    """
    last_exc: OSError | None = None
    for attempt in range(_EDEADLK_RETRY_ATTEMPTS):
        try:
            await asyncio.to_thread(writer, *args)
            if attempt > 0:
                logger.info(
                    "%s.sync_write_recovered", label,
                    extra={"attempt": attempt + 1, "errno": errno.EDEADLK},
                )
            return True
        except OSError as exc:
            if exc.errno != errno.EDEADLK:
                # Not the lock-collision case we know how to retry — let
                # the caller see it (permission denied, disk full, etc).
                raise
            last_exc = exc
            if attempt == _EDEADLK_RETRY_ATTEMPTS - 1:
                break
            delay = _EDEADLK_BACKOFF_BASE_S * (2**attempt)
            logger.info(
                "%s.sync_write_edeadlk_retry", label,
                extra={"attempt": attempt + 1, "delay_s": delay},
            )
            await asyncio.sleep(delay)

    logger.warning(
        "%s.sync_write_failed_edeadlk", label,
        extra={
            "attempts": _EDEADLK_RETRY_ATTEMPTS,
            "errno": errno.EDEADLK,
            "error": str(last_exc) if last_exc else "EDEADLK",
            "user_message": user_message,
        },
    )
    return False


async def write_settings_with_retry(
    sync_dir: str, settings: dict[str, Any]
) -> bool:
    """EDEADLK-retrying wrapper for :func:`write_settings`. See
    :func:`_write_with_edeadlk_retry` for retry semantics."""
    return await _write_with_edeadlk_retry(
        write_settings, sync_dir, settings,
        label="settings",
        user_message=(
            "Settings were not saved to cloud sync — another process "
            "(likely Dropbox) held the file lock. Try again or pause "
            "Dropbox briefly."
        ),
    )


def read_settings(sync_dir: str) -> dict[str, Any]:
    """Read settings from ``user/settings.json``. Returns empty dict if missing."""
    return _decrypt_dict(_read_json(_user_dir(sync_dir) / "settings.json"))


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def write_conversation(sync_dir: str, conversation: dict[str, Any]) -> None:
    """Write a single conversation to ``user/conversations/{id}.json``."""
    conv_id = conversation.get("id")
    if not conv_id:
        raise ValueError("Conversation must have an 'id' field")
    conv = dict(conversation)
    conv["_synced_at"] = _now_iso()
    conv["_machine_id"] = config.MACHINE_ID
    path = _conversations_dir(sync_dir) / f"{conv_id}.json"
    _write_json(path, _encrypt_dict(conv))
    logger.info("Wrote conversation %s to %s", conv_id, path)


def read_conversations(sync_dir: str) -> list[dict[str, Any]]:
    """Read all conversations from ``user/conversations/``.

    Skips Dropbox conflict copies (files with ``(conflicted copy`` in the name).
    Returns empty list if directory is missing.
    """
    conv_dir = Path(sync_dir) / "user" / "conversations"
    if not conv_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for fp in sorted(conv_dir.glob("*.json")):
        if _is_conflict_copy(fp.name):
            logger.debug("Skipping conflict copy: %s", fp.name)
            continue
        data = _decrypt_dict(_read_json(fp))
        if data:
            results.append(data)
    return results


def read_conversation(sync_dir: str, conv_id: str) -> dict[str, Any]:
    """Read a single conversation by ID. Returns empty dict if missing."""
    path = Path(sync_dir) / "user" / "conversations" / f"{conv_id}.json"
    return _decrypt_dict(_read_json(path))


def delete_conversation(sync_dir: str, conv_id: str) -> None:
    """Delete a conversation file. No-op if the file does not exist."""
    path = Path(sync_dir) / "user" / "conversations" / f"{conv_id}.json"
    try:
        path.unlink()
        logger.info("Deleted conversation %s", conv_id)
    except FileNotFoundError:
        logger.debug("Conversation %s not found, nothing to delete", conv_id)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def write_preferences(sync_dir: str, preferences: dict[str, Any]) -> None:
    """Merge *preferences* into ``user/state.json``."""
    path = _user_dir(sync_dir) / "state.json"
    existing = _decrypt_dict(_read_json(path))
    existing.update(preferences)
    existing["updated_at"] = _now_iso()
    existing["machine_id"] = config.MACHINE_ID
    _write_json(path, _encrypt_dict(existing))
    logger.info("Wrote preferences to %s", path)


async def write_preferences_with_retry(
    sync_dir: str, preferences: dict[str, Any]
) -> bool:
    """EDEADLK-retrying wrapper for :func:`write_preferences`. See
    :func:`_write_with_edeadlk_retry` for retry semantics.

    Beta-test regression (2026-04-18): ``PATCH /user-state/preferences``
    was returning HTTP 500 on macOS because ``state.json`` shares the same
    Dropbox-lock-collision pattern as ``settings.json``. This wrapper
    brings preferences onto the same retry path as settings.
    """
    return await _write_with_edeadlk_retry(
        write_preferences, sync_dir, preferences,
        label="preferences",
        user_message=(
            "UI preferences were not saved to cloud sync — another process "
            "(likely Dropbox) held the file lock. Try again or pause "
            "Dropbox briefly."
        ),
    )


def read_preferences(sync_dir: str) -> dict[str, Any]:
    """Read UI preferences from ``user/state.json``. Returns empty dict if missing."""
    return _decrypt_dict(_read_json(_user_dir(sync_dir) / "state.json"))
