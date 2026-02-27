#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI - Watch Folder Ingestion Script

Monitors ~/cerid-archive/ (or WATCH_FOLDER) for new files and auto-ingests them
into the MCP knowledge base via the /ingest_file API.

Runs on the HOST (not inside Docker). Translates host paths to container paths.

Usage:
    python src/mcp/scripts/watch_ingest.py [--mode smart|pro|manual]

Domain Detection:
    ~/cerid-archive/coding/file.py   → domain="coding", mode="manual"
    ~/cerid-archive/inbox/file.pdf   → domain="", mode=<default or --mode>
    ~/cerid-archive/unknown/file.txt → domain="", mode=<default or --mode>
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

# Debounce: track recently processed files to avoid duplicate events
_recent: dict[str, float] = {}
DEBOUNCE_SECONDS = 2.0
# File stability: wait for file size to stop changing before ingesting
STABILITY_CHECKS = 5
STABILITY_INTERVAL = 2.0  # seconds between size checks (max wait ~30s)
# Retry: failed files get one retry after this delay
RETRY_DELAY = 30.0
_retry_queue: list[tuple[str, str, float]] = []  # (host_path, mode, retry_after)


def _log(level: str, msg: str):
    """Simple colored logging."""
    colors = {"INFO": "\033[92m", "WARN": "\033[93m", "ERROR": "\033[91m"}
    reset = "\033[0m"
    ts = datetime.now().strftime("%H:%M:%S")
    color = colors.get(level, "")
    print(f"{color}[{ts}] [{level}] {msg}{reset}")


def _translate_path(host_path: str) -> str:
    """Translate host path to container path (/archive/...)."""
    watch = config.WATCH_FOLDER.rstrip("/")
    archive = config.ARCHIVE_PATH.rstrip("/")
    if host_path.startswith(watch):
        return host_path.replace(watch, archive, 1)
    return host_path


def _detect_domain(host_path: str) -> tuple[str, str]:
    """
    Detect domain and sub-category from folder structure.

    ~/cerid-archive/coding/python/file.py → ("coding", "python")
    ~/cerid-archive/coding/file.py        → ("coding", "")
    ~/cerid-archive/inbox/file.pdf        → ("", "")
    """
    watch = config.WATCH_FOLDER.rstrip("/")
    rel = os.path.relpath(host_path, watch)
    parts = rel.split(os.sep)
    if len(parts) >= 2:
        folder = parts[0].lower()
        if folder in config.DOMAINS:
            # Check for sub-category (second-level folder)
            if len(parts) >= 3:
                sub_cat = parts[1].lower()
                domain_info = config.TAXONOMY.get(folder, {})
                valid_subs = [s.lower() for s in domain_info.get("sub_categories", [])]
                if sub_cat in valid_subs:
                    return folder, sub_cat
            return folder, ""
    return "", ""


def _should_process(file_path: str) -> bool:
    """Check if file should be processed (extension + debounce)."""
    ext = Path(file_path).suffix.lower()
    if ext not in config.SUPPORTED_EXTENSIONS:
        return False

    # Skip hidden files and directories
    if any(p.startswith(".") for p in Path(file_path).parts):
        return False

    # Debounce: skip if same file was processed very recently
    now = time.time()
    if file_path in _recent and (now - _recent[file_path]) < DEBOUNCE_SECONDS:
        return False
    _recent[file_path] = now

    # Prune old debounce entries (prevent unbounded growth)
    stale = [k for k, v in _recent.items() if (now - v) > 60]
    for k in stale:
        del _recent[k]

    return True


def _wait_for_stable(file_path: str) -> bool:
    """
    Wait for file size to stabilize (file write to complete).
    Returns True if file is stable, False if file disappeared or stayed unstable.
    """
    prev_size = -1
    stable_count = 0
    for _ in range(STABILITY_CHECKS * 3):  # max total wait ~9s
        try:
            size = os.path.getsize(file_path)
        except OSError:
            return False  # file disappeared
        if size == prev_size and size > 0:
            stable_count += 1
            if stable_count >= STABILITY_CHECKS:
                return True
        else:
            stable_count = 0
        prev_size = size
        time.sleep(STABILITY_INTERVAL)
    _log("WARN", f"  File did not stabilize after {STABILITY_CHECKS * STABILITY_INTERVAL * 3:.0f}s: {Path(file_path).name}")
    # Still return True to attempt ingestion — the server will report parse errors
    return True


