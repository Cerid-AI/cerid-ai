#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Surgically wipe seeded eval-corpus artifacts from the live stack
(Workstream E Phase 3b/2b — measure-first wire-in cycle).

Used to wipe + re-seed the eval corpus when toggling ingest-time flags
that change chunk shape or content (ENABLE_CONTEXTUAL_CHUNKS,
ENABLE_LAYOUT_AWARE_PARSING).

Wipes artifacts whose ``sub_category`` matches one of the eval-corpus
families:

* ``eval-corpus`` (synthetic 20-doc corpus from ``seed_eval_corpus.py``)
* ``beir-scifact`` (5K-doc SciFact subset from ``seed_beir_corpus.py``)
* ``beir-nfcorpus`` (3.6K-doc NFCorpus subset from same)

The script never touches non-eval artifacts (tax returns, resumes,
conversation memories, etc.) in the same domains because every
non-eval artifact carries a different ``sub_category`` value.

Wipe scope per artifact:
* Neo4j: DETACH DELETE the Artifact node + all relationships
* ChromaDB: drop the chunk_ids from the per-domain collection
* BM25: filter the per-domain JSONL corpus file in-place to drop the
  matching chunk_ids (chunk_id format: ``{artifact_id}_chunk_{i}``),
  then call ``rebuild_all()`` so live indexes reload from disk

Usage from the host:
    # Wipe all eval families (default)
    docker exec -e PYTHONPATH=/app ai-companion-mcp \\
        python /app/../scripts/wipe_eval_corpus.py

    # Wipe only specific families
    docker exec -e PYTHONPATH=/app -e CERID_WIPE_FAMILIES=eval-corpus \\
        ai-companion-mcp python /app/../scripts/wipe_eval_corpus.py

Env vars:
    CERID_WIPE_FAMILIES   default "eval-corpus,beir-scifact,beir-nfcorpus"
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("wipe-eval-corpus")


def main() -> int:
    try:
        import config
        from app.db.neo4j.artifacts import delete_artifact
        from app.deps import get_chroma, get_neo4j
        from core.retrieval.bm25 import rebuild_all
    except ImportError as exc:
        log.error("import failure (PYTHONPATH=/app required): %s", exc)
        return 2

    neo = get_neo4j()
    chroma = get_chroma()

    # 1. Discover eval-corpus artifacts across the configured families.
    # Multiple families (synthetic + BEIR-* corpora) all live under
    # well-known sub_category prefixes; the wipe is scoped to those
    # so non-eval artifacts in the same Cerid domains stay safe.
    families = (
        os.getenv("CERID_WIPE_FAMILIES", "eval-corpus,beir-scifact,beir-nfcorpus")
        .strip()
        .split(",")
    )
    families = [f.strip() for f in families if f.strip()]
    log.info("Wipe families: %s", families)

    with neo.session() as s:
        rows = s.run(
            "MATCH (a:Artifact) WHERE a.sub_category IN $families "
            "RETURN a.id AS id, a.domain AS domain, a.filename AS filename, "
            "       a.chunk_ids AS chunk_ids, a.sub_category AS sub_category",
            families=families,
        ).data()

    if not rows:
        log.info("No eval-corpus artifacts found in families %s. Nothing to wipe.", families)
        return 0

    by_family: dict[str, int] = {}
    for r in rows:
        by_family[r["sub_category"]] = by_family.get(r["sub_category"], 0) + 1
    log.info(
        "Found %d eval-corpus artifacts to wipe (by family: %s)",
        len(rows), by_family,
    )

    # Build per-domain chunk_id sets for downstream cleanup
    chunks_by_domain: dict[str, set[str]] = {}
    artifacts_by_domain: dict[str, list[str]] = {}
    for row in rows:
        domain = row["domain"] or "general"
        artifacts_by_domain.setdefault(domain, []).append(row["id"])
        raw = row["chunk_ids"] or "[]"
        try:
            cids = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            cids = []
        chunks_by_domain.setdefault(domain, set()).update(cids)

    # 2. Delete Neo4j artifacts (also tells us their chunk_ids if not stored)
    deleted_neo4j = 0
    for row in rows:
        try:
            result = delete_artifact(neo, row["id"])
            if result.get("deleted"):
                deleted_neo4j += 1
                # Backfill chunk_ids from delete_artifact's response if neo4j-stored
                # column was empty for some reason.
                cids = result.get("chunk_ids", [])
                if cids:
                    chunks_by_domain.setdefault(
                        row["domain"] or "general", set(),
                    ).update(cids)
        except Exception as exc:  # noqa: BLE001 — surface but continue
            log.warning("neo4j delete failed for %s: %s", row["id"][:8], exc)

    log.info("Neo4j: deleted %d artifacts", deleted_neo4j)

    # 3. Delete chunks from ChromaDB collections
    chroma_removed = 0
    for domain, chunk_ids in chunks_by_domain.items():
        if not chunk_ids:
            continue
        coll_name = config.collection_name(domain)
        try:
            coll = chroma.get_collection(name=coll_name)
            coll.delete(ids=list(chunk_ids))
            chroma_removed += len(chunk_ids)
            log.info(
                "Chroma: removed %d chunks from %s", len(chunk_ids), coll_name,
            )
        except Exception as exc:  # noqa: BLE001 — collection may not exist
            log.warning("chroma delete failed for %s: %s", coll_name, exc)

    # 4. Surgical BM25 corpus filter — keep non-eval entries, drop ours
    bm25_dir = Path(config.BM25_DATA_DIR)
    bm25_removed = 0
    for domain, chunk_ids in chunks_by_domain.items():
        if not chunk_ids:
            continue
        corpus_file = bm25_dir / f"{domain}.jsonl"
        if not corpus_file.exists():
            continue
        kept: list[str] = []
        dropped = 0
        with open(corpus_file) as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)  # preserve malformed lines as-is
                    continue
                if entry.get("id") in chunk_ids:
                    dropped += 1
                else:
                    kept.append(line)
        if dropped:
            tmp = corpus_file.with_suffix(".jsonl.tmp")
            with open(tmp, "w") as f:
                for line in kept:
                    f.write(line + "\n")
            os.replace(tmp, corpus_file)
            log.info("BM25: dropped %d entries from %s", dropped, corpus_file.name)
            bm25_removed += dropped

    # 5. Reload BM25 indexes from cleaned corpora
    if bm25_removed:
        try:
            rebuild_all()
            log.info("BM25: reloaded indexes from disk")
        except Exception as exc:  # noqa: BLE001
            log.warning("BM25 rebuild failed: %s", exc)

    log.info(
        "Done. neo4j=%d chroma_chunks=%d bm25_chunks=%d",
        deleted_neo4j, chroma_removed, bm25_removed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
