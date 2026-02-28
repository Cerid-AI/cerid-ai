# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared constants and utility functions for the sync package."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import config

logger = logging.getLogger("ai-companion.sync")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = "manifest.json"
ARTIFACTS_JSONL = "artifacts.jsonl"
DOMAINS_JSONL = "domains.jsonl"
RELATIONSHIPS_JSONL = "relationships.jsonl"
AUDIT_LOG_JSONL = "audit_log.jsonl"

NEO4J_SUBDIR = "neo4j"
CHROMA_SUBDIR = "chroma"
BM25_SUBDIR = "bm25"
REDIS_SUBDIR = "redis"

CHROMA_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _default_sync_dir() -> str:
    """Return SYNC_DIR from config if present, else ~/Dropbox/cerid-sync."""
    return getattr(config, "SYNC_DIR", os.path.expanduser("~/Dropbox/cerid-sync"))


def _ensure_dir(path: str) -> Path:
    """Create directory (and parents) if it does not exist. Returns Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha256_file(filepath: str) -> str:
    """Return hex SHA-256 of a file's contents."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        logger.warning("Cannot checksum %s: %s", filepath, exc)
        return ""


def _count_jsonl_lines(filepath: str) -> int:
    """Return number of non-empty lines in a JSONL file."""
    try:
        count = 0
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count
    except OSError:
        return 0


def _write_jsonl(filepath: str, rows: List[Dict[str, Any]]) -> int:
    """Write a list of dicts to a JSONL file. Returns number of rows written."""
    written = 0
    with open(filepath, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
            written += 1
    return written


def _iter_jsonl(filepath: str):
    """Yield parsed dicts from a JSONL file, skipping blank/invalid lines."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed JSONL line %d in %s: %s", lineno, filepath, exc)
    except OSError as exc:
        logger.warning("Cannot read %s: %s", filepath, exc)
