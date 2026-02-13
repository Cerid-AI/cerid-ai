#!/usr/bin/env python3
"""
Cerid AI - CLI Batch Ingestion Script

Ingest files from a directory into the MCP knowledge base via the /ingest_file API.

Runs on the HOST (not inside Docker). Translates host paths to container paths.

Usage:
    python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/
    python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/finance/ --mode pro
    python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ --domain coding --workers 4
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests

# Add parent dir so we can import config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

MCP_URL = os.getenv("MCP_URL", "http://localhost:8888")
REQUEST_DELAY = 0.3  # seconds between requests to avoid server overload

# Thread-safe counters
_print_lock = Lock()


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


def _ingest_one(
    fpath: str,
    index: int,
    total: int,
    domain: str,
    mode: str,
    base_dir: str,
) -> dict:
    """Ingest a single file. Returns result dict for aggregation."""
    filename = Path(fpath).name
    file_domain = domain or _detect_domain(fpath, base_dir)
    container_path = _translate_path(fpath)
    file_mode = "manual" if file_domain else mode

    result = {
        "file": filename,
        "path": fpath,
        "index": index,
        "status": "unknown",
        "domain": file_domain,
        "chunks": 0,
        "error": None,
        "error_type": None,
    }

    try:
        time.sleep(REQUEST_DELAY)  # avoid hammering the server
        resp = requests.post(
            f"{MCP_URL}/ingest_file",
            json={
                "file_path": container_path,
                "domain": file_domain,
                "categorize_mode": file_mode,
            },
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "success")
            if status == "duplicate":
                result["status"] = "duplicate"
                result["domain"] = data.get("domain", file_domain)
                dup_of = data.get("duplicate_of", "?")
                with _print_lock:
                    print(f"  [{index}/{total}] {filename:40s} SKIP (duplicate of '{dup_of}')")
            else:
                result["status"] = "success"
                result["domain"] = data.get("domain", file_domain)
                result["chunks"] = data.get("chunks", 0)
                with _print_lock:
                    print(
                        f"  [{index}/{total}] {filename:40s} → {result['domain']} "
                        f"({result['chunks']} chunks)"
                    )
        else:
            result["status"] = "failed"
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            result["error"] = f"HTTP {resp.status_code}: {detail}"
            result["error_type"] = f"HTTP {resp.status_code}"
            with _print_lock:
                print(f"  [{index}/{total}] {filename:40s} FAILED ({result['error']})")
    except requests.Timeout:
        result["status"] = "failed"
        result["error"] = "Request timed out (120s)"
        result["error_type"] = "timeout"
        with _print_lock:
            print(f"  [{index}/{total}] {filename:40s} FAILED (timeout)")
    except requests.RequestException as e:
        result["status"] = "failed"
        result["error"] = str(e)
        result["error_type"] = "network"
        with _print_lock:
            print(f"  [{index}/{total}] {filename:40s} ERROR ({e})")

    return result


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
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers (default: 4)")
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
    print(f"Workers: {args.workers}")
    print()

    if args.dry_run:
        for f in files:
            domain = args.domain or _detect_domain(f, base_dir)
            print(f"  {Path(f).name:40s} → {domain or '(AI)'}")
        print(f"\nTotal: {len(files)} files (dry run)")
        return

    # Ingest with thread pool
    start_time = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _ingest_one, fpath, i, len(files), args.domain, args.mode, base_dir
            ): fpath
            for i, fpath in enumerate(files, 1)
        }
        for future in as_completed(futures):
            results.append(future.result())

    elapsed = time.time() - start_time

    # Aggregate results
    succeeded = sum(1 for r in results if r["status"] == "success")
    duplicates = sum(1 for r in results if r["status"] == "duplicate")
    failed_results = [r for r in results if r["status"] == "failed"]
    domain_counts: Counter = Counter()
    for r in results:
        if r["status"] == "success" and r["domain"]:
            domain_counts[r["domain"]] += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Completed in {elapsed:.1f}s ({len(files)/max(elapsed,1)*60:.0f} files/hr)")
    print(f"  Ingested:   {succeeded}")
    print(f"  Duplicates: {duplicates}")
    print(f"  Failed:     {len(failed_results)}")

    if domain_counts:
        print("\nDomains:")
        for domain, count in domain_counts.most_common():
            print(f"  {domain}: {count}")

    if failed_results:
        print("\nFailures by type:")
        error_types: Counter = Counter()
        for r in failed_results:
            error_types[r.get("error_type", "unknown")] += 1
        for etype, count in error_types.most_common():
            print(f"  {etype}: {count}")
        print("\nFailed files:")
        for r in failed_results:
            print(f"  {r['file']}: {r['error']}")


if __name__ == "__main__":
    main()
