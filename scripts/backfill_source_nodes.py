#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""CL-1 source-node backfill — link pre-CL-1 artifacts to their :Source and
recompute per-source counters from graph truth.

Why this exists
---------------
The ``:Source`` write path (``m0003_source_nodes.py``) only started funding
the source economy for NEW ingests: ``app.services.ingestion.ingest_content``
now calls ``link_artifact`` (MERGE ``(:Artifact)-[:FROM_SOURCE]->(:Source)``)
and ``increment_source_counters(artifacts=1, chunks=len(child_chunk_ids))``
whenever an ingest's metadata carries a ``source_id`` that resolves to a real
``:Source`` (see the "CL-1 — fund the :Source economy" block). Every artifact
ingested BEFORE that wiring has no FROM_SOURCE edge, and its source's
``total_artifacts`` / ``total_chunks`` counters are still 0. This one-shot
backfill closes that gap.

Where ``source_id`` lives for existing artifacts
------------------------------------------------
It is NOT on the ``:Artifact`` node. ``graph.create_artifact`` only persists
``client_source`` (a provenance *string* such as "mobile" / "browser-ext"),
never the source UUID. The ``source_id`` travels through ingest metadata into
each chunk's ChromaDB metadata (``ingest_content`` merges ``metadata`` into
``base_meta``, which is spread onto every chunk's Chroma metadata; it is NOT in
``CHROMA_ENCRYPTED_FIELDS`` so it is stored in plaintext). This script therefore
reconstructs the artifact→source association by scanning Chroma chunk metadata,
collapsing by ``artifact_id``, and joining back to the ``:Artifact`` nodes.

Strategy
--------
1. Scan every Chroma collection's chunk metadata → ``{artifact_id: source_id}``
   (also collecting ``client_source`` / ``source_type`` for triage).
2. From Neo4j: the full ``:Artifact`` id → ``chunk_count`` map, the existing
   FROM_SOURCE edges, and the known ``:Source`` ids (+ current counters) via
   ``list_sources``.
3. For each artifact node that carries a ``source_id`` resolving to a real
   ``:Source``: MERGE the FROM_SOURCE edge (``link_artifact`` is idempotent).
4. Recompute counters in ONE ``SET``-from-graph pass (see idempotency note).

Idempotency (counters)
----------------------
``increment_source_counters`` ADDS a delta, so re-running a naive
increment-based backfill would double-count. Instead this script recomputes
each source's counters directly from the graph in a single pass::

    MATCH (a:Artifact)-[:FROM_SOURCE]->(s:Source)
    WITH s, count(a) AS n, sum(coalesce(a.chunk_count, 0)) AS c
    SET s.total_artifacts = n, s.total_chunks = c

``sum(a.chunk_count)`` matches the live path exactly: CL-1 increments
``chunks=len(child_chunk_ids)`` and ``create_artifact`` stores that same value
as ``a.chunk_count``. Combined with the idempotent ``link_artifact`` MERGE, the
whole script is safely re-runnable — a second run creates no new edges and sets
the counters to the identical graph-truth values.

Deliberately NOT touched: ``total_edges`` (CL-1 never funds it) and
``total_artifacts_24h`` (a rolling 24h window — inflating it with historical
backfilled artifacts that did not arrive in the last day would be wrong).

Modes
-----
Default (no flags) is a DRY-RUN: prints sources scanned, artifacts that would
be linked, per-source projected counters (current → projected), and the
artifacts whose source cannot be linked (source_id present but no :Source,
Chroma-orphan chunks with no artifact node, and artifacts with no source_id at
all — grouped by (client_source, source_type) per the m0003 fallback). Nothing
is written.

``--apply`` performs the backfill: MERGEs the FROM_SOURCE edges, then runs the
SET-from-graph counter recompute. Never destructive — only MERGE edges + SET
counters; no node/edge/property is ever deleted.

``--limit N`` caps the number of NEW FROM_SOURCE edges created in a run (bounded
test runs against a live database). The counter recompute always reflects the
full graph, so a bounded run yields partial (but correct-for-what-was-linked)
per-source totals.

