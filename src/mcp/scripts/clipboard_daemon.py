#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid AI - Clipboard Capture Daemon (in-process variant)

Monitors the macOS clipboard for new content and ingests qualifying text
directly via ``ingest_content()`` (when running in-process) or via the
``POST /ingest/webhook`` endpoint (when running standalone).

Runs on the HOST (not inside Docker). Requires macOS (pbpaste).

Usage:
    python src/mcp/scripts/clipboard_daemon.py            # daemon mode
    python src/mcp/scripts/clipboard_daemon.py --once      # single poll (testing)
    python src/mcp/scripts/clipboard_daemon.py --api-only  # use HTTP API, skip in-process

Configuration (env vars):
    CERID_CLIPBOARD_ENABLED       Enable the daemon (default: false)
    CERID_CLIPBOARD_MIN_LENGTH    Min content length to ingest (default: 50)
    CERID_CLIPBOARD_POLL_SECONDS  Poll interval in seconds (default: 2)
    CERID_PORT_MCP                MCP server port for API mode (default: 8888)
    CERID_API_KEY                 API key for webhook auth (optional)
    CERID_WEBHOOK_SECRET          Webhook secret for auth (optional)
    REDIS_URL                     Redis URL for dedup/heartbeat (default: redis://localhost:6379)
    REDIS_PASSWORD                Redis password (optional)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import urlparse

if TYPE_CHECKING:
    import redis

# Add parent dir so we can import project modules when running standalone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("cerid.clipboard")

# ── Configuration ─────────────────────────────────────────────────────────────

ENABLED = os.getenv("CERID_CLIPBOARD_ENABLED", "false").lower() == "true"
MIN_LENGTH = int(os.getenv("CERID_CLIPBOARD_MIN_LENGTH", "50"))
MAX_LENGTH = 50_000
POLL_SECONDS = float(os.getenv("CERID_CLIPBOARD_POLL_SECONDS", "2"))
MCP_PORT = os.getenv("CERID_PORT_MCP", "8888")
API_URL = f"http://localhost:{MCP_PORT}"
API_KEY = os.getenv("CERID_API_KEY", "")
WEBHOOK_SECRET = os.getenv("CERID_WEBHOOK_SECRET", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

SEEN_SET_KEY = "cerid:clipboard:seen"
HEARTBEAT_KEY = "cerid:clipboard:alive"
SEEN_TTL_DAYS = 7
HEARTBEAT_TTL = 10  # seconds

CODE_PATTERNS = re.compile(
    r"(?:^|\s)(?:def |function |class |import |from .+ import |const |let |var |=> \{|#include |func |pub fn )"
)

# ── Globals ───────────────────────────────────────────────────────────────────

_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown  # noqa: PLW0603
    logger.info("Received signal %d — shutting down gracefully", signum)
    _shutdown = True


# ── Redis helpers ─────────────────────────────────────────────────────────────


def _connect_redis() -> redis.Redis[Any]:
    """Connect to Redis with optional password."""
    import redis as _redis

    kwargs: dict[str, Any] = {"decode_responses": True, "socket_connect_timeout": 5}
    if REDIS_PASSWORD:
        kwargs["password"] = REDIS_PASSWORD
    client: redis.Redis[Any] = _redis.from_url(REDIS_URL, **kwargs)
    client.ping()
    return client


def _is_seen(r: redis.Redis[Any], content_hash: str) -> bool:
    return bool(r.sismember(SEEN_SET_KEY, content_hash))


def _mark_seen(r: redis.Redis[Any], content_hash: str) -> None:
    r.sadd(SEEN_SET_KEY, content_hash)
    r.expire(SEEN_SET_KEY, SEEN_TTL_DAYS * 86400)


def _heartbeat(r: redis.Redis[Any]) -> None:
    r.set(HEARTBEAT_KEY, str(int(time.time())), ex=HEARTBEAT_TTL)


# ── Content helpers ───────────────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_html(html: str) -> str:
    """Rough HTML-to-text conversion for URL content."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _detect_content_type(text: str) -> tuple[str, str | None]:
    """Return (domain, fetched_text_or_None).

    For URLs: attempts to fetch page content. Returns the stripped page text
    so the caller can ingest the fetched content instead of the raw URL.
    """
    stripped = text.strip()

    # URL detection
    if stripped.startswith(("http://", "https://")):
        parsed = urlparse(stripped)
        if parsed.scheme and parsed.netloc:
            try:
                import httpx
                resp = httpx.get(stripped, timeout=10.0, follow_redirects=True)
                resp.raise_for_status()
                page_text = _strip_html(resp.text)
                if len(page_text) >= MIN_LENGTH:
                    return "general", page_text
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to fetch URL %s: %s", stripped[:80], e)
            return "general", None

    # Code detection
    if CODE_PATTERNS.search(text):
        return "code", None

    # Default: prose
    return "general", None


def _detect_source_type(text: str) -> str:
    if text.strip().startswith(("http://", "https://")):
        return "clipboard_url"
    return "clipboard"


# ── Clipboard read ────────────────────────────────────────────────────────────


def _read_clipboard() -> str:
    """Read current macOS clipboard via pbpaste."""
    try:
        proc = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("pbpaste failed: %s", e)
        return ""


# ── Ingestion ─────────────────────────────────────────────────────────────────


def _ingest_via_api(text: str, domain: str) -> dict | None:
    """POST content to the Cerid webhook endpoint."""
    import httpx

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    elif WEBHOOK_SECRET:
        headers["X-Webhook-Secret"] = WEBHOOK_SECRET

    payload = {
        "text": text,
        "source": "clipboard-daemon",
        "domain": domain,
        "metadata": {"source_type": _detect_source_type(text)},
    }
    try:
        resp = httpx.post(f"{API_URL}/ingest/webhook", json=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, OSError) as e:
        logger.error("Webhook POST failed: %s", e)
        return None


def _ingest_in_process(text: str, domain: str) -> dict | None:
    """Directly call ingest_content() when running in the MCP process."""
    try:
        from services.ingestion import ingest_content
        meta = {
            "client_source": "clipboard-daemon",
            "webhook_source": "clipboard-daemon",
            "source_type": _detect_source_type(text),
        }
        return ingest_content(text, domain, meta)
    except Exception as e:  # noqa: BLE001
        logger.error("In-process ingest failed: %s", e)
        return None


# ── Poll cycle ────────────────────────────────────────────────────────────────


def poll_once(r: redis.Redis[Any], *, api_only: bool = False) -> bool:
    """Run a single poll cycle. Returns True if content was ingested."""
    text = _read_clipboard()
    if not text:
        return False

    text = text.strip()

    # Length filters
    if len(text) < MIN_LENGTH or len(text) > MAX_LENGTH:
        return False

    # Dedup
    h = _content_hash(text)
    if _is_seen(r, h):
        return False

    # Content type detection (may fetch URL content)
    domain, fetched_text = _detect_content_type(text)
    ingest_text = fetched_text if fetched_text else text

    # Choose ingestion path
    if api_only:
        result = _ingest_via_api(ingest_text, domain)
    else:
        result = _ingest_in_process(ingest_text, domain)

    if result and result.get("status") in ("success", "ingested"):
        _mark_seen(r, h)
        logger.info(
            "Ingested clipboard (%d chars, domain=%s, artifact=%s)",
            len(ingest_text), domain, result.get("artifact_id", "?"),
        )
        return True

    return False


# ── Main loop ─────────────────────────────────────────────────────────────────


def run_daemon(*, api_only: bool = False) -> NoReturn:
    """Main daemon loop."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "Clipboard daemon starting (poll=%.1fs, min=%d, max=%d, mode=%s)",
        POLL_SECONDS, MIN_LENGTH, MAX_LENGTH,
        "api" if api_only else "in-process",
    )

    try:
        r = _connect_redis()
        logger.info("Redis connected (%s)", REDIS_URL)
    except Exception as e:  # noqa: BLE001
        logger.error("Cannot connect to Redis: %s", e)
        sys.exit(1)

    while not _shutdown:
        try:
            _heartbeat(r)
            poll_once(r, api_only=api_only)
        except Exception as e:  # noqa: BLE001
            logger.error("Poll cycle error: %s", e)
            # Reconnect on Redis connection loss
            try:
                import redis as _redis
                if isinstance(e, _redis.ConnectionError):
                    logger.warning("Redis connection lost, reconnecting...")
                    r = _connect_redis()
            except Exception:  # noqa: BLE001
                pass

        # Interruptible sleep
        deadline = time.monotonic() + POLL_SECONDS
        while time.monotonic() < deadline and not _shutdown:
            time.sleep(0.25)

    logger.info("Clipboard daemon stopped")
    sys.exit(0)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    if sys.platform != "darwin":
        logger.error("Clipboard daemon requires macOS (pbpaste)")
        return 1

    api_only = "--api-only" in sys.argv

    if "--once" in sys.argv:
        # Single poll for testing — doesn't require ENABLED
        try:
            r = _connect_redis()
        except Exception as e:  # noqa: BLE001
            logger.error("Cannot connect to Redis: %s", e)
            return 1
        ingested = poll_once(r, api_only=api_only)
        return 0 if ingested else 1

    if not ENABLED:
        logger.error(
            "Clipboard daemon is disabled. Set CERID_CLIPBOARD_ENABLED=true to enable."
        )
        return 1

    run_daemon(api_only=api_only)
    return 0  # unreachable but satisfies type checker


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sys.exit(main())
