#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Seed the eval corpus into the live cerid stack (Workstream E Phase 1.2.b).

Runs INSIDE the MCP container so it can bypass the /ingest_file
endpoint's archive-path guard (validate_file_path restricts inputs
to ${ARCHIVE_PATH} which is /archive). Reads every *.md under the
mounted /eval-corpus/<version>/ tree, classifies by parent directory
(coding / finance / projects / personal / general), and calls
``services.ingestion.ingest_content`` directly with proper metadata.

Idempotent — cerid dedupes by content_hash; re-running on an
already-seeded corpus reports each file as ``status=duplicate``.

Usage from the host:
    docker exec -e PYTHONPATH=/app ai-companion-mcp \\
        python /eval-corpus/../seed_eval_corpus.py
    # or, after rebuilding with this script copied in:
    docker exec ai-companion-mcp python -m scripts.seed_eval_corpus

Env vars:
    CERID_CORPUS_VERSION   default "v1"
    CERID_CORPUS_ROOT      default /eval-corpus (the compose mount)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed-eval-corpus")


def main() -> int:
    version = os.getenv("CERID_CORPUS_VERSION", "v1")
    corpus_root = Path(os.getenv("CERID_CORPUS_ROOT", "/eval-corpus")) / version

    if not corpus_root.is_dir():
        log.error("corpus directory not found: %s", corpus_root)
        log.error(
            "this script runs INSIDE the MCP container; "
            "verify ./data/eval-corpus is mounted at /eval-corpus in docker-compose.yml",
        )
        return 1

    # Lazy import — keep failure message clean if PYTHONPATH isn't set
    try:
        import config
        from app.services.ingestion import ingest_content
    except ImportError as exc:
        log.error("import failure (PYTHONPATH=/app required): %s", exc)
        return 2

    # Workstream E Phase 2b — when ENABLE_LAYOUT_AWARE_PARSING is set, mirror
    # the production ingest_file wire-in by pre-calling layout_aware_parse
    # and passing pre_chunked= to ingest_content. ingest_content alone
    # doesn't honor the feature flag — the routing is in ingest_file —
    # so the seeder needs to do this dispatch itself for the measurement
    # to actually exercise layout-aware chunking.
    layout_aware = bool(getattr(config, "ENABLE_LAYOUT_AWARE_PARSING", False))
    layout_aware_parse = None
    if layout_aware:
        try:
            from core.ingest.dispatch import layout_aware_parse  # noqa: F401
        except ImportError as exc:
            log.warning("layout_aware_parse unavailable (%s); falling back", exc)
            layout_aware = False

    files = sorted(corpus_root.rglob("*.md"))
    files = [f for f in files if f.name != "MANIFEST.md"]
    log.info("Seeding %d eval-corpus docs from %s", len(files), corpus_root)

    ok = duplicate = failed = 0

    for path in files:
        rel = path.relative_to(corpus_root)
        domain = rel.parts[0] if len(rel.parts) > 1 else "general"
        filename = path.name

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — surface file-read errors
            log.error("read_failed %s: %s", rel, exc)
            failed += 1
            continue

        metadata = {
            "filename": filename,
            "domain": domain,
            "file_type": "md",
            "client_source": "seed-eval-corpus",
            "summary": f"Eval corpus {version}: {rel}",
            "tags_json": json.dumps(["eval-corpus", version, domain]),
            "keywords_json": json.dumps([]),
            "sub_category": "eval-corpus",
        }

        try:
            pre_chunked = None
            if layout_aware and layout_aware_parse is not None:
                layout_result = layout_aware_parse(str(path))
                if layout_result is not None:
                    _, pre_chunked = layout_result
            result = ingest_content(
                content, domain, metadata,
                skip_quality=True, pre_chunked=pre_chunked,
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log.error("ingest_failed %s: %s", rel, exc)
            failed += 1
            continue

        status = result.get("status")
        if status == "success":
            ok += 1
            log.info("OK   %s (domain=%s)", rel, domain)
        elif status == "duplicate":
            duplicate += 1
            log.info("DUP  %s (already ingested)", rel)
        else:
            failed += 1
            log.error("FAIL %s — %s", rel, result)

    log.info(
        "Done. total=%d ok=%d duplicate=%d failed=%d",
        len(files), ok, duplicate, failed,
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