Usage (from repo root, either form)::

    .venv/bin/python scripts/backfill_source_nodes.py              # dry-run
    .venv/bin/python -m scripts.backfill_source_nodes --limit 50   # bounded dry-run
    .venv/bin/python scripts/backfill_source_nodes.py --apply      # backfill for real

Env vars (loaded from repo-root ``.env`` if present; caller-set env wins)::

    NEO4J_URI       default "bolt://127.0.0.1:7687"
    NEO4J_USER      default "neo4j"
    NEO4J_PASSWORD  required — never logged.
    CHROMA_URL      default "http://127.0.0.1:8000"
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill-source-nodes")

# Add src/mcp to sys.path so ``app.*`` imports resolve without PYTHONPATH set,
# whether this script is run directly, via ``python -m``, or imported in tests.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))

DEFAULT_NEO4J_URI = "bolt://127.0.0.1:7687"
DEFAULT_NEO4J_USER = "neo4j"
DEFAULT_CHROMA_URL = "http://127.0.0.1:8000"

# Page size for the Chroma metadata scan.
_CHROMA_PAGE = 5000

# Dry-run sample cap for the "cannot link" triage lists.
_SAMPLE_LIMIT = 30

_EXIT_OK = 0
_EXIT_NEO4J_UNAVAILABLE = 2


# ---------------------------------------------------------------------------
# Host bootstrap (mirrors scripts/purge_junk_entities.py)
# ---------------------------------------------------------------------------


def _load_dotenv_into_environ() -> None:
    """Best-effort load of repo-root .env for host (non-Docker) runs.

    Caller-set env wins. Silent on a missing file.
    """
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
    """Return a Neo4j driver, or None if unreachable/misconfigured.

    Reads env at call time (never captured at import) so ``.env`` loaded in
    ``main`` takes effect. Lazy ``neo4j`` import keeps the module import-safe
    (and unit-testable) without the driver package installed.
    """
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


def _get_chroma_client() -> Any:
    """Return a raw ChromaDB HttpClient, or None if unreachable.

    Built directly from ``CHROMA_URL`` (read at call time) rather than through
    ``app.deps.get_chroma`` so the script does not depend on ``config`` having
    captured env at import. No embedding function is attached — the backfill
    only READS metadata (``collection.get(include=["metadatas"])``), which
    never embeds. Chroma being unavailable is non-fatal: the run degrades to a
    counter-recompute over whatever FROM_SOURCE edges already exist.
    """
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except ImportError:
        log.warning("chromadb package not installed — cannot reconstruct source_id links.")
        return None

    url = os.environ.get("CHROMA_URL", DEFAULT_CHROMA_URL)
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000
    try:
        client = chromadb.HttpClient(
            host=host, port=port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        client.heartbeat()
        return client
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI warning
        log.warning("ChromaDB connection failed (host=%s port=%s): %s", host, port, exc)
        return None


# ---------------------------------------------------------------------------
# Chroma scan — reconstruct {artifact_id: source_id}
# ---------------------------------------------------------------------------


def scan_chroma_source_ids(chroma: Any) -> dict[str, dict[str, str]]:
    """Scan every Chroma collection and collapse chunk metadata by artifact_id.

    Returns ``{artifact_id: {"source_id", "client_source", "source_type"}}``.
    A chunk missing a field contributes an empty string for it; the first
    non-empty value per artifact wins. Conflicting non-empty ``source_id``
    values across an artifact's chunks are logged and the first is kept (this
    should not happen — all chunks of one ingest share one source_id).
    """
    out: dict[str, dict[str, str]] = {}
    try:
        collections = chroma.list_collections()
    except Exception as exc:  # noqa: BLE001 — degrade cleanly on a bad client
        log.warning("Could not list Chroma collections: %s", exc)
        return out

    for col in collections:
        name = getattr(col, "name", str(col))
        try:
            coll = chroma.get_collection(name=name)
        except Exception as exc:  # noqa: BLE001 — collection may be gone mid-scan
            log.warning("Skipping collection %s (get_collection failed): %s", name, exc)
            continue

        offset = 0
        seen_here = 0
        while True:
            try:
                batch = coll.get(limit=_CHROMA_PAGE, offset=offset, include=["metadatas"])
            except Exception as exc:  # noqa: BLE001 — empty/new collection or transient error
                log.warning("Metadata scan of %s stopped at offset %d: %s", name, offset, exc)
                break
            metadatas = batch.get("metadatas") or []
            if not metadatas:
                break
            for meta in metadatas:
                if not isinstance(meta, dict):
                    continue
                aid = str(meta.get("artifact_id") or "").strip()
                if not aid:
                    continue
                rec = out.setdefault(
                    aid, {"source_id": "", "client_source": "", "source_type": ""},
                )
                sid = str(meta.get("source_id") or "").strip()
                if sid:
                    if rec["source_id"] and rec["source_id"] != sid:
                        log.warning(
                            "artifact %s has conflicting source_id in Chroma (%s vs %s) — keeping first",
                            aid[:12], rec["source_id"], sid,
                        )
                    elif not rec["source_id"]:
                        rec["source_id"] = sid
                if not rec["client_source"]:
                    rec["client_source"] = str(meta.get("client_source") or "").strip()
                if not rec["source_type"]:
                    rec["source_type"] = str(meta.get("source_type") or "").strip()
            seen_here += len(metadatas)
            if len(metadatas) < _CHROMA_PAGE:
                break
            offset += _CHROMA_PAGE
        log.info("Scanned collection %s: %d chunk metadatas", name, seen_here)

    return out


# ---------------------------------------------------------------------------
# Neo4j reads
# ---------------------------------------------------------------------------


def fetch_artifact_chunk_counts(driver: Any) -> dict[str, int]:
    """Return ``{artifact_id: chunk_count}`` for every :Artifact node."""
    out: dict[str, int] = {}
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact) RETURN a.id AS id, coalesce(a.chunk_count, 0) AS cc",
        )
        for record in result:
            aid = record["id"]
            if aid:
                out[aid] = int(record["cc"] or 0)
    return out


