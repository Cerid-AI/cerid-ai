#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI - Clipboard Capture Daemon

Monitors the macOS clipboard and ingests qualifying content into the Cerid KB
via the webhook endpoint.

Configuration (env vars):
    CERID_CLIPBOARD_ENABLED      Enable the daemon (default: false)
    CERID_CLIPBOARD_MIN_LENGTH   Minimum content length (default: 50)
    CERID_CLIPBOARD_MAX_LENGTH   Maximum content length (default: 50000)
    CERID_CLIPBOARD_POLL_SECONDS Poll interval in seconds (default: 2)
    CERID_CLIPBOARD_API_URL      Cerid API base URL (default: http://localhost:8888)

Usage:
    python scripts/clipboard_daemon.py          # run (requires CERID_CLIPBOARD_ENABLED=true)
    python scripts/clipboard_daemon.py --once   # single poll cycle then exit (for testing)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from typing import NoReturn
from urllib.parse import urlparse

import httpx
import redis

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENABLED = os.getenv("CERID_CLIPBOARD_ENABLED", "false").lower() == "true"
MIN_LENGTH = int(os.getenv("CERID_CLIPBOARD_MIN_LENGTH", "50"))
MAX_LENGTH = int(os.getenv("CERID_CLIPBOARD_MAX_LENGTH", "50000"))
POLL_SECONDS = float(os.getenv("CERID_CLIPBOARD_POLL_SECONDS", "2"))
API_URL = os.getenv("CERID_CLIPBOARD_API_URL", "http://localhost:8888").rstrip("/")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

SEEN_SET_KEY = "cerid:clipboard:seen"
ALIVE_KEY = "cerid:clipboard:alive"
SEEN_TTL_DAYS = 7
HEARTBEAT_TTL_SECONDS = 10

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [clipboard] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("clipboard-daemon")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %d — shutting down gracefully", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def connect_redis() -> redis.Redis:
    """Connect to Redis with optional password."""
    kwargs: dict = {
        "decode_responses": True,
        "socket_connect_timeout": 5,
    }
    if REDIS_PASSWORD:
        kwargs["password"] = REDIS_PASSWORD
    client = redis.from_url(REDIS_URL, **kwargs)
    client.ping()
    return client


def is_seen(r: redis.Redis, content_hash: str) -> bool:
    """Check if content hash has already been ingested."""
    return bool(r.sismember(SEEN_SET_KEY, content_hash))


def mark_seen(r: redis.Redis, content_hash: str) -> None:
    """Add content hash to the seen set with TTL refresh."""
    r.sadd(SEEN_SET_KEY, content_hash)
    # Refresh TTL on the whole set (approximate per-member TTL)
    r.expire(SEEN_SET_KEY, SEEN_TTL_DAYS * 86400)


def heartbeat(r: redis.Redis) -> None:
    """Write heartbeat key so monitors know we are alive."""
    r.set(ALIVE_KEY, "1", ex=HEARTBEAT_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

CODE_PATTERNS = re.compile(
    r"(?:^|\s)(?:def |function |class |import |from .+ import |const |let |var |=> \{|#include )"
)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_content_type(text: str) -> tuple[str, str | None]:
    """Return (domain, fetched_text_or_None).

    For URLs, attempts to fetch page content. Returns the stripped page text
    as the second element so the caller can ingest the fetched content instead
    of the raw URL.
    """
    stripped = text.strip()

    # URL detection
    if stripped.startswith(("http://", "https://")):
        parsed = urlparse(stripped)
        if parsed.scheme and parsed.netloc:
            try:
                resp = httpx.get(stripped, timeout=10.0, follow_redirects=True)
                resp.raise_for_status()
                # Crude HTML stripping — good enough for ingestion
                page_text = _strip_html(resp.text)
                if len(page_text) >= MIN_LENGTH:
                    return "general", page_text
            except (httpx.HTTPError, OSError) as e:
                logger.warning("Failed to fetch URL %s: %s", stripped[:80], e)
            # Fall through: ingest the URL string itself if fetch failed
            return "general", None

    # Code detection
    if CODE_PATTERNS.search(text):
        return "code", None

    # Default: prose
    return "general", None


def _strip_html(html: str) -> str:
    """Rough HTML-to-text conversion for ingestion."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Clipboard read
# ---------------------------------------------------------------------------


def read_clipboard() -> str:
    """Read current macOS clipboard via pbpaste."""
    try:
        proc = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5,
        )
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("pbpaste failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Ingest via API
# ---------------------------------------------------------------------------


def post_to_cerid(text: str, domain: str, source: str = "clipboard") -> dict | None:
    """POST content to the Cerid webhook endpoint."""
    url = f"{API_URL}/ingest/webhook"
    payload = {
        "text": text,
        "source": source,
        "domain": domain,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, OSError) as e:
        logger.error("Failed to POST to %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def poll_once(r: redis.Redis) -> bool:
    """Run a single poll cycle. Returns True if content was ingested."""
    text = read_clipboard()
    if not text:
        return False

    text = text.strip()

    # Length filters
    if len(text) < MIN_LENGTH:
        return False
    if len(text) > MAX_LENGTH:
        logger.debug("Clipboard too large (%d chars), skipping", len(text))
        return False

    # Dedup
    h = content_hash(text)
    if is_seen(r, h):
        return False

    # Content type detection (may fetch URL content)
    domain, fetched_text = detect_content_type(text)
    ingest_text = fetched_text if fetched_text else text

    # Post to Cerid
    result = post_to_cerid(ingest_text, domain)
    if result and result.get("status") in ("success", "ingested"):
        mark_seen(r, h)
        logger.info(
            "Ingested clipboard (%d chars, domain=%s, artifact=%s)",
            len(ingest_text),
            domain,
            result.get("artifact_id", "?"),
        )
        return True

    return False


def run_daemon() -> NoReturn:
    """Main daemon loop."""
    logger.info("Clipboard daemon starting (poll=%.1fs, min=%d, max=%d)", POLL_SECONDS, MIN_LENGTH, MAX_LENGTH)

    try:
        r = connect_redis()
        logger.info("Redis connected (%s)", REDIS_URL)
    except Exception as e:
        logger.error("Cannot connect to Redis: %s", e)
        sys.exit(1)

    while not _shutdown:
        try:
            heartbeat(r)
            poll_once(r)
        except redis.ConnectionError:
            logger.warning("Redis connection lost, reconnecting...")
            try:
                r = connect_redis()
            except Exception:
                pass
        except Exception as e:
            logger.error("Poll cycle error: %s", e)

        # Interruptible sleep
        deadline = time.monotonic() + POLL_SECONDS
        while time.monotonic() < deadline and not _shutdown:
            time.sleep(0.25)

    logger.info("Clipboard daemon stopped")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if sys.platform != "darwin":
        logger.error("Clipboard daemon requires macOS (pbpaste)")
        return 1

    if "--once" in sys.argv:
        # Single poll for testing — doesn't require ENABLED
        try:
            r = connect_redis()
        except Exception as e:
            logger.error("Cannot connect to Redis: %s", e)
            return 1
        ingested = poll_once(r)
        return 0 if ingested else 1

    if not ENABLED:
        logger.error(
            "Clipboard daemon is disabled. Set CERID_CLIPBOARD_ENABLED=true to enable."
        )
        return 1

    run_daemon()
    return 0  # unreachable, but satisfies type checker


if __name__ == "__main__":
    sys.exit(main())
