#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Backfill ``(:Memory)-[:MENTIONS]->(:Entity)`` edges for existing memories.

Why this exists
---------------
Memories reach the graph by two paths. Conversational memories are stored as
``:Artifact`` nodes and have enqueued entity extraction since Phase K2.1.
Verified-claim promotion (``core.agents.verified_memory.promote_verified_facts``)
instead creates ``:Memory`` nodes, and never enqueued anything — so every
``:Memory`` node created before that gap was closed carries no entity edges and
cannot participate in entity-anchored retrieval.

New memories are handled by ``MemoryEntityExtractionJob``, wired at promotion
time. This script is the one-off catch-up for the pre-existing backlog.

Usage
-----
    # Triage only — counts and a sample, writes nothing:
    python3 scripts/backfill_memory_entities.py

    # Extract + write, oldest first:
    python3 scripts/backfill_memory_entities.py --apply

    # Bound a first run:
    python3 scripts/backfill_memory_entities.py --apply --limit 25

Each memory costs one local-LLM extraction call, so ``--limit`` is the usual
way to sanity-check output before committing to the full backlog. Re-running is
safe: the graph write is a MERGE, and already-linked memories are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill-memory-entities")

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))

DEFAULT_NEO4J_URI = "bolt://127.0.0.1:7687"
DEFAULT_NEO4J_USER = "neo4j"

_SAMPLE_LIMIT = 10
_EXIT_OK = 0
_EXIT_NEO4J_UNAVAILABLE = 2


def _load_dotenv_into_environ() -> None:
    """Best-effort load of repo-root .env for host runs. Caller-set env wins."""
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in os.environ:
                continue
            os.environ[key] = value.strip().strip('"').strip("'")
    except OSError:
        return


def _get_neo4j_driver() -> Any:
    """Return a Neo4j driver, or None if unreachable/misconfigured."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        log.error("neo4j package not installed.")
        return None

    uri = os.environ.get("NEO4J_URI", DEFAULT_NEO4J_URI)
    user = os.environ.get("NEO4J_USER", DEFAULT_NEO4J_USER)
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        log.error("NEO4J_PASSWORD is not set — check .env.")
        return None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        return driver
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        log.error("Neo4j connection failed (uri=%s user=%s): %s", uri, user, exc)
        return None


_UNLINKED = """
MATCH (m:Memory)
WHERE coalesce(m.archived, false) = false
  AND NOT exists((m)-[:MENTIONS]->(:Entity))
  AND m.text IS NOT NULL AND trim(m.text) <> ''
RETURN m.id AS id, m.text AS text, m.created_at AS created_at
ORDER BY m.created_at
"""


def fetch_unlinked(driver: Any, limit: int | None) -> list[dict[str, Any]]:
    """Return memories with no entity edge yet, oldest first."""
    cypher = _UNLINKED + ("LIMIT $limit" if limit else "")
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher, limit=limit)]


def counts(driver: Any) -> tuple[int, int]:
    """Return (total memories, memories with >=1 entity edge)."""
    with driver.session() as session:
        row = session.run(
            """
            MATCH (m:Memory) WHERE coalesce(m.archived, false) = false
            RETURN count(m) AS total,
                   sum(CASE WHEN exists((m)-[:MENTIONS]->(:Entity))
                            THEN 1 ELSE 0 END) AS linked
            """
        ).single()
    return (int(row["total"]), int(row["linked"])) if row else (0, 0)


async def _extract_and_write(driver: Any, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Extract entities per memory and upsert MENTIONS edges."""
    from app.db.neo4j.entity import upsert_entities_for_memory
    from core.agents.entity_extraction import default_llm_caller, extract_entities_from_text

    stats = {"processed": 0, "linked": 0, "no_entities": 0, "failed": 0, "edges": 0}
    for i, row in enumerate(rows, 1):
        mid = row["id"]
        try:
            entities = await extract_entities_from_text(
                row["text"] or "", llm_caller=default_llm_caller, max_chars=8_000,
            )
        except Exception as exc:  # noqa: BLE001 — one bad memory must not abort the run
            log.warning("[%d/%d] %s — extraction failed: %s", i, len(rows), mid, exc)
            stats["failed"] += 1
            continue

        stats["processed"] += 1
        if not entities:
            stats["no_entities"] += 1
            log.info("[%d/%d] %s — no entities", i, len(rows), mid)
            continue

        written = await asyncio.to_thread(
            upsert_entities_for_memory, driver, mid, entities,
        )
        edges = int(written.get("edges_upserted", 0))
        stats["edges"] += edges
        if edges:
            stats["linked"] += 1
        log.info(
            "[%d/%d] %s — %d entities, %d edges",
            i, len(rows), mid, len(entities), edges,
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write MENTIONS edges. Without it the script only reports.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N memories (applies to both modes).",
    )
    args = parser.parse_args(argv)

    _load_dotenv_into_environ()
    driver = _get_neo4j_driver()
    if driver is None:
        return _EXIT_NEO4J_UNAVAILABLE

    try:
        total, linked = counts(driver)
        rows = fetch_unlinked(driver, args.limit)
        pct = (100.0 * linked / total) if total else 0.0
        log.info(
            "memories=%d linked=%d (%.1f%%) unlinked-with-text=%d",
            total, linked, pct, len(rows),
        )

        if not rows:
            log.info("Nothing to backfill.")
            return _EXIT_OK

        if not args.apply:
            log.info("DRY RUN — re-run with --apply to write. Sample:")
            for row in rows[:_SAMPLE_LIMIT]:
                preview = " ".join((row["text"] or "").split())[:100]
                log.info("  %s  %s", row["id"], preview)
            if len(rows) > _SAMPLE_LIMIT:
                log.info("  ... and %d more", len(rows) - _SAMPLE_LIMIT)
            return _EXIT_OK

        stats = asyncio.run(_extract_and_write(driver, rows))
        total_after, linked_after = counts(driver)
        pct_after = (100.0 * linked_after / total_after) if total_after else 0.0
        log.info(
            "done: processed=%d linked=%d no_entities=%d failed=%d edges=%d",
            stats["processed"], stats["linked"],
            stats["no_entities"], stats["failed"], stats["edges"],
        )
        log.info(
            "linkage: %d/%d (%.1f%%) -> %d/%d (%.1f%%)",
            linked, total, pct, linked_after, total_after, pct_after,
        )
        return _EXIT_OK
    finally:
        driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
