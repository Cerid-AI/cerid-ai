#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""One-time purge of pre-existing junk Entity nodes (Phase 1 item 1.5).

``core.agents.entity_extraction.is_junk_entity_name`` (2026-07-13) now
stops NEW junk at extraction time — single characters, doc-path names
("library/email.charset.html"), and pure version tokens ("version-3-6").
It does nothing for junk Entity nodes that were already written to Neo4j
before the gate landed. Those nodes have ``summary IS NULL`` forever
(nothing can enrich "version-3-6"), so the nightly wiki stale sweep
(``app.scheduler._run_wiki_stale_sweep``, ordered by ``mention_count``
over ``summary IS NULL``) re-selects them every night in perpetuity.

A second family churns the wiki-refresh queue the same way without ever
tripping ``is_junk_entity_name``: unknown-typed shouty acronyms
("ALIASES") and codec-alias-shaped names ("euc-jp") that
``app.services.external_apis.wiki_enrichment._passes_adapter_gate``
skips before ever calling the Wikipedia API. This script classifies
against BOTH gates so the purge matches production skip behaviour
exactly (see ``classify_junk_entity``).

Modes
-----
Default (no flags) is a DRY-RUN: prints scanned/junk counts, a
per-class breakdown, the nightly-sweep pressure (junk nodes with
``summary IS NULL``), and up to 30 sample names. Nothing is written.

``--apply`` performs the purge: writes a JSONL backup of every junk
node's full properties + relationship count to
``scripts/out/junk_entities_backup_<timestamp>.jsonl`` (so the purge is
reversible by re-ingest), THEN ``DETACH DELETE``s the nodes in batches.

``--limit N`` caps the number of Entity nodes scanned, for safety/test
runs against a live database.

Usage (from repo root, either form)::

    .venv/bin/python scripts/purge_junk_entities.py              # dry-run
    .venv/bin/python scripts/purge_junk_entities.py --limit 500   # bounded dry-run
    .venv/bin/python scripts/purge_junk_entities.py --apply       # purge for real

Env vars (loaded from repo-root ``.env`` if present, caller-set wins):
    NEO4J_URI       default "bolt://127.0.0.1:7687"
    NEO4J_USER      default "neo4j"
    NEO4J_PASSWORD  required — never logged.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("purge-junk-entities")

# Add src/mcp to sys.path so core.* / app.* import without PYTHONPATH set,
# whether this script is run directly or loaded via importlib in tests.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))

JunkClass = Literal[
    "single_char", "doc_path", "version_token", "shouty_acronym", "codec_alias",
]

DEFAULT_NEO4J_URI = "bolt://127.0.0.1:7687"
DEFAULT_NEO4J_USER = "neo4j"

# UNWIND batch size for the DETACH DELETE pass.
_DELETE_BATCH_SIZE = 200

# Refuse --apply when the junk share of the whole Entity population exceeds
# this fraction — almost certainly a classification bug, not real junk.
_MAX_JUNK_SHARE = 0.60

# Dry-run sample size for eyeballing what would be deleted.
_SAMPLE_LIMIT = 30

_EXIT_OK = 0
_EXIT_NEO4J_UNAVAILABLE = 2
_EXIT_UNSAFE_JUNK_SHARE = 3


# ---------------------------------------------------------------------------
# Host bootstrap (mirrors scripts/k_program_metrics.py)
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

    Lazy ``neo4j`` import so the module stays import-safe (and unit
    testable) without the driver package installed.
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


# ---------------------------------------------------------------------------
# Classification — pure functions, no Neo4j required
# ---------------------------------------------------------------------------


