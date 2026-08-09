#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Idempotent purge of eval-seed contamination from the live KB.

Targets two families of artifacts that should never exist in a production
knowledge base:

1. **BEIR seed data** — ``client_source STARTS WITH 'seed-beir'``
   (~8,774 artifacts: 5,183 SciFact + 3,591 NFCorpus) ingested by
   ``scripts/seed_beir_corpus.py``.

2. **Beta-smoke fixtures** — artifacts whose ``filename STARTS WITH
   'beta-smoke://'`` ingested via the ``text_input`` ingest path during
   smoke/integration testing.

Neither family has a ``pack_id`` property; the script asserts this before
deletion and skips any artifact that carries one.

Usage from the host:
    docker cp scripts/purge_eval_seed.py ai-companion-mcp:/tmp/
    docker exec -e PYTHONPATH=/app ai-companion-mcp \\
        python /tmp/purge_eval_seed.py            # dry-run (safe default)
    docker exec -e PYTHONPATH=/app ai-companion-mcp \\
        python /tmp/purge_eval_seed.py --execute  # actually delete

``--dry-run`` is the default.  A second ``--execute`` run finds 0 artifacts.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("purge-eval-seed")

# How many artifacts to delete per Neo4j/Chroma round-trip.
_BATCH_SIZE = 100


def _is_eval_seed(artifact: dict) -> bool:
    """Return True when the artifact matches either eval-seed family.

    Selection criteria (OR):
    - ``client_source`` starts with ``'seed-beir'``
    - ``filename``      starts with ``'beta-smoke://'``

    Safety gate: any artifact carrying a ``pack_id`` is excluded so
    knowledge-pack data is never touched regardless of naming overlap.
    """
    if artifact.get("pack_id"):
        return False
    client_source = artifact.get("client_source") or ""
    filename = artifact.get("filename") or ""
    return client_source.startswith("seed-beir") or filename.startswith("beta-smoke://")


def _query_candidates(neo4j_driver) -> list[dict]:
    """Fetch all eval-seed candidate artifacts from Neo4j.

    Pulls only the fields needed for deletion + reporting; does NOT
    load chunk text.
    """
    with neo4j_driver.session() as s:
        rows = s.run(
            "MATCH (a:Artifact) "
            "WHERE (a.client_source STARTS WITH 'seed-beir' "
            "       OR a.filename STARTS WITH 'beta-smoke://') "
            "  AND (a.pack_id IS NULL OR a.pack_id = '') "
            "RETURN a.id          AS id, "
            "       a.filename     AS filename, "
            "       a.domain       AS domain, "
            "       a.client_source AS client_source, "
            "       a.chunk_ids    AS chunk_ids, "
            "       a.pack_id      AS pack_id",
        ).data()
    # Python-side safety: re-apply the predicate so the function is
    # correct even if the Cypher filter changes.
    return [r for r in rows if _is_eval_seed(r)]


def _dry_run(candidates: list[dict]) -> None:
    """Print per-client_source and per-domain counts; delete nothing."""
    if not candidates:
        log.info("DRY-RUN: no eval-seed artifacts found — nothing to purge.")
        return

    by_source: dict[str, int] = defaultdict(int)
    by_domain: dict[str, int] = defaultdict(int)
    for a in candidates:
        by_source[a.get("client_source") or "(beta-smoke)"] += 1
        by_domain[a.get("domain") or "unknown"] += 1

    log.info("DRY-RUN: %d artifact(s) would be deleted.", len(candidates))
    log.info("  By client_source:")
    for src, n in sorted(by_source.items()):
        log.info("    %-35s  %d", src, n)
    log.info("  By domain:")
    for dom, n in sorted(by_domain.items()):
        log.info("    %-20s  %d", dom, n)
    log.info("Re-run with --execute to perform deletion.")


def _execute(candidates: list[dict], neo4j_driver, chroma_client) -> dict:
    """Delete all candidates in batches; return summary counts."""
    import config

    total = len(candidates)
    deleted = 0
    missing = 0
    chunks_removed = 0

    log.info("EXECUTE: deleting %d artifact(s) in batches of %d …", total, _BATCH_SIZE)

    for batch_start in range(0, total, _BATCH_SIZE):
        batch = candidates[batch_start : batch_start + _BATCH_SIZE]
        log.info(
            "  batch %d–%d / %d",
            batch_start + 1,
            min(batch_start + _BATCH_SIZE, total),
            total,
        )

        # Group chunk_ids by domain for Chroma deletion.
        chunks_by_domain: dict[str, list[str]] = defaultdict(list)

        for artifact in batch:
            aid = artifact["id"]
            domain = artifact.get("domain") or ""

            # Pull chunk_ids before Neo4j deletion (delete_artifact also
            # returns them, but we pre-collect here so Chroma cleanup
            # works even if the Neo4j row was already absent).
            raw_chunk_ids = artifact.get("chunk_ids") or "[]"
            try:
                chunk_ids: list[str] = (
                    json.loads(raw_chunk_ids)
                    if isinstance(raw_chunk_ids, str)
                    else list(raw_chunk_ids)
                )
            except (json.JSONDecodeError, TypeError):
                chunk_ids = []

            # Neo4j deletion — synchronous (wipe_eval_corpus.py pattern).
            from app.db.neo4j.artifacts import delete_artifact

            result = delete_artifact(neo4j_driver, aid)
            if result.get("deleted"):
                deleted += 1
                # delete_artifact may return chunk_ids from the pre-delete
                # fetch; merge in case our pre-collected list was stale.
                extra = result.get("chunk_ids") or []
                all_ids = list({*chunk_ids, *extra})
            else:
                missing += 1
                all_ids = chunk_ids  # already missing from neo4j; still clean chroma

            if all_ids and domain:
                chunks_by_domain[domain].extend(all_ids)

        # Chroma cleanup for this batch.
        for domain, cids in chunks_by_domain.items():
            if not cids:
                continue
            coll_name = config.collection_name(domain)
            try:
                coll = chroma_client.get_collection(name=coll_name)
                coll.delete(ids=cids)
                chunks_removed += len(cids)
                log.info("  chroma: removed %d chunk(s) from %s", len(cids), coll_name)
            except Exception as exc:  # noqa: BLE001 — collection may be absent
                log.warning("  chroma delete failed for %s: %s", coll_name, exc)

    summary = {
        "deleted": deleted,
        "missing": missing,
        "chunks_removed": chunks_removed,
    }
    log.info(
        "Done. deleted=%d  missing=%d  chunks_removed=%d",
        deleted,
        missing,
        chunks_removed,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge BEIR eval-seed and beta-smoke artifacts from the live KB.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Print what would be deleted without deleting (default).",
    )
    mode.add_argument(
        "--execute",
        dest="dry_run",
        action="store_false",
        help="Perform deletion in batches.",
    )
    args = parser.parse_args(argv)

    try:
        from app.deps import get_chroma, get_neo4j
    except ImportError as exc:
        log.error("import failure (PYTHONPATH=/app required): %s", exc)
        return 2

    neo4j_driver = get_neo4j()
    chroma_client = get_chroma()

    log.info("Querying Neo4j for eval-seed candidates …")
    candidates = _query_candidates(neo4j_driver)
    log.info("Found %d candidate artifact(s).", len(candidates))

    if args.dry_run:
        _dry_run(candidates)
        return 0

    if not candidates:
        log.info("Nothing to delete — already clean.")
        return 0

    _execute(candidates, neo4j_driver, chroma_client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
