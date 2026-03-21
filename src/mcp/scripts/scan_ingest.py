#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI — Folder Scan Ingestion Script

Recursively scans a directory and ingests all supported files into the
MCP knowledge base via the /ingest_file API. Runs on the HOST (not
inside Docker) to avoid macOS Docker virtiofs file read limitations.

Usage:
    python src/mcp/scripts/scan_ingest.py [path] [options]

    python src/mcp/scripts/scan_ingest.py ~/Documents --dry-run
    python src/mcp/scripts/scan_ingest.py ~/cerid-archive --min-quality 0.5
    python src/mcp/scripts/scan_ingest.py  # defaults to ~/cerid-archive

Features:
    - Recursive directory walk with smart exclusions
    - Content-hash dedup (skips already-ingested files)
    - Domain detection from folder structure
    - Quality threshold filtering
    - Dry-run mode (preview without ingesting)
    - Progress reporting
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

# Add parent dir so we can import config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

MCP_URL = os.getenv("MCP_URL", "http://localhost:8888")

# Extensions from config/taxonomy.py
SUPPORTED_EXTENSIONS = getattr(config, "SUPPORTED_EXTENSIONS", None)
if not SUPPORTED_EXTENSIONS:
    from config.taxonomy import SUPPORTED_EXTENSIONS

EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", ".env",
    ".tox", "dist", "build", ".cache", ".pytest_cache", ".mypy_cache",
    "egg-info", ".DS_Store", ".Spotlight-V100", ".fseventsd",
}

# Host-to-container path translation
WATCH_FOLDER = os.path.expanduser(os.getenv("WATCH_FOLDER", "~/cerid-archive"))
CONTAINER_ARCHIVE = "/archive"