def classify_junk_entity(name: str) -> JunkClass | None:
    """Classify ``name`` as junk, or return None for a keeper.

    Layers the two gates already live in production, in the same order
    they fire there, so the purge selection matches production skip
    behaviour exactly:

    1. ``core.agents.entity_extraction`` extraction-time / wiki-refresh
       gate — single/empty names, doc-path names, pure version tokens.
    2. ``wiki_enrichment._passes_adapter_gate``'s unknown-typed skip
       classes — shouty acronyms and codec-alias-shaped names — which
       only fire when ``infer_entity_type(name)`` resolves to "unknown"
       (the same call ``app.processor.jobs.wiki_refresh`` makes before
       invoking ``enrich()``).
    """
    from app.services.external_apis.wiki_enrichment import (  # noqa: PLC0415
        _is_codec_alias_shaped,
        _is_shouty_single_token,
        infer_entity_type,
    )
    from core.agents.entity_extraction import (  # noqa: PLC0415
        _MIN_ENTITY_NAME_CHARS,
        _is_doc_path_like,
        _is_version_token,
    )

    stripped = name.strip()
    if len(stripped) < _MIN_ENTITY_NAME_CHARS:
        return "single_char"
    if _is_doc_path_like(stripped):
        return "doc_path"
    if _is_version_token(stripped):
        return "version_token"

    if infer_entity_type(stripped) == "unknown":
        if _is_shouty_single_token(stripped):
            return "shouty_acronym"
        if _is_codec_alias_shaped(stripped):
            return "codec_alias"
    return None


def classify_entity_records(records: Iterable[dict]) -> list[dict]:
    """Tag Entity records with their junk class; drop keepers.

    Each input record is the raw query-row shape: ``{"props": {...node
    properties...}, "rel_count": <int>}``. Returns only the junk subset,
    each augmented with ``"_junk_class"``.
    """
    junk: list[dict] = []
    for rec in records:
        props = rec.get("props") or {}
        name = str(props.get("name") or "")
        junk_class = classify_junk_entity(name)
        if junk_class is None:
            continue
        junk.append({**rec, "_junk_class": junk_class})
    return junk


def summarize(all_records: list[dict], junk_records: list[dict]) -> dict:
    """Pure aggregation: counts, per-class breakdown, and the nightly-sweep
    pressure metric — junk nodes with ``summary IS NULL``, the perpetual
    re-sweep set per ``app.scheduler._run_wiki_stale_sweep``.
    """
    total = len(all_records)
    junk_count = len(junk_records)
    by_class: dict[str, int] = {}
    summary_null = 0
    for rec in junk_records:
        cls = str(rec.get("_junk_class") or "unknown")
        by_class[cls] = by_class.get(cls, 0) + 1
        if (rec.get("props") or {}).get("summary") is None:
            summary_null += 1
    return {
        "total_entities": total,
        "junk_count": junk_count,
        "junk_share": (junk_count / total) if total else 0.0,
        "by_class": by_class,
        "summary_null_count": summary_null,
    }


def is_junk_share_unsafe(junk_share: float) -> bool:
    """True when the junk share is implausibly high — likely mis-selection."""
    return junk_share > _MAX_JUNK_SHARE


# ---------------------------------------------------------------------------
# Neo4j I/O
# ---------------------------------------------------------------------------


def _fetch_entity_records(driver: Any, limit: int | None = None) -> list[dict]:
    """Stream all ``(:Entity)`` nodes with their full properties + degree.

    ``rel_count`` (total relationship degree, MENTIONS + any other edge
    type) rides along for the backup record — not used for selection.
    """
    query = (
        "MATCH (e:Entity) "
        "OPTIONAL MATCH (e)-[r]-() "
        "WITH e, count(r) AS rel_count "
        "RETURN properties(e) AS props, rel_count AS rel_count"
    )
    params: dict[str, Any] = {}
    if limit is not None:
        query += " LIMIT $limit"
        params["limit"] = limit

    records: list[dict] = []
    with driver.session() as session:
        result = session.run(query, **params)
        for row in result:
            records.append({"props": dict(row["props"]), "rel_count": int(row["rel_count"])})
    return records


