#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Seed BEIR datasets into the live cerid stack (Workstream E Phase 2b).

Downloads SciFact (5K docs, 300 queries) and NFCorpus (3.6K docs, 323
queries) from the BEIR public mirror, parses the standard BEIR layout
(corpus.jsonl + queries.jsonl + qrels/test.tsv), and ingests every
document via ``app.services.ingestion.ingest_content`` with
``sub_category="beir-{dataset_name}"`` for surgical wipe protection.

Then writes our harness-format dataset JSONL files to
``app/eval/datasets/beir_{dataset_name}.jsonl`` carrying the REAL BEIR
queries + ``relevant_paths`` referencing the just-ingested filenames
(``{dataset}/{doc_id}.md``). The IR regression harness scores these
the same way it scores the synthetic eval-corpus.

Provenance + license: BEIR datasets are CC BY-SA 4.0 (NFCorpus) and
CC BY 2.5 (SciFact). Source: BEIR public mirror at
https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/

Idempotent: cerid dedupes ingest by content_hash; re-running treats
``status=duplicate`` as success. The download is also cached — if
the zip already exists locally the script skips re-fetching.

Usage from the host:
    docker cp scripts/seed_beir_corpus.py ai-companion-mcp:/tmp/
    docker exec -e PYTHONPATH=/app ai-companion-mcp \\
        python /tmp/seed_beir_corpus.py
    # or, to seed only one dataset:
    docker exec -e PYTHONPATH=/app -e CERID_BEIR_DATASETS=scifact \\
        ai-companion-mcp python /tmp/seed_beir_corpus.py

Env vars:
    CERID_BEIR_DATASETS   default "scifact,nfcorpus"
    CERID_BEIR_CACHE_DIR  default /tmp/beir-cache (writable in any container;
                          override to a persistent mount to keep zips across
                          restarts — see data/eval-corpus/beir/MANIFEST.md)
    CERID_BEIR_OUT_DIR    default /app/app/eval/datasets (where JSONLs are written)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed-beir")

BEIR_MIRROR = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets"

