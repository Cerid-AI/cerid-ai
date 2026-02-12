#!/usr/bin/env python3
"""
Cerid AI - CLI Batch Ingestion Script

Ingest files from a directory into the MCP knowledge base via the /ingest_file API.

Runs on the HOST (not inside Docker). Translates host paths to container paths.

Usage:
    python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/
    python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/finance/ --mode pro
    python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ --domain coding
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import requests

# Add parent dir so we can import config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

MCP_URL = os.getenv("MCP_URL", "http://localhost:8888")


def _translate_path(host_path: str) -> str:
    """Translate host path to container path (/archive/...)."""
    watch = config.WATCH_FOLDER.rstrip("/")
    archive = config.ARCHIVE_PATH.rstrip("/")
    if host_path.startswith(watch):
        return host_path.replace(watch, archive, 1)
    return host_path


def _detect_domain(host_path: str, base_dir: str) -> str:
    """Detect domain from parent folder name relative to base_dir."""
    rel = os.path.relpath(host_path, base_dir)
    parts = rel.split(os.sep)
    if len(parts) >= 2:
        folder = parts[0].lower()
        if folder in config.DOMAINS:
            return folder
    return ""


def _should_skip(file_path: str) -> bool:
    """Check if file should be skipped."""
    path = Path(file_path)

    # Skip hidden files
    if any(p.startswith(".") for p in path.parts):
        return True

    # Skip legacy directories
    for part in path.parts:
        if part.startswith("legacy-"):
            return True

    # Skip unsupported extensions
    if path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
        return True

    return False


def main():
    parser = argparse.ArgumentParser(description="Batch ingest files into Cerid AI")
    parser.add_argument("--dir", required=True, help="Directory to ingest (recursively)")
    parser.add_argument(
        "--mode",
        choices=["manual", "smart", "pro"],
        default=config.CATEGORIZE_MODE,
        help=f"Categorization mode (default: {config.CATEGORIZE_MODE})",
    )
    parser.add_argument("--domain", default="", help="Force domain for all files")
    parser.add_argument("--dry-run", action="store_true", help="List files without ingesting")
    args = parser.parse_args()

    base_dir = os.path.expanduser(args.dir)
    if not os.path.isdir(base_dir):
        print(f"ERROR: Directory does not exist: {base_dir}")
        sys.exit(1)

    # Collect files
    files = []
    for root, dirs, filenames in os.walk(base_dir):
        for fname in sorted(filenames):
            fpath = os.path.join(root, fname)
            if not _should_skip(fpath):
                files.append(fpath)

    if not files:
        print(f"No supported files found in {base_dir}")
        sys.exit(0)

    print(f"Found {len(files)} files in {base_dir}")
    print(f"Mode: {args.mode}, Domain override: {args.domain or '(auto-detect)'}")
    print()

    if args.dry_run:
        for f in files:
            domain = args.domain or _detect_domain(f, base_dir)
            print(f"  {Path(f).name:40s} → {domain or '(AI)'}")
        print(f"\nTotal: {len(files)} files (dry run)")
        return

    # Ingest
    succeeded = 0
    failed = 0
    domain_counts: Counter = Counter()

    for i, fpath in enumerate(files, 1):
        filename = Path(fpath).name
        domain = args.domain or _detect_domain(fpath, base_dir)
        container_path = _translate_path(fpath)
        mode = "manual" if domain else args.mode

        print(f"[{i}/{len(files)}] {filename}...", end=" ", flush=True)

        try:
            resp = requests.post(
                f"{MCP_URL}/ingest_file",
                json={
                    "file_path": container_path,
                    "domain": domain,
                    "categorize_mode": mode,
                },
                timeout=120,
            )
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "success")
                if status == "duplicate":
                    dup_of = data.get("duplicate_of", "?")
                    print(f"SKIPPED (duplicate of '{dup_of}')")
                    succeeded += 1  # not a failure
                else:
                    assigned = data.get("domain", "?")
                    chunks = data.get("chunks", 0)
                    domain_counts[assigned] += 1
                    succeeded += 1
                    print(f"→ {assigned} ({chunks} chunks)")
            else:
                failed += 1
                # Include server error message for diagnosis
                try:
                    detail = resp.json().get("detail", resp.text[:200])
                except Exception:
                    detail = resp.text[:200]
                print(f"FAILED (HTTP {resp.status_code}: {detail})")
        except requests.RequestException as e:
            failed += 1
            print(f"ERROR: {e}")

    # Summary
    print(f"\n{'='*50}")
    print(f"Ingested: {succeeded}/{len(files)} ({failed} failed)")
    if domain_counts:
        print("Domains:")
        for domain, count in domain_counts.most_common():
            print(f"  {domain}: {count}")


if __name__ == "__main__":
    main()