def _chunked(seq: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def delete_junk_entities(
    driver: Any, canonical_ids: list[str], batch_size: int = _DELETE_BATCH_SIZE,
) -> int:
    """DETACH DELETE Entity nodes by canonical_id, batched via UNWIND.

    Returns the number of nodes actually deleted. A canonical_id that no
    longer matches (e.g. deleted between selection and apply) is simply
    not counted — not an error.
    """
    deleted = 0
    with driver.session() as session:
        for batch in _chunked(canonical_ids, batch_size):
            result = session.run(
                "UNWIND $ids AS cid "
                "MATCH (e:Entity {canonical_id: cid}) "
                "DETACH DELETE e "
                "RETURN count(e) AS n",
                ids=batch,
            )
            row = result.single()
            if row is not None:
                deleted += int(row["n"])
    return deleted


def write_backup(junk_records: list[dict], out_path: Path) -> None:
    """JSONL backup: one line per purged node — full properties, degree,
    and assigned junk class — so a mis-selected purge is reversible by
    re-ingest.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for rec in junk_records:
            line = {
                "props": rec.get("props"),
                "rel_count": rec.get("rel_count"),
                "junk_class": rec.get("_junk_class"),
            }
            f.write(json.dumps(line, default=str) + "\n")


def _backup_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _REPO_ROOT / "scripts" / "out" / f"junk_entities_backup_{ts}.jsonl"


# ---------------------------------------------------------------------------
# Reporting + CLI
# ---------------------------------------------------------------------------


def _print_report(summary: dict, junk_records: list[dict]) -> None:
    log.info(
        "Entities scanned: %d | junk: %d (%.1f%%) | keepers: %d",
        summary["total_entities"],
        summary["junk_count"],
        summary["junk_share"] * 100,
        summary["total_entities"] - summary["junk_count"],
    )
    log.info("Junk breakdown by class:")
    for cls in sorted(summary["by_class"]):
        log.info("  %-16s %d", cls, summary["by_class"][cls])
    log.info(
        "Nightly-sweep pressure: %d of %d junk entities have summary IS NULL "
        "(perpetual re-sweep set per app.scheduler._run_wiki_stale_sweep).",
        summary["summary_null_count"],
        summary["junk_count"],
    )
    sample_names = [
        str((rec.get("props") or {}).get("name") or "") for rec in junk_records[:_SAMPLE_LIMIT]
    ]
    if sample_names:
        log.info("Sample junk names (up to %d):", _SAMPLE_LIMIT)
        for name in sample_names:
            log.info("  %r", name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-time purge of pre-existing junk Entity nodes that predate "
            "the extraction-time is_junk_entity_name gate and keep "
            "re-triggering the nightly wiki stale sweep."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write a JSONL backup then DETACH DELETE the junk nodes (default: dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of Entity nodes scanned (safety/testing runs).",
    )
    args = parser.parse_args(argv)

    _load_dotenv_into_environ()
    driver = _get_neo4j_driver()
    if driver is None:
        log.error("Could not obtain a Neo4j driver — check NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD.")
        return _EXIT_NEO4J_UNAVAILABLE

    try:
        records = _fetch_entity_records(driver, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error over a raw traceback
        log.error("Neo4j query failed: %s", exc)
        return _EXIT_NEO4J_UNAVAILABLE

    junk_records = classify_entity_records(records)
    summary = summarize(records, junk_records)
    _print_report(summary, junk_records)

    if not args.apply:
        log.info(
            "DRY-RUN — no changes made. Re-run with --apply to delete %d entities.",
            summary["junk_count"],
        )
        return _EXIT_OK

    if summary["junk_count"] == 0:
        log.info("Nothing to purge.")
        return _EXIT_OK

    if is_junk_share_unsafe(summary["junk_share"]):
        log.error(
            "Refusing --apply: junk share %.1f%% of %d entities exceeds the %.0f%% safety "
            "threshold — likely mis-selection. Investigate the classification before "
            "forcing a re-run.",
            summary["junk_share"] * 100,
            summary["total_entities"],
            _MAX_JUNK_SHARE * 100,
        )
        return _EXIT_UNSAFE_JUNK_SHARE

    backup_path = _backup_path()
    write_backup(junk_records, backup_path)
    log.info("Backup written: %s (%d records)", backup_path, len(junk_records))

    canonical_ids = [
        cid
        for cid in (str((rec.get("props") or {}).get("canonical_id") or "") for rec in junk_records)
        if cid
    ]
    deleted = delete_junk_entities(driver, canonical_ids)

    log.info("DONE — deleted=%d backup=%s", deleted, backup_path)
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