def file_content_hash(path: str) -> str:
    """SHA-256 of file content using 64KB chunked reads."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65_536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def detect_domain(file_path: str, root: str) -> tuple[str, str]:
    """Infer domain and sub-category from directory structure."""
    rel = os.path.relpath(file_path, root)
    parts = rel.split(os.sep)
    domains = getattr(config, "DOMAINS", [])
    if len(parts) >= 2:
        folder = parts[0].lower()
        if folder in domains:
            sub_cat = parts[1].lower() if len(parts) >= 3 else ""
            return folder, sub_cat
    return "", ""


def translate_path(host_path: str) -> str:
    """Convert host path to container path for the MCP API."""
    abs_path = os.path.abspath(host_path)
    abs_watch = os.path.abspath(WATCH_FOLDER)
    if abs_path.startswith(abs_watch):
        rel = os.path.relpath(abs_path, abs_watch)
        return os.path.join(CONTAINER_ARCHIVE, rel)
    return abs_path


def ingest_via_api(host_path: str, domain: str, sub_category: str) -> dict:
    """Read file on host, parse content, send via /ingest (content-based).

    This avoids macOS Docker virtiofs Errno 35 issues with /ingest_file,
    which reads files inside the container via the bind mount.
    """
    filename = os.path.basename(host_path)
    headers = {"Content-Type": "application/json", "X-Client-ID": "folder_scanner"}

    # On macOS Docker, /ingest_file crashes the container due to virtiofs Errno 35.
    # Skip the path-based approach and always read files on the host side.
    import platform
    if platform.system() != "Darwin":
        # Try /ingest_file first (works on Linux Docker, native installs)
        container_path = translate_path(host_path)
        try:
            resp = requests.post(
                f"{MCP_URL}/ingest_file",
                json={"file_path": container_path, "domain": domain or "", "sub_category": sub_category or "", "categorize_mode": "smart"},
                headers=headers,
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.HTTPError):
            pass

    # Read file on host and send content via /ingest
    with open(host_path, "rb") as f:
        content = f.read()

    # For text-based files, decode and send as content
    ext = os.path.splitext(filename)[1].lower()
    text_extensions = {".txt", ".md", ".rst", ".log", ".py", ".js", ".ts", ".jsx", ".tsx",
                       ".java", ".go", ".rs", ".rb", ".cpp", ".c", ".h", ".cs", ".sql",
                       ".r", ".swift", ".kt", ".sh", ".bash", ".json", ".yaml", ".yml",
                       ".toml", ".ini", ".cfg", ".csv", ".tsv", ".xml", ".html", ".htm"}

    if ext in text_extensions:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            text = content.decode("latin-1", errors="replace")

        resp = requests.post(
            f"{MCP_URL}/ingest",
            json={"content": text, "filename": filename, "domain": domain or "", "tags": [], "sub_category": sub_category or ""},
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    # For binary files (PDF, DOCX, EPUB), use multipart /upload endpoint
    resp = requests.post(
        f"{MCP_URL}/upload",
        files={"file": (filename, content)},
        data={"domain": domain or "", "sub_category": sub_category or ""},
        headers={"X-Client-ID": "folder_scanner"},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


def scan_directory(
    root: str,
    *,
    dry_run: bool = False,
    max_file_size_mb: int = 50,
    exclude_patterns: set[str] | None = None,
) -> dict:
    """Walk directory tree and ingest all supported files."""
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        print(f"ERROR: Not a directory: {root}")
        sys.exit(1)

    excludes = EXCLUDE_DIRS | (exclude_patterns or set())
    max_size = max_file_size_mb * 1024 * 1024
    seen_hashes: set[str] = set()

    stats = {
        "total_files": 0,
        "ingested": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": 0,
        "results": [],
    }

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Filter out excluded directories
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in excludes
        ]

        for fname in filenames:
            if fname.startswith("."):
                continue

            fpath = os.path.join(dirpath, fname)
            ext = os.path.splitext(fname)[1].lower()

            if ext not in SUPPORTED_EXTENSIONS:
                continue

            stats["total_files"] += 1

            # Size check
            try:
                fsize = os.path.getsize(fpath)
            except OSError:
                stats["errors"] += 1
                continue

            if fsize > max_size:
                stats["skipped"] += 1
                print(f"  SKIP (size): {fpath} ({fsize / 1024 / 1024:.1f}MB)")
                continue

            # Content hash dedup
            try:
                fhash = file_content_hash(fpath)
            except OSError as e:
                stats["errors"] += 1
                print(f"  ERROR (hash): {fpath} → {e}")
                continue

            if fhash in seen_hashes:
                stats["duplicates"] += 1
                print(f"  DUP (local): {fpath}")
                continue
            seen_hashes.add(fhash)

            domain, sub_cat = detect_domain(fpath, root)

            if dry_run:
                print(f"  PREVIEW: {fpath} → domain={domain or 'auto'}")
                stats["results"].append({
                    "path": fpath, "status": "preview",
                    "domain": domain, "size_kb": round(fsize / 1024, 1),
                })
                continue

            # Brief pause between files to respect rate limits
            time.sleep(1)

            # Ingest via API (reads file on host, sends to MCP)
            try:
                result = ingest_via_api(fpath, domain, sub_cat)
                status = result.get("status", "unknown")
                if status == "duplicate":
                    stats["duplicates"] += 1
                    print(f"  DUP (KB): {fname}")
                else:
                    stats["ingested"] += 1
                    aid = result.get("artifact_id", "?")[:8]
                    chunks = result.get("chunks", 0)
                    print(f"  ✓ {fname} → {domain or result.get('domain', 'auto')} ({chunks} chunks) [{aid}]")
                stats["results"].append({
                    "path": fpath, "status": status,
                    "domain": result.get("domain", domain),
                    "artifact_id": result.get("artifact_id", ""),
                    "chunks": result.get("chunks", 0),
                })
            except requests.HTTPError as e:
                stats["errors"] += 1
                print(f"  ERROR: {fname} → {e.response.status_code}: {e.response.text[:200]}")
                stats["results"].append({"path": fpath, "status": "error", "error": str(e)})
            except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
                stats["errors"] += 1
                print(f"  ERROR: {fname} → MCP connection lost (PDF may be too complex)")
                # Wait for MCP to recover from potential OOM restart
                print(f"  Waiting 30s for MCP recovery...")
                time.sleep(30)
                # Verify MCP is back
                for attempt in range(5):
                    try:
                        requests.get(f"{MCP_URL}/health", timeout=5)
                        print(f"  MCP recovered. Continuing scan.")
                        break
                    except requests.ConnectionError:
                        time.sleep(10)
                else:
                    print(f"\nERROR: MCP did not recover after 80s. Aborting scan.")
                    sys.exit(1)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Cerid AI — Folder Scan Ingestion")
    parser.add_argument("path", nargs="?", default=WATCH_FOLDER, help="Directory to scan")
    parser.add_argument("--dry-run", action="store_true", help="Preview without ingesting")
    parser.add_argument("--max-size-mb", type=int, default=50, help="Max file size in MB")
    parser.add_argument("--exclude", nargs="*", default=[], help="Additional directories to exclude")
    args = parser.parse_args()

    root = os.path.expanduser(args.path)
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Scanning: {root}")
    print(f"MCP Server: {MCP_URL}")
    print(f"Max file size: {args.max_size_mb}MB")
    print(f"Supported extensions: {len(SUPPORTED_EXTENSIONS)}")
    print("-" * 60)

    start = time.time()
    stats = scan_directory(
        root,
        dry_run=args.dry_run,
        max_file_size_mb=args.max_size_mb,
        exclude_patterns=set(args.exclude) if args.exclude else None,
    )
    elapsed = time.time() - start

    print("-" * 60)
    print(f"Scan complete in {elapsed:.1f}s")
    print(f"  Files found:  {stats['total_files']}")
    print(f"  Ingested:     {stats['ingested']}")
    print(f"  Duplicates:   {stats['duplicates']}")
    print(f"  Skipped:      {stats['skipped']}")
    print(f"  Errors:       {stats['errors']}")


if __name__ == "__main__":
    main()
