# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Startup-time dimension validation for ChromaDB collections.

On MCP boot, iterates every Chroma collection under the embedding-aware
client, probes its stored embedding dim via ``collection.peek(1)``, and
compares against the singleton embedder's output dim.

On mismatch we log ERROR with the offending collection name, the actual
dim, the expected dim, and a remediation pointer to
``POST /admin/collections/repair``.  We do NOT hard-fail — unlike the
``NEO4J_PASSWORD`` check in ``deps.py`` (which has no runtime recovery
path), a dim mismatch has a dedicated recovery endpoint.  Hard-failing
here would lock the operator out of the very endpoint the log points
them at.  This is the deliberate divergence from the deps pattern.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.startup")


def _probe_collection_dim(collection: Any) -> int | None:
    """Return the stored embedding dim for a collection, or ``None`` if empty.

    ``collection.peek(limit)`` returns a dict with an ``embeddings`` key
    (list of vectors) when the collection has any documents.  For a
    freshly-created empty collection there is no stored dim yet — return
    ``None`` so the caller can skip it (an empty collection cannot
    mismatch anything).
    """
    try:
        peek = collection.peek(1)
    except Exception as exc:
        logger.warning("Collection peek failed for %r: %s", getattr(collection, "name", "?"), exc)
        return None

    embeddings = None
    if isinstance(peek, dict):
        embeddings = peek.get("embeddings")
    else:
        embeddings = getattr(peek, "embeddings", None)

    if not embeddings:
        return None

    first = embeddings[0]
    try:
        return len(first)
    except TypeError:
        return None


def validate_collection_dimensions(chroma_client: Any, expected_dim: int) -> list[dict[str, Any]]:
    """Probe every collection in the Chroma client and compare dim vs expected.

    Returns a list of mismatch records, one per offending collection.
    Each record has keys: ``collection``, ``actual_dim``, ``expected_dim``.
    Also logs an ERROR line per mismatch with a remediation pointer.
    """
    mismatches: list[dict[str, Any]] = []

    try:
        collections = chroma_client.list_collections()
    except Exception as exc:
        logger.warning("Could not enumerate Chroma collections at startup: %s", exc)
        return mismatches

    for entry in collections:
        # Chroma returns Collection objects or dicts depending on version;
        # both expose a ``name`` attribute or key.
        name = getattr(entry, "name", None)
        if name is None and isinstance(entry, dict):
            name = entry.get("name")
        if not name:
            continue

        try:
            coll = chroma_client.get_collection(name=name)
        except Exception as exc:
            logger.warning("Could not fetch collection %s for dim probe: %s", name, exc)
            continue

        actual = _probe_collection_dim(coll)
        if actual is None:
            # Empty collection — nothing to validate.
            continue

        if actual != expected_dim:
            logger.error(
                "embedding_dim_mismatch: collection=%r actual_dim=%d expected_dim=%d — "
                "repair via POST /admin/collections/repair "
                "{\"collection_name\": %r, \"dry_run\": true} (set dry_run=false to apply)",
                name, actual, expected_dim, name,
            )
            mismatches.append({
                "collection": name,
                "actual_dim": actual,
                "expected_dim": expected_dim,
            })

    return mismatches


def run_startup_dim_check() -> list[dict[str, Any]]:
    """Entry point called from ``app.main`` lifespan.

    Returns the list of mismatches so callers (or tests) can introspect.
    Always returns — never raises — so a probe failure cannot wedge startup.
    """
    try:
        from app.deps import get_chroma
        from core.utils.embeddings import get_embedding_dim

        expected = get_embedding_dim()
        client = get_chroma()
        mismatches = validate_collection_dimensions(client, expected)
        if not mismatches:
            logger.info("Startup dim check passed (expected_dim=%d)", expected)
        else:
            logger.error(
                "Startup dim check FAILED: %d collection(s) mismatched — "
                "server will continue running so the repair endpoint is reachable, "
                "but ingest/query against affected collections will error until repaired.",
                len(mismatches),
            )
        return mismatches
    except Exception as exc:
        logger.warning("Startup dim check skipped (non-fatal): %s", exc)
        return []
