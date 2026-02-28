#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI - Obsidian Vault Watcher

Monitors an Obsidian vault for changes and auto-ingests modified markdown files
into the personal knowledge base via the MCP /ingest_file API.

Runs on the HOST (not inside Docker). Pairs with watch_ingest.py for archive files.

Features:
- Watches .md files only (Obsidian's native format)
- Detects create, modify, and rename events
- Debounces rapid saves (Obsidian auto-saves frequently)
- Maps vault path to a configurable domain (default: personal)
- Skips hidden files (.obsidian/, .trash/, etc.)
- File stability detection for large files
- Retry queue for transient failures

Usage:
    python src/mcp/scripts/watch_obsidian.py --vault ~/Obsidian/MyVault [--domain personal] [--mode manual]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Add parent dir so we can import config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

MCP_URL = os.getenv("MCP_URL", "http://localhost:8888")

# Debounce: Obsidian auto-saves frequently, avoid duplicate ingests
_recent: dict[str, float] = {}
DEBOUNCE_SECONDS = 5.0  # Higher than archive watcher due to Obsidian auto-save

# File stability
STABILITY_CHECKS = 3
STABILITY_INTERVAL = 1.0

# Retry
RETRY_DELAY = 30.0
_retry_queue: list[tuple[str, str, str, float]] = []  # (path, domain, mode, retry_after)


def _log(level: str, msg: str):
    """Simple colored logging."""
    colors = {"INFO": "\033[96m", "WARN": "\033[93m", "ERROR": "\033[91m"}
    reset = "\033[0m"
    ts = datetime.now().strftime("%H:%M:%S")
    color = colors.get(level, "")
    print(f"{color}[{ts}] [OBSIDIAN] [{level}] {msg}{reset}")


def _should_process(file_path: str) -> bool:
    """Check if file should be processed."""
    path = Path(file_path)

    # Only markdown files
    if path.suffix.lower() != ".md":
        return False

    # Skip hidden files/dirs (.obsidian, .trash, etc.)
    if any(p.startswith(".") for p in path.parts):
        return False

    # Skip very small files (likely empty templates)
    try:
        if path.stat().st_size < 10:
            return False
    except OSError:
        return False

    # Debounce
    now = time.time()
    if file_path in _recent and (now - _recent[file_path]) < DEBOUNCE_SECONDS:
        return False
    _recent[file_path] = now

    # Prune old debounce entries
    stale = [k for k, v in _recent.items() if (now - v) > 60]
    for k in stale:
        del _recent[k]

    return True


def _wait_for_stable(file_path: str) -> bool:
    """Wait for file size to stabilize."""
    prev_size = -1
    stable_count = 0
    for _ in range(STABILITY_CHECKS * 3):
        try:
            size = os.path.getsize(file_path)
        except OSError:
            return False
        if size == prev_size and size > 0:
            stable_count += 1
            if stable_count >= STABILITY_CHECKS:
                return True
        else:
            stable_count = 0
        prev_size = size
        time.sleep(STABILITY_INTERVAL)
    return True


def _schedule_retry(file_path: str, domain: str, mode: str):
    """Schedule a retry for a failed file."""
    for p, _, _, _ in _retry_queue:
        if p == file_path:
            return
    retry_at = time.time() + RETRY_DELAY
    _retry_queue.append((file_path, domain, mode, retry_at))
    _log("WARN", f"  Scheduled retry in {RETRY_DELAY:.0f}s: {Path(file_path).name}")


def _process_retries():
    """Process pending retries."""
    now = time.time()
    due = [(p, d, m) for p, d, m, t in _retry_queue if now >= t]
    _retry_queue[:] = [(p, d, m, t) for p, d, m, t in _retry_queue if now < t]
    for file_path, domain, mode in due:
        _log("INFO", f"Retrying: {Path(file_path).name}")
        _recent.pop(file_path, None)
        ingest_note(file_path, domain, mode)


def ingest_note(file_path: str, domain: str, mode: str):
    """Send a markdown file to the MCP /ingest_file endpoint."""
    if not _should_process(file_path):
        return

    if not _wait_for_stable(file_path):
        _log("WARN", f"  Skipping (file disappeared): {Path(file_path).name}")
        return

    filename = Path(file_path).name
    _log("INFO", f"Ingesting: {filename} → domain={domain}")

    # For Obsidian, we read the file and send content directly
    # (vault isn't mounted in Docker like cerid-archive is)
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        _log("ERROR", f"  Failed to read: {filename}: {e}")
        return

    try:
        resp = requests.post(
            f"{MCP_URL}/ingest",
            json={
                "content": text,
                "domain": domain,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "success")
            if status == "duplicate":
                _log("INFO", f"  = {filename}: unchanged (duplicate)")
            else:
                chunks = data.get("chunks", 0)
                _log("INFO", f"  + {filename} → {domain} ({chunks} chunks)")
        else:
            _log("ERROR", f"  ! {filename}: HTTP {resp.status_code} - {resp.text[:200]}")
            _schedule_retry(file_path, domain, mode)
    except requests.RequestException as e:
        _log("ERROR", f"  ! {filename}: {e}")
        _schedule_retry(file_path, domain, mode)


def main():
    parser = argparse.ArgumentParser(description="Watch Obsidian vault for auto-ingestion")
    parser.add_argument(
        "--vault",
        required=True,
        help="Path to Obsidian vault (e.g. ~/Obsidian/MyVault)",
    )
    parser.add_argument(
        "--domain",
        default="personal",
        choices=config.DOMAINS,
        help="Target domain for vault notes (default: personal)",
    )
    parser.add_argument(
        "--mode",
        default="manual",
        choices=["manual", "smart", "pro"],
        help="Categorization mode (default: manual — notes go to specified domain)",
    )
    args = parser.parse_args()

    vault_path = os.path.expanduser(args.vault)
    if not os.path.isdir(vault_path):
        print(f"ERROR: Vault not found: {vault_path}")
        sys.exit(1)

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("ERROR: watchdog not installed. Run: pip install watchdog")
        sys.exit(1)

    class ObsidianHandler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                ingest_note(event.src_path, args.domain, args.mode)

        def on_modified(self, event):
            if not event.is_directory:
                ingest_note(event.src_path, args.domain, args.mode)

        def on_moved(self, event):
            if not event.is_directory:
                ingest_note(event.dest_path, args.domain, args.mode)

    observer = Observer()
    observer.schedule(ObsidianHandler(), path=vault_path, recursive=True)
    observer.start()

    _log("INFO", f"Watching vault: {vault_path}")
    _log("INFO", f"Domain: {args.domain}")
    _log("INFO", f"Mode: {args.mode}")
    _log("INFO", f"MCP URL: {MCP_URL}")
    _log("INFO", "Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
            _process_retries()
    except KeyboardInterrupt:
        observer.stop()
        _log("INFO", "Obsidian watcher stopped")
    observer.join()


if __name__ == "__main__":
    main()