# Per-dataset metadata. License + size sourced from BEIR project.
DATASETS: dict[str, dict[str, object]] = {
    "scifact": {
        "url": f"{BEIR_MIRROR}/scifact.zip",
        "domain": "research",  # closest cerid domain
        "license": "CC BY 2.5",
        "expected_corpus": 5183,
        "expected_queries": 300,
        # Title field carries useful signal in scifact (real paper titles)
        "include_title": True,
    },
    "nfcorpus": {
        "url": f"{BEIR_MIRROR}/nfcorpus.zip",
        "domain": "general",
        "license": "CC BY-SA 4.0",
        "expected_corpus": 3633,
        "expected_queries": 323,
        "include_title": True,
    },
}


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` if not already present."""
    if dest.exists():
        log.info("cached %s (%d bytes)", dest.name, dest.stat().st_size)
        return
    log.info("downloading %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Stream to disk to avoid loading the whole zip in memory.
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as f:  # noqa: S310
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            f.write(chunk)
    log.info("downloaded %d bytes", dest.stat().st_size)


def _extract(zip_path: Path, target_dir: Path) -> Path:
    """Extract ``zip_path`` into ``target_dir/{dataset}/`` and return that path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target_dir)
    # BEIR zips extract to a top-level dir named after the dataset.
    extracted = target_dir / zip_path.stem
    if not extracted.is_dir():
        msg = f"BEIR zip layout unexpected: {extracted} not a directory after extract"
        raise RuntimeError(msg)
    return extracted


def _parse_corpus(extracted: Path) -> dict[str, dict[str, str]]:
    """Parse ``corpus.jsonl`` into ``{doc_id: {"title": ..., "text": ...}}``."""
    corpus_path = extracted / "corpus.jsonl"
    docs: dict[str, dict[str, str]] = {}
    with open(corpus_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            doc_id = entry["_id"]
            docs[doc_id] = {
                "title": entry.get("title", "") or "",
                "text": entry.get("text", "") or "",
            }
    log.info("parsed %d corpus docs", len(docs))
    return docs


def _parse_queries(extracted: Path) -> dict[str, str]:
    """Parse ``queries.jsonl`` into ``{query_id: query_text}``."""
    queries_path = extracted / "queries.jsonl"
    queries: dict[str, str] = {}
    with open(queries_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            queries[entry["_id"]] = entry.get("text", "") or ""
    log.info("parsed %d queries", len(queries))
    return queries


def _parse_qrels(extracted: Path) -> dict[str, set[str]]:
    """Parse ``qrels/test.tsv`` (TREC format).

    Returns ``{query_id: {relevant_doc_id, ...}}`` for entries with
    score ≥ 1 (BEIR uses 0/1/2 in some sets — anything > 0 is
    treated as relevant per BEIR convention).
    """
    qrels_path = extracted / "qrels" / "test.tsv"
    qrels: defaultdict[str, set[str]] = defaultdict(set)
    with open(qrels_path) as f:
        # Skip header line "query-id\tcorpus-id\tscore"
        next(f, None)
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 3:
                continue
            query_id, doc_id, score_str = parts[0], parts[1], parts[2]
            try:
                score = int(score_str)
            except ValueError:
                continue
            if score > 0:
                qrels[query_id].add(doc_id)
    log.info("parsed qrels covering %d queries", len(qrels))
    return dict(qrels)


def _doc_filename(doc_id: str) -> str:
    """Convert a BEIR doc_id into a stable, filesystem-safe filename.

    BEIR doc_ids are typically short alphanumerics (e.g., ``MED-12345``,
    ``4983``). We use ``{doc_id}.md`` directly — no slashes, no spaces,
    safe across all OSes. The filename is what the harness will
    resolve via ``relevant_paths``.
    """
    safe = doc_id.replace("/", "_").replace("\\", "_")
    return f"{safe}.md"


def _ingest_dataset(
    dataset_name: str,
    docs: dict[str, dict[str, str]],
    domain: str,
) -> tuple[int, int, int]:
    """Ingest all docs into cerid via ingest_content.

    Returns ``(ok, duplicate, failed)``.
    """
    from app.services.ingestion import ingest_content

    ok = duplicate = failed = 0
    for doc_id, parts in docs.items():
        title = parts["title"]
        text = parts["text"]
        # BEIR docs vary: SciFact has titles + abstracts; NFCorpus has
        # title + body. Compose a "title\n\nbody" structure so the
        # harness sees consistent headed content.
        content = f"{title}\n\n{text}".strip() if title else text
        if not content:
            failed += 1
            continue

        filename = _doc_filename(doc_id)
        sub_category = f"beir-{dataset_name}"
        metadata = {
            "filename": filename,
            "domain": domain,
            "file_type": "md",
            "client_source": f"seed-beir-{dataset_name}",
            "summary": f"BEIR/{dataset_name} {doc_id}: {title[:160]}",
            "tags_json": json.dumps(["eval-corpus", sub_category, dataset_name]),
            "keywords_json": json.dumps([]),
            "sub_category": sub_category,
            # Stash the BEIR doc_id explicitly for downstream lookups
            "beir_doc_id": doc_id,
        }
        try:
            result = ingest_content(content, domain, metadata, skip_quality=True)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log.error("ingest_failed %s/%s: %s", dataset_name, doc_id, exc)
            failed += 1
            continue
        status = result.get("status")
        if status == "success":
            ok += 1
        elif status == "duplicate":
            duplicate += 1
        else:
            failed += 1
            log.error("FAIL %s/%s — %s", dataset_name, doc_id, result)
    return ok, duplicate, failed


def _write_dataset_jsonl(
    dataset_name: str,
    queries: dict[str, str],
    qrels: dict[str, set[str]],
    domain: str,
    out_dir: Path,
) -> Path:
    """Write our harness-format JSONL with REAL BEIR queries + relevant_paths.

    relevant_paths references the ingested filename in the form
    ``{domain}/{filename}`` so the harness's
    ``_resolve_paths_to_artifact_ids`` can map them via the existing
    Neo4j filename resolver.
    """
    out_path = out_dir / f"beir_{dataset_name}.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out_path, "w") as f:
        for query_id, query_text in queries.items():
            relevant_doc_ids = qrels.get(query_id, set())
            if not relevant_doc_ids:
                # Skip queries with no positive judgments — they can't
                # be scored against an artifact-id ranking.
                continue
            entry = {
                "query": query_text,
                "relevant_paths": sorted(
                    f"{domain}/{_doc_filename(d)}" for d in relevant_doc_ids
                ),
                "domain": domain,
                "beir_query_id": query_id,
            }
            f.write(json.dumps(entry) + "\n")
            written += 1
    log.info("wrote %d queries -> %s", written, out_path)
    return out_path


def main() -> int:
    requested = (
        os.getenv("CERID_BEIR_DATASETS", "scifact,nfcorpus").strip().split(",")
    )
    requested = [name.strip() for name in requested if name.strip()]
    if not requested:
        log.error("no datasets requested via CERID_BEIR_DATASETS")
        return 1

    cache_dir = Path(os.getenv("CERID_BEIR_CACHE_DIR", "/tmp/beir-cache"))
    out_dir = Path(os.getenv("CERID_BEIR_OUT_DIR", "/app/app/eval/datasets"))

    overall_failed = 0

    for name in requested:
        if name not in DATASETS:
            log.error("unknown BEIR dataset: %s (known: %s)", name, list(DATASETS))
            overall_failed += 1
            continue
        spec = DATASETS[name]
        url = str(spec["url"])
        domain = str(spec["domain"])

        log.info("=== %s ===", name)
        log.info("  domain=%s license=%s", domain, spec["license"])

        zip_path = cache_dir / f"{name}.zip"
        try:
            _download(url, zip_path)
        except Exception as exc:  # noqa: BLE001 — surface clearly
            log.error("download failed %s: %s", url, exc)
            overall_failed += 1
            continue

        try:
            extracted = _extract(zip_path, cache_dir)
        except Exception as exc:  # noqa: BLE001
            log.error("extract failed %s: %s", zip_path, exc)
            overall_failed += 1
            continue

        try:
            docs = _parse_corpus(extracted)
            queries = _parse_queries(extracted)
            qrels = _parse_qrels(extracted)
        except Exception as exc:  # noqa: BLE001
            log.error("parse failed for %s: %s", name, exc)
            overall_failed += 1
            continue

        ok, dup, fail = _ingest_dataset(name, docs, domain)
        log.info(
            "  ingest done: ok=%d duplicate=%d failed=%d (total=%d)",
            ok, dup, fail, len(docs),
        )

        # Write the harness-format JSONL only after ingest completed
        # so relevant_paths references docs that actually exist.
        try:
            _write_dataset_jsonl(name, queries, qrels, domain, out_dir)
        except Exception as exc:  # noqa: BLE001
            log.error("dataset jsonl write failed for %s: %s", name, exc)
            overall_failed += 1
            continue

        if fail:
            overall_failed += 1

    log.info("Done. failed_datasets=%d", overall_failed)
    return 0 if overall_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
