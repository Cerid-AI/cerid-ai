#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Backfill the (:Entity) layer over the existing artifact corpus.

Workstream E Phase 4a.4. For each ``(:Artifact)`` in Neo4j: fetch all
its chunks from chromadb, concatenate the text (truncated at
``max_chars``), run :func:`core.agents.entity_extraction.extract_entities_from_text`,
and upsert the result via :func:`app.db.neo4j.entity.upsert_entities_for_artifact`.

Resumable + idempotent: a checkpoint file at ``.cerid-state/entity_backfill.json``
records the artifact IDs already processed; rerunning skips them. Safe
to interrupt with Ctrl-C — the next run continues from the last
checkpoint.

Usage:
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --limit 100         # pilot run (default: process all)
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --reset             # drop checkpoint and start fresh
    docker exec ai-companion-mcp python -m scripts.backfill_entities \\
        --domain general    # restrict to one domain

Output: progress every 10 artifacts; a final summary with total
entities upserted, MENTIONS edges added, and elapsed wall-clock time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

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


def _list_artifacts(driver, domain: str | None = None) -> list[tuple[str, str]]:
    """Return [(artifact_id, domain), ...] in stable order."""
    cypher = "MATCH (a:Artifact)"
    params: dict = {}
    if domain:
        cypher += " WHERE a.domain = $domain"
        params["domain"] = domain
    cypher += " RETURN a.id AS id, a.domain AS domain ORDER BY a.id"
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
    artifacts = _list_artifacts(driver, domain=args.domain)
    pending = [(aid, dom) for (aid, dom) in artifacts if aid not in processed]

    total = len(pending)
    if args.limit:
        pending = pending[: args.limit]

    logger.info(
        "Backfill: %d artifacts pending (showing %d this run; %d already in checkpoint)",
        total, len(pending), len(processed),
    )

    started = time.time()
    totals = {"entities_upserted": 0, "edges_upserted": 0, "no_entities": 0,
              "no_chunks": 0, "empty_text": 0, "errors": 0}

    for i, (artifact_id, domain) in enumerate(pending, start=1):
        try:
            stats = await _process_one(driver, chroma_client, artifact_id, domain, args.max_chars)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill entity layer")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N artifacts (default: all)")
    parser.add_argument("--domain", type=str, default=None, help="Restrict to one domain")
    parser.add_argument("--max-chars", type=int, default=8000, help="Max chars per artifact extraction")
    parser.add_argument("--reset", action="store_true", help="Clear the checkpoint and start fresh")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