def fetch_existing_edges(driver: Any) -> set[tuple[str, str]]:
    """Return the set of existing ``(artifact_id, source_id)`` FROM_SOURCE edges."""
    out: set[tuple[str, str]] = set()
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact)-[:FROM_SOURCE]->(s:Source) "
            "RETURN a.id AS aid, s.id AS sid",
        )
        for record in result:
            aid, sid = record["aid"], record["sid"]
            if aid and sid:
                out.add((aid, sid))
    return out


def recompute_counters_set(driver: Any) -> list[dict[str, Any]]:
    """Recompute total_artifacts / total_chunks from graph truth, in one pass.

    Idempotent SET (not increment). Only touches sources that have at least one
    FROM_SOURCE edge; sources with none keep their existing (0) counters.
    Returns the per-source values that were set.
    """
    cypher = """
        MATCH (a:Artifact)-[:FROM_SOURCE]->(s:Source)
        WITH s, count(a) AS n, sum(coalesce(a.chunk_count, 0)) AS c
        SET s.total_artifacts = n,
            s.total_chunks = c
        RETURN s.id AS id, s.display_name AS display_name, n AS artifacts, c AS chunks
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher).data()]


# ---------------------------------------------------------------------------
# Planning — pure computation over the reads above
# ---------------------------------------------------------------------------


def build_plan(
    chroma_map: dict[str, dict[str, str]],
    chunk_counts: dict[str, int],
    existing_edges: set[tuple[str, str]],
    known_source_ids: set[str],
    current_counters: dict[str, dict[str, Any]],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Compute the backfill plan without writing anything.

    ``chroma_map``       : {artifact_id: {source_id, client_source, source_type}}
    ``chunk_counts``     : {artifact_id: chunk_count} for every :Artifact node
    ``existing_edges``   : current (artifact_id, source_id) FROM_SOURCE edges
    ``known_source_ids`` : ids of real :Source nodes (existence check)
    ``current_counters`` : {source_id: {total_artifacts, total_chunks, ...}}
    ``limit``            : cap on NEW edges created (bounded runs)
    """
    node_ids = set(chunk_counts)

    desired_edges: list[tuple[str, str]] = []          # linkable (aid, sid)
    unresolvable: list[tuple[str, str]] = []           # sid present but no :Source
    chroma_orphans: list[tuple[str, str]] = []         # sid present but no :Artifact node
    no_source_by_combo: dict[tuple[str, str], int] = defaultdict(int)  # nodes w/o source_id
    no_source_count = 0

    for aid, rec in chroma_map.items():
        sid = rec["source_id"]
        if not sid:
            continue
        if aid not in node_ids:
            chroma_orphans.append((aid, sid))
            continue
        if sid in known_source_ids:
            desired_edges.append((aid, sid))
        else:
            unresolvable.append((aid, sid))

    # Artifact nodes with no reconstructable source_id — the m0003 fallback
    # surface. We do NOT fabricate :Source nodes here; we report the
    # (client_source, source_type) combos so an operator can decide.
    for aid in node_ids:
        rec = chroma_map.get(aid)
        if rec is None or not rec.get("source_id"):
            no_source_count += 1
            cs = (rec or {}).get("client_source", "") or "(none)"
            st = (rec or {}).get("source_type", "") or "(none)"
            no_source_by_combo[(cs, st)] += 1

    # New edges to MERGE = desired minus already-present. Deterministic order so
    # --limit takes a stable slice across runs.
    desired_set = set(desired_edges)
    edges_to_create = sorted(desired_set - existing_edges)
    if limit is not None and limit >= 0:
        edges_to_create = edges_to_create[:limit]

    # Projected counters == what the SET-from-graph pass yields AFTER the new
    # edges land: recompute over (existing ∪ newly-created) edges.
    final_edges: dict[str, set[str]] = defaultdict(set)
    for aid, sid in existing_edges:
        final_edges[sid].add(aid)
    for aid, sid in edges_to_create:
        final_edges[sid].add(aid)

    projected: dict[str, dict[str, Any]] = {}
    for sid, aids in final_edges.items():
        n = len(aids)
        c = sum(chunk_counts.get(aid, 0) for aid in aids)
        cur = current_counters.get(sid, {})
        projected[sid] = {
            "display_name": cur.get("display_name", ""),
            "current_artifacts": int(cur.get("total_artifacts") or 0),
            "current_chunks": int(cur.get("total_chunks") or 0),
            "projected_artifacts": n,
            "projected_chunks": c,
        }

    return {
        "edges_to_create": edges_to_create,
        "desired_total": len(desired_set),
        "already_linked": len(desired_set & existing_edges),
        "unresolvable": unresolvable,
        "chroma_orphans": chroma_orphans,
        "no_source_count": no_source_count,
        "no_source_by_combo": dict(no_source_by_combo),
        "projected": projected,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(
    plan: dict[str, Any],
    *,
    sources_scanned: int,
    artifacts_scanned: int,
    chroma_available: bool,
) -> None:
    log.info(
        "Sources scanned: %d | :Artifact nodes: %d | Chroma: %s",
        sources_scanned, artifacts_scanned,
        "available" if chroma_available else "UNAVAILABLE (link phase skipped)",
    )
    log.info(
        "Linkable artifact→source pairs: %d (already linked: %d) | new edges to create: %d",
        plan["desired_total"], plan["already_linked"], len(plan["edges_to_create"]),
    )

    projected = plan["projected"]
    if projected:
        log.info("Per-source counters (current → projected):")
        for sid in sorted(projected):
            p = projected[sid]
            name = p["display_name"] or "(unnamed)"
            log.info(
                "  %s [%s]  artifacts %d→%d  chunks %d→%d",
                sid[:12], name,
                p["current_artifacts"], p["projected_artifacts"],
                p["current_chunks"], p["projected_chunks"],
            )

    unresolvable = plan["unresolvable"]
    if unresolvable:
        log.warning(
            "Cannot link — source_id present but NO matching :Source node (%d artifacts):",
            len(unresolvable),
        )
        for aid, sid in unresolvable[:_SAMPLE_LIMIT]:
            log.warning("  artifact %s → missing source %s", aid[:12], sid[:12])

    chroma_orphans = plan["chroma_orphans"]
    if chroma_orphans:
        log.warning(
            "Cannot link — Chroma chunks reference a source but have NO :Artifact node "
            "(%d orphans):",
            len(chroma_orphans),
        )
        for aid, sid in chroma_orphans[:_SAMPLE_LIMIT]:
            log.warning("  chroma artifact %s → source %s", aid[:12], sid[:12])

    if plan["no_source_count"]:
        log.warning(
            "Cannot link — %d :Artifact nodes have no reconstructable source_id "
            "(m0003 fallback: grouped by client_source / source_type; NOT auto-created):",
            plan["no_source_count"],
        )
        for (cs, st), count in sorted(
            plan["no_source_by_combo"].items(), key=lambda kv: kv[1], reverse=True,
        ):
            log.warning("  client_source=%s source_type=%s → %d artifacts", cs, st, count)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_backfill(driver: Any, chroma: Any, *, apply: bool, limit: int | None) -> int:
    """Execute the backfill (dry-run unless ``apply``). Returns an exit code."""
    from app.db.neo4j.sources import link_artifact, list_sources

    sources = list_sources(driver)
    known_source_ids = {s["id"] for s in sources if s.get("id")}
    current_counters = {s["id"]: s for s in sources if s.get("id")}

    chunk_counts = fetch_artifact_chunk_counts(driver)
    existing_edges = fetch_existing_edges(driver)

    chroma_map: dict[str, dict[str, str]] = {}
    if chroma is not None:
        chroma_map = scan_chroma_source_ids(chroma)

    plan = build_plan(
        chroma_map, chunk_counts, existing_edges, known_source_ids, current_counters,
        limit=limit,
    )
    _print_report(
        plan,
        sources_scanned=len(known_source_ids),
        artifacts_scanned=len(chunk_counts),
        chroma_available=chroma is not None,
    )

    if not apply:
        log.info(
            "DRY-RUN — no changes made. Re-run with --apply to create %d edge(s) and "
            "recompute counters.",
            len(plan["edges_to_create"]),
        )
        return _EXIT_OK

    # --apply: MERGE the new edges (idempotent), then recompute counters from graph.
    linked = 0
    for aid, sid in plan["edges_to_create"]:
        try:
            link_artifact(driver, aid, sid)
            linked += 1
        except Exception as exc:  # noqa: BLE001 — one bad pair must not abort the batch
            log.warning("link_artifact failed for artifact %s → source %s: %s", aid[:12], sid[:12], exc)

    set_rows = recompute_counters_set(driver)
    log.info(
        "APPLIED — created/merged %d FROM_SOURCE edge(s); recomputed counters for %d source(s).",
        linked, len(set_rows),
    )
    for row in sorted(set_rows, key=lambda r: str(r.get("id"))):
        log.info(
            "  set %s [%s]  total_artifacts=%s  total_chunks=%s",
            str(row.get("id"))[:12], row.get("display_name") or "(unnamed)",
            row.get("artifacts"), row.get("chunks"),
        )
    log.info(
        "Note: total_edges and total_artifacts_24h left untouched (CL-1 funds neither; "
        "24h is a rolling window historical backfill must not inflate).",
    )
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "CL-1 source-node backfill: link pre-CL-1 artifacts to their :Source "
            "(reconstructing source_id from Chroma chunk metadata) and recompute "
            "per-source counters from graph truth. Dry-run by default; --apply to write."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="MERGE FROM_SOURCE edges then SET counters from the graph (default: dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of NEW FROM_SOURCE edges created (bounded test runs).",
    )
    args = parser.parse_args(argv)

    _load_dotenv_into_environ()

    driver = _get_neo4j_driver()
    if driver is None:
        log.error("Could not obtain a Neo4j driver — check NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD.")
        return _EXIT_NEO4J_UNAVAILABLE

    chroma = _get_chroma_client()
    if chroma is None:
        log.warning(
            "Proceeding without Chroma: no NEW source links can be reconstructed; "
            "counters will still be recomputed from existing FROM_SOURCE edges under --apply.",
        )

    try:
        return run_backfill(driver, chroma, apply=args.apply, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error over a raw traceback
        log.error("Backfill failed: %s", exc)
        return _EXIT_NEO4J_UNAVAILABLE
    finally:
        try:
            driver.close()
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            log.debug("driver close failed (ignored): %s", exc)


if __name__ == "__main__":
    sys.exit(main())
