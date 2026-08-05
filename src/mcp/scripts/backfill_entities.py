#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Backfill the (:Entity) layer over the existing artifact corpus.

Workstream E Phase 4a.4 (origin) + v0.92 Phase P refactor.

Default path enqueues an ``EntityExtractionJob`` per artifact onto the
Redis-backed processor queue. The script becomes a *driver* — it lists
candidates, dedupes against the checkpoint, and submits work — while
the existing background worker drains the queue with CPU-aware
throttling, retry logic, cost tracking, and progress visible in the
Processor pane. Use ``--in-process`` to bypass the queue for ad-hoc
diagnostic runs (no Redis required).

Resumable + idempotent: a checkpoint file at ``.cerid-state/entity_backfill.json``
records the artifact IDs already enqueued; rerunning skips them. Safe
to interrupt with Ctrl-C — the next run continues from the last
checkpoint.

Usage:
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --limit 100              # pilot run (default: process all)
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --in-process             # bypass the queue (legacy path)
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --reset                  # drop checkpoint and start fresh
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --domain general         # restrict to one domain

Output: progress every 10 artifacts; a final summary with total
artifacts enqueued (or, in legacy mode, entities upserted + edges added)
and elapsed wall-clock time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("entity-backfill")

CHECKPOINT_PATH = Path(".cerid-state/entity_backfill.json")


def _load_checkpoint() -> set[str]:
    if not CHECKPOINT_PATH.exists():
        return set()
    try:
        return set(json.loads(CHECKPOINT_PATH.read_text()).get("processed", []))
    except Exception:  # noqa: BLE001 — best-effort read; corrupted checkpoint is recoverable by restart
        logger.warning("Checkpoint at %s is unreadable — starting fresh", CHECKPOINT_PATH)
        return set()


def _save_checkpoint(processed: set[str]) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps({"processed": sorted(processed)}, indent=2))


def _list_artifacts(
    driver,
    domain: str | None = None,
    *,
    domains: list[str] | None = None,
    exclude_filename_patterns: list[str] | None = None,
    max_age_days: int | None = None,
    sort: str = "id",
    limit: int | None = None,
    ids_from_file: str | None = None,
) -> list[tuple[str, str]]:
    """Return [(artifact_id, domain), ...] in stable order.

    Filtering knobs (compose freely) for the tiered backfill workflow:

      - ``domain`` (single) or ``domains`` (list) — restrict to one or more
        ``a.domain`` values.
      - ``exclude_filename_patterns`` — drop artifacts whose filename
        matches any of these regex patterns (Cypher ``=~``). Useful for
        skipping the bulk PubMed/MED literature in Tier 1.
      - ``max_age_days`` — keep only artifacts ingested within N days.
        Used by Tier 2 (recent memory_*).
      - ``sort`` — ``"id"`` (default), ``"quality_desc"``, or
        ``"ingested_desc"``. Tier 3 uses ``quality_desc`` to skim the
        top-quality bulk-literature subsample.
      - ``limit`` — cap the result count (combine with ``sort`` for
        "top-N by X").
      - ``ids_from_file`` — read artifact IDs (one per line) from a file;
        bypasses other filters. Lets the caller pre-compute a set via
        ad-hoc Cypher and feed it back. Conflicts with the rest.
    """
    if ids_from_file:
        with open(ids_from_file) as f:
            wanted = {line.strip() for line in f if line.strip()}
        cypher = (
            "MATCH (a:Artifact) WHERE a.id IN $wanted "
            "RETURN a.id AS id, a.domain AS domain ORDER BY a.id"
        )
        with driver.session() as session:
            return [(row["id"], row["domain"]) for row in session.run(cypher, wanted=list(wanted))]

    where_clauses: list[str] = []
    params: dict = {}
    if domain:
        where_clauses.append("a.domain = $domain")
        params["domain"] = domain
    if domains:
        where_clauses.append("a.domain IN $domains")
        params["domains"] = domains
    if exclude_filename_patterns:
        for i, pat in enumerate(exclude_filename_patterns):
            key = f"excl_pat_{i}"
            where_clauses.append(f"NOT a.filename =~ ${key}")
            params[key] = pat
    if max_age_days is not None:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        where_clauses.append("a.ingested_at >= $ingested_cutoff")
        params["ingested_cutoff"] = cutoff

    cypher = "MATCH (a:Artifact)"
    if where_clauses:
        cypher += " WHERE " + " AND ".join(where_clauses)
    if sort == "quality_desc":
        cypher += " RETURN a.id AS id, a.domain AS domain ORDER BY coalesce(a.quality_score, 0.0) DESC, a.id"
    elif sort == "ingested_desc":
        cypher += " RETURN a.id AS id, a.domain AS domain ORDER BY a.ingested_at DESC, a.id"
    else:
        cypher += " RETURN a.id AS id, a.domain AS domain ORDER BY a.id"
    if limit is not None:
        cypher += " LIMIT $limit_n"
        params["limit_n"] = int(limit)

    with driver.session() as session:
        return [(row["id"], row["domain"]) for row in session.run(cypher, **params)]


def _fetch_chunks_for_artifact(chroma_client, domain: str, artifact_id: str) -> tuple[list[str], list[str]]:
    """Return (chunk_ids, chunk_texts) for the artifact across its domain collection.

    chromadb 1.x metadata filter shape: ``where={"artifact_id": {"$eq": "..."}}``.
    """
    from config.taxonomy import collection_name

    try:
        coll = chroma_client.get_collection(name=collection_name(domain))
    except Exception:  # noqa: BLE001 — collection-missing is a valid skip path
        return [], []
    res = coll.get(
        where={"artifact_id": {"$eq": artifact_id}},
        include=["documents"],
    )
    return list(res.get("ids", [])), list(res.get("documents", []) or [])


async def _process_one(
    driver,
    chroma_client,
    artifact_id: str,
    domain: str,
    max_chars: int,
) -> dict:
    from app.db.neo4j.entity import upsert_entities_for_artifact
    from core.agents.entity_extraction import default_llm_caller, extract_entities_from_text

    chunk_ids, docs = _fetch_chunks_for_artifact(chroma_client, domain, artifact_id)
    if not chunk_ids:
        return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_chunks"}

    blob = "\n\n---\n\n".join(d for d in docs if d)
    if not blob.strip():
        return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "empty_text"}

    entities = await extract_entities_from_text(
        blob, llm_caller=default_llm_caller, max_chars=max_chars,
    )
    if not entities:
        return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_entities"}

    return upsert_entities_for_artifact(driver, artifact_id, entities, chunk_ids)


async def _run(args: argparse.Namespace) -> int:
    from app.deps import get_chroma, get_neo4j

    driver = get_neo4j()
    chroma_client = get_chroma()

    if args.reset and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        logger.info("Checkpoint cleared.")

    processed = _load_checkpoint()
    artifacts = _list_artifacts(
        driver,
        domain=args.domain,
        domains=args.domains,
        exclude_filename_patterns=args.exclude_filename_pattern or None,
        max_age_days=args.max_age_days,
        sort=args.sort,
        limit=args.list_limit,
        ids_from_file=args.ids_from_file,
    )
    pending = [(aid, dom) for (aid, dom) in artifacts if aid not in processed]

    total = len(pending)
    if args.limit:
        pending = pending[: args.limit]

    mode = "in-process" if args.in_process else "processor-queue"
    logger.info(
        "Backfill (%s): %d artifacts pending (showing %d this run; %d already in checkpoint)",
        mode, total, len(pending), len(processed),
    )

    started = time.time()
    if args.in_process:
        return await _run_in_process(
            driver, chroma_client, pending, args.max_chars, processed, started,
        )
    return _run_via_processor(pending, args.tenant_id, processed, started)


async def _run_in_process(
    driver: Any,
    chroma_client: Any,
    pending: list[tuple[str, str]],
    max_chars: int,
    processed: set[str],
    started: float,
) -> int:
    """Legacy direct-call path. Useful for environments without Redis."""
    totals: dict[str, int] = {
        "entities_upserted": 0, "edges_upserted": 0, "no_entities": 0,
        "no_chunks": 0, "empty_text": 0, "errors": 0,
    }
    for i, (artifact_id, domain) in enumerate(pending, start=1):
        try:
            stats = await _process_one(driver, chroma_client, artifact_id, domain, max_chars)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            logger.exception("Artifact %s failed: %s", artifact_id, exc)
            totals["errors"] += 1
            continue

        totals["entities_upserted"] += stats.get("entities_upserted", 0)
        totals["edges_upserted"] += stats.get("edges_upserted", 0)
        skipped = stats.get("skipped")
        if skipped:
            totals[skipped] = totals.get(skipped, 0) + 1

        processed.add(artifact_id)
        if i % 10 == 0 or i == len(pending):
            _save_checkpoint(processed)
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            logger.info(
                "[%d/%d] entities=%d edges=%d (%.1f art/s)",
                i, len(pending),
                totals["entities_upserted"], totals["edges_upserted"], rate,
            )

    _save_checkpoint(processed)
    logger.info("Done in %.1fs. Totals: %s", time.time() - started, totals)
    return 0


def _run_via_processor(
    pending: list[tuple[str, str]],
    tenant_id: str,
    processed: set[str],
    started: float,
) -> int:
    """Enqueue an ``EntityExtractionJob`` per artifact onto the processor queue.

    The script returns once all jobs are enqueued — the worker drains
    them asynchronously. Progress and per-job results land in the
    Processor pane and the ``processor_*`` ``/health.invariants`` fields.
    """
    from app.db.redis.processor_queue import enqueue_job
    from app.processor.jobs.entity_extraction import EntityExtractionJob

    enqueued = 0
    errors = 0
    for i, (artifact_id, _domain) in enumerate(pending, start=1):
        try:
            payload = {"artifact_id": artifact_id, "tenant_id": tenant_id}
            enqueue_job(EntityExtractionJob(**payload), payload=payload)
            enqueued += 1
            processed.add(artifact_id)
        except Exception as exc:  # noqa: BLE001 — enqueue-failure boundary
            logger.exception("Enqueue failed for artifact %s: %s", artifact_id, exc)
            errors += 1
            continue

        if i % 50 == 0 or i == len(pending):
            _save_checkpoint(processed)
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            logger.info("[%d/%d] enqueued=%d errors=%d (%.0f art/s)",
                        i, len(pending), enqueued, errors, rate)

    _save_checkpoint(processed)
    logger.info(
        "Done in %.1fs. Enqueued %d job(s); %d enqueue error(s). "
        "Watch the Processor pane or /processor/status for run progress.",
        time.time() - started, enqueued, errors,
    )
    return 0 if errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill entity layer")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process at most N artifacts in this run (post-list-filter cap)")
    parser.add_argument("--domain", type=str, default=None, help="Restrict to one domain")
    parser.add_argument("--domains", type=str, default=None,
                        help="Comma-separated list of domains; OR'd in the WHERE clause")
    parser.add_argument("--exclude-filename-pattern", action="append", default=[],
                        help="Cypher regex (=~) to drop matching filenames; repeatable")
    parser.add_argument("--max-age-days", type=int, default=None,
                        help="Only artifacts ingested within the last N days")
    parser.add_argument("--sort", choices=("id", "quality_desc", "ingested_desc"),
                        default="id", help="Ordering for the candidate list")
    parser.add_argument("--list-limit", type=int, default=None,
                        help="Cap the candidate list pre-checkpoint-skip (use with --sort)")
    parser.add_argument("--ids-from-file", type=str, default=None,
                        help="One artifact_id per line; bypasses the filter knobs above")
    parser.add_argument("--max-chars", type=int, default=8000, help="Max chars per artifact extraction (in-process path only)")
    parser.add_argument("--reset", action="store_true", help="Clear the checkpoint and start fresh")
    parser.add_argument(
        "--in-process",
        action="store_true",
        help=(
            "Bypass the processor queue and run extraction inline. "
            "Useful when Redis is not available or for ad-hoc diagnostics; "
            "the default path enqueues an EntityExtractionJob per artifact."
        ),
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default="default",
        help="Tenant identifier passed to EntityExtractionJob for log correlation (default: 'default').",
    )
    args = parser.parse_args()
    if args.domains:
        args.domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    else:
        args.domains = None
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