def _schedule_retry(host_path: str, mode: str):
    """Schedule a failed file for one retry after RETRY_DELAY seconds."""
    # Don't schedule if already in retry queue
    for path, _, _ in _retry_queue:
        if path == host_path:
            return
    retry_at = time.time() + RETRY_DELAY
    _retry_queue.append((host_path, mode, retry_at))
    _log("WARN", f"  Scheduled retry in {RETRY_DELAY:.0f}s: {Path(host_path).name}")


def _process_retries():
    """Process any pending retries that are due. Called from the main loop."""
    now = time.time()
    due = [(p, m) for p, m, t in _retry_queue if now >= t]
    # Remove due items from queue
    _retry_queue[:] = [(p, m, t) for p, m, t in _retry_queue if now < t]
    for host_path, mode in due:
        _log("INFO", f"Retrying: {Path(host_path).name}")
        # Reset debounce so file can be reprocessed
        _recent.pop(host_path, None)
        ingest_file(host_path, mode)


def ingest_file(host_path: str, default_mode: str):
    """Send a file to the MCP /ingest_file endpoint."""
    if not _should_process(host_path):
        return

    # Wait for file write to complete (race condition prevention)
    if not _wait_for_stable(host_path):
        _log("WARN", f"  Skipping (file disappeared): {Path(host_path).name}")
        return

    domain, sub_category = _detect_domain(host_path)
    container_path = _translate_path(host_path)
    mode = "manual" if domain else default_mode

    filename = Path(host_path).name
    sub_info = f"/{sub_category}" if sub_category else ""
    _log("INFO", f"Ingesting: {filename} → domain={domain or '(AI)'}{sub_info}, mode={mode}")

    try:
        payload = {
            "file_path": container_path,
            "domain": domain,
            "categorize_mode": mode,
        }
        if sub_category:
            payload["sub_category"] = sub_category
        resp = requests.post(
            f"{MCP_URL}/ingest_file",
            json=payload,
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "success")
            if status == "duplicate":
                dup_of = data.get("duplicate_of", "?")
                _log("INFO", f"  ⊘ {filename}: duplicate of '{dup_of}' (skipped)")
            else:
                assigned_domain = data.get("domain", "?")
                chunks = data.get("chunks", 0)
                cat_mode = data.get("categorize_mode", mode)
                _log("INFO", f"  ✓ {filename} → {assigned_domain} ({chunks} chunks, {cat_mode})")
        else:
            _log("ERROR", f"  ✗ {filename}: HTTP {resp.status_code} - {resp.text[:200]}")
            _schedule_retry(host_path, default_mode)
    except requests.RequestException as e:
        _log("ERROR", f"  ✗ {filename}: {e}")
        _schedule_retry(host_path, default_mode)


def main():
    parser = argparse.ArgumentParser(description="Watch folder for auto-ingestion")
    parser.add_argument(
        "--mode",
        choices=["manual", "smart", "pro"],
        default=config.CATEGORIZE_MODE,
        help=f"Default categorization mode for inbox/unknown files (default: {config.CATEGORIZE_MODE})",
    )
    parser.add_argument(
        "--folder",
        default=config.WATCH_FOLDER,
        help=f"Folder to watch (default: {config.WATCH_FOLDER})",
    )
    args = parser.parse_args()

    watch_path = os.path.expanduser(args.folder)
    if not os.path.isdir(watch_path):
        print(f"ERROR: Watch folder does not exist: {watch_path}")
        sys.exit(1)

    # Import watchdog here so config import doesn't fail if watchdog not installed
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("ERROR: watchdog not installed. Run: pip install watchdog")
        sys.exit(1)

    class IngestHandler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                ingest_file(event.src_path, args.mode)

        def on_modified(self, event):
            if not event.is_directory:
                ingest_file(event.src_path, args.mode)

        def on_moved(self, event):
            if not event.is_directory:
                ingest_file(event.dest_path, args.mode)

    observer = Observer()
    observer.schedule(IngestHandler(), path=watch_path, recursive=True)
    observer.start()

    _log("INFO", f"Watching: {watch_path}")
    _log("INFO", f"Default mode: {args.mode}")
    _log("INFO", f"Domains: {', '.join(config.DOMAINS)}")
    _log("INFO", f"MCP URL: {MCP_URL}")
    _log("INFO", "Press Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
            _process_retries()
    except KeyboardInterrupt:
        observer.stop()
        _log("INFO", "Watcher stopped")
    observer.join()


if __name__ == "__main__":
    main()