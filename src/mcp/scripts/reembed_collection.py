# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-embed a domain's ChromaDB collection under a new embedding model.

Implements the dual-collection migration pattern documented in
``docs/EMBEDDING_MIGRATIONS.md``. Reads the source collection in
batches, writes to a versioned target collection
(``<domain>__<target_version>``), and is resumable: chunks already
present in the target are skipped on re-run.

Usage (run inside Docker MCP container):

    python -m scripts.reembed_collection \\
        --domain code \\
        --target-model "Snowflake/snowflake-arctic-embed-l-v2.0" \\
        --target-version "snowflake-arctic-embed-l-v2.0" \\
        --batch-size 256 \\
        --dry-run

Drop ``--dry-run`` (or pass ``--execute``) to perform the writes.

The script intentionally does NOT swap the live collection. After
validating against the eval harness, the operator flips
``EMBEDDING_MODEL_VERSIONS_PER_DOMAIN`` in settings to cut over.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from typing import Any

import chromadb
from chromadb.api.types import IncludeEnum

import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reembed-collection")


def _slug(version: str) -> str:
    """Sanitize a version string for use as a ChromaDB collection suffix."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", version)


def _target_collection_name(domain: str, target_version: str) -> str:
    """Versioned target collection, e.g. ``code__arctic-embed-l-v2.0``."""
    return f"{config.collection_name(domain)}__{_slug(target_version)}"


def _connect_chroma() -> chromadb.ClientAPI:
    """Create a ChromaDB HTTP client from CHROMA_URL."""
    url = config.CHROMA_URL.rstrip("/")
    # Strip protocol for the host arg; ChromaDB's HttpClient takes host+port separately
    host_part = url.replace("http://", "").replace("https://", "")
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_part
        port = 8000
    return chromadb.HttpClient(host=host, port=port)


def _collection_count(coll: Any) -> int:
    """Return chunk count for a collection (handles missing-collection)."""
    try:
        return int(coll.count())
    except Exception:  # noqa: BLE001 — best-effort cardinality probe
        return -1


def _existing_target_ids(coll: Any) -> set[str]:
    """Return the set of chunk_ids already present in the target."""
    out: set[str] = set()
    offset = 0
    page = 5000
    while True:
        try:
            batch = coll.get(limit=page, offset=offset, include=[])
        except Exception:  # noqa: BLE001 — collection may be brand-new/empty
            break
        ids = batch.get("ids", []) or []
        if not ids:
            break
        out.update(ids)
        if len(ids) < page:
            break
        offset += page
    return out


def reembed(
    *,
    domain: str,
    target_model: str,
    target_version: str,
    batch_size: int,
    execute: bool,
) -> int:
    """Run the re-embed pass. Returns 0 on success, non-zero on failure."""
    started = time.perf_counter()

    chroma = _connect_chroma()
    source_name = config.collection_name(domain)
    target_name = _target_collection_name(domain, target_version)

    try:
        source = chroma.get_collection(name=source_name)
    except Exception as e:  # noqa: BLE001
        logger.error("Source collection %s not found: %s", source_name, e)
        return 2

    source_count = _collection_count(source)
    if source_count <= 0:
        logger.error(
            "Source collection %s is empty or unreadable (count=%d) — nothing to do",
            source_name, source_count,
        )
        return 3

    logger.info("Source: %s (%d chunks)", source_name, source_count)
    logger.info("Target: %s", target_name)
    logger.info("Target model: %s (version stamp: %s)", target_model, target_version)

    if not execute:
        batches = (source_count + batch_size - 1) // batch_size
        logger.info(
            "[DRY-RUN] Would re-embed %d chunks across %d batches "
            "of %d. Re-run with --execute to perform writes.",
            source_count, batches, batch_size,
        )
        return 0

    # NOTE: ChromaDB's embedding function is bound at collection creation time.
    # In this codebase, the embedder is configured via EMBEDDING_MODEL on the
    # client side (utils/embeddings.py). The recommended migration path is to
    # pre-stage the new EMBEDDING_MODEL in the running container's config so
    # newly-created collections pick it up. The script honors whichever
    # embedder is currently active in the container — the operator must set
    # EMBEDDING_MODEL=<target_model> before invoking with --execute.
    if config.EMBEDDING_MODEL != target_model:
        logger.error(
            "Container's active EMBEDDING_MODEL (%s) does not match "
            "--target-model (%s). Set EMBEDDING_MODEL=%s in the container "
            "env (and restart) before re-running with --execute.",
            config.EMBEDDING_MODEL, target_model, target_model,
        )
        return 4

    target = chroma.get_or_create_collection(name=target_name)
    already_done = _existing_target_ids(target)
    if already_done:
        logger.info(
            "Resume mode: %d chunks already present in target — will be skipped",
            len(already_done),
        )

    # Iterate the source in batches via offset-paginated get()
    written = 0
    skipped = 0
    offset = 0
    while True:
        batch = source.get(
            limit=batch_size,
            offset=offset,
            include=[IncludeEnum.documents, IncludeEnum.metadatas],
        )
        ids = batch.get("ids", []) or []
        if not ids:
            break
        documents = batch.get("documents", []) or []
        metadatas = batch.get("metadatas", []) or []

        # Filter out chunks already in the target
        new_ids: list[str] = []
        new_docs: list[str] = []
        new_metas: list[dict[str, Any]] = []
        for cid, doc, meta in zip(ids, documents, metadatas, strict=True):
            if cid in already_done:
                skipped += 1
                continue
            new_ids.append(cid)
            new_docs.append(doc)
            # Stamp the new version on metadata so query-side can route correctly
            meta = dict(meta or {})
            meta["embedding_model_version"] = target_version
            new_metas.append(meta)

        if new_ids:
            try:
                # ChromaDB requires metadata values to be primitives
                # (str/int/float/bool) — chunk metadata in this codebase
                # already conforms; the cast satisfies the type-checker.
                target.add(
                    ids=new_ids,
                    documents=new_docs,
                    metadatas=new_metas,  # type: ignore[arg-type]
                )
                written += len(new_ids)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Batch failed at offset=%d (size=%d): %s — re-run to resume",
                    offset, len(new_ids), e,
                )
                return 5

        offset += len(ids)
        elapsed = time.perf_counter() - started
        rate = written / elapsed if elapsed > 0 else 0.0
        logger.info(
            "stage=reembed_collection batch_offset=%d written=%d skipped=%d "
            "elapsed=%.1fs rate=%.1f/s",
            offset, written, skipped, elapsed, rate,
        )

        if len(ids) < batch_size:
            break

    target_count = _collection_count(target)
    elapsed = time.perf_counter() - started
    logger.info(
        "Done. source=%d target=%d written=%d skipped=%d elapsed=%.1fs",
        source_count, target_count, written, skipped, elapsed,
    )

    if target_count != source_count:
        logger.warning(
            "Cardinality mismatch: source=%d target=%d (delta=%d). "
            "Re-run to retry; check Langfuse traces for embedder errors.",
            source_count, target_count, source_count - target_count,
        )
        return 6

    logger.info(
        "Cardinality match. Validate with the eval harness, then flip "
        "EMBEDDING_MODEL_VERSIONS_PER_DOMAIN[%r] = %r in settings to cut over.",
        domain, target_version,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-embed a domain's ChromaDB collection under a new model "
            "via the dual-collection migration pattern."
        ),
    )
    parser.add_argument(
        "--domain", required=True,
        help="KB domain to migrate (e.g. 'code', 'finance', 'projects').",
    )
    parser.add_argument(
        "--target-model", required=True,
        help="HuggingFace repo ID of the target embedding model.",
    )
    parser.add_argument(
        "--target-version", required=True,
        help=(
            "Short version label stamped on chunk metadata "
            "(e.g. 'arctic-embed-l-v2.0'). Used as the target collection suffix."
        ),
    )
    parser.add_argument(
        "--batch-size", type=int, default=256,
        help="Chunks per re-embed batch (default 256).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without writing (default if neither flag).",
    )
    mode.add_argument(
        "--execute", action="store_true",
        help="Actually perform the dual-write.",
    )

    args = parser.parse_args(argv)
    execute = bool(args.execute) and not args.dry_run

    return reembed(
        domain=args.domain,
        target_model=args.target_model,
        target_version=args.target_version,
        batch_size=args.batch_size,
        execute=execute,
    )


if __name__ == "__main__":
    sys.exit(main())
