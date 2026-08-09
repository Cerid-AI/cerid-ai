#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Refresh GraphRAG communities + their LLM summaries.

Workstream E Phase 4b.4. Composes Phase 4b.1 (Leiden) + Phase 4b.2
(community summaries) into one operator-invocable script. Suitable
for nightly scheduler runs (e.g. apscheduler 02:00 server time) and
for ad-hoc invocation after a corpus refresh.

Usage:
    docker exec ai-companion-mcp python -m scripts.refresh_communities
    docker exec ai-companion-mcp python -m scripts.refresh_communities \\
        --max-communities 50           # cap LLM cost; useful for first runs
    docker exec ai-companion-mcp python -m scripts.refresh_communities \\
        --skip-detection               # only re-summarise; reuse last partition
    docker exec ai-companion-mcp python -m scripts.refresh_communities \\
        --force-refresh-summaries      # ignore Community.summary IS NULL filter

Output: JSON summary on stdout. Idempotent — safe to re-run; existing
summaries are skipped unless ``--force-refresh-summaries`` is set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("refresh-communities")


async def _run(args: argparse.Namespace) -> int:
    from app.db.neo4j.community_detection import detect_communities
    from app.db.neo4j.community_summaries import summarize_communities
    from app.deps import get_chroma, get_neo4j

    driver = get_neo4j()
    chroma = get_chroma()

    summary: dict = {}

    if not args.skip_detection:
        logger.info("Phase 4b.1: detecting communities...")
        summary["detection"] = detect_communities(
            driver,
            min_community_size=args.min_community_size,
            max_levels=args.max_levels,
        )

    logger.info("Phase 4b.2: summarising communities (level=%d)...", args.level)
    summary["summaries"] = await summarize_communities(
        driver,
        chroma,
        level=args.level,
        top_k_entities=args.top_k_entities,
        max_communities=args.max_communities,
        skip_with_existing_summary=not args.force_refresh_summaries,
    )

    print(json.dumps(summary, default=str, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh GraphRAG community partition + summaries",
    )
    parser.add_argument("--skip-detection", action="store_true",
                        help="Only re-summarise; do not re-run Leiden")
    parser.add_argument("--min-community-size", type=int, default=2)
    parser.add_argument("--max-levels", type=int, default=5)
    parser.add_argument("--level", type=int, default=0,
                        help="Hierarchical level to summarise (default: 0 = finest)")
    parser.add_argument("--top-k-entities", type=int, default=10)
    parser.add_argument("--max-communities", type=int, default=None,
                        help="Cap LLM-summarisation runs (default: all)")
    parser.add_argument("--force-refresh-summaries", action="store_true",
                        help="Re-summarise communities that already have a summary")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
