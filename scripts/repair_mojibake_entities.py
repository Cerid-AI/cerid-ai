#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Repair Entity nodes whose names carry UTF-8/latin-1 mojibake.

The Apple Mail connector's quoted-printable decode mangled every
non-ASCII character ("Guardian Tigerâ€™s Eye…" — sf-3, 2026-08-13
findings). The decode is fixed at the root in
``packages/desktop/src/main/connectors/apple_mail.ts``; this script
repairs the entities that were already written, using
``core.utils.mojibake.fix_mojibake`` (an exact reversal, not a guess).

Per affected entity the plan is one of:

* ``rename``          — fixed name slugs to the SAME canonical_id, or to a
                        new canonical_id no other node holds: update the
                        node's ``name`` (and ``canonical_id`` when it moved).
* ``merge``           — a correctly-named twin already exists under the fixed
                        canonical_id: fold the mojibake node into it via
                        ``app.db.neo4j.entity.merge_entities`` (reversible,
                        tombstoned).
* ``unfixable``       — the signature is present but reversal failed; reported
                        for manual review, never touched.

Modes
-----
Default is a DRY-RUN report. ``--apply`` writes a JSONL backup of the
affected nodes to ``scripts/out/`` first, then repairs.

Usage (from repo root)::

    .venv/bin/python scripts/repair_mojibake_entities.py           # dry-run
    .venv/bin/python scripts/repair_mojibake_entities.py --apply   # repair

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
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("repair-mojibake-entities")

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "mcp"))

DEFAULT_NEO4J_URI = "bolt://127.0.0.1:7687"
DEFAULT_NEO4J_USER = "neo4j"

_EXIT_OK = 0
_EXIT_NEO4J_UNAVAILABLE = 2


# ---------------------------------------------------------------------------
# Host bootstrap (mirrors scripts/purge_junk_entities.py)
# ---------------------------------------------------------------------------


def _load_dotenv_into_environ() -> None:
    """Best-effort load of repo-root .env for host runs; caller-set wins."""
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
# Planning — pure functions, no Neo4j required
# ---------------------------------------------------------------------------


def plan_repairs(records: list[dict]) -> list[dict]:
    """Build the repair plan for entity records with mojibake names.

    Each input record is ``{"props": {...node properties...}}``. Returns
    one plan entry per affected node:
    ``{"canonical_id", "name", "fixed_name", "new_canonical_id", "action"}``
    where ``action`` is ``rename`` / ``rename_or_merge`` / ``unfixable``.
    ``rename_or_merge`` is resolved at apply time by whether the fixed
    canonical_id already belongs to another node.
    """
    from core.agents.entity_extraction import canonical_id as slug_canonical
    from core.utils.mojibake import fix_mojibake, looks_like_mojibake

    plan: list[dict] = []
    for rec in records:
        props = rec.get("props") or {}
        name = str(props.get("name") or "")
        cid = str(props.get("canonical_id") or "")
        if not name or not cid or not looks_like_mojibake(name):
            continue
        fixed = fix_mojibake(name)
        if fixed == name:
            plan.append({
                "canonical_id": cid, "name": name, "fixed_name": name,
                "new_canonical_id": cid, "action": "unfixable",
            })
            continue
        ent_type = str(props.get("entity_type") or "OTHER")
        new_cid = slug_canonical(fixed, ent_type)
        plan.append({
            "canonical_id": cid,
            "name": name,
            "fixed_name": fixed,
            "new_canonical_id": new_cid,
            "action": "rename" if new_cid == cid else "rename_or_merge",
        })
    return plan


# ---------------------------------------------------------------------------
# Neo4j I/O
# ---------------------------------------------------------------------------


def _fetch_entity_records(driver: Any) -> list[dict]:
    with driver.session() as session:
        result = session.run("MATCH (e:Entity) RETURN properties(e) AS props")
        return [{"props": dict(row["props"])} for row in result]


def _entity_exists(driver: Any, canonical_id: str) -> bool:
    with driver.session() as session:
        row = session.run(
            "MATCH (e:Entity {canonical_id: $cid}) RETURN count(e) AS n",
            cid=canonical_id,
        ).single()
        return bool(row and int(row["n"]) > 0)


def apply_plan(driver: Any, plan: list[dict]) -> dict[str, int]:
    """Execute the repair plan. Returns per-outcome counts."""
    from app.db.neo4j.entity import merge_entities

    counts = {"renamed": 0, "merged": 0, "unfixable": 0, "errors": 0}
    for entry in plan:
        action = entry["action"]
        if action == "unfixable":
            counts["unfixable"] += 1
            continue
        try:
            if (
                action == "rename_or_merge"
                and _entity_exists(driver, entry["new_canonical_id"])
            ):
                merge_entities(
                    driver,
                    survivor_id=entry["new_canonical_id"],
                    loser_ids=[entry["canonical_id"]],
                    merge_method="mojibake_repair",
                )
                counts["merged"] += 1
                continue
            with driver.session() as session:
                session.run(
                    "MATCH (e:Entity {canonical_id: $cid}) "
                    "SET e.name = $fixed, e.canonical_id = $new_cid",
                    cid=entry["canonical_id"],
                    fixed=entry["fixed_name"],
                    new_cid=entry["new_canonical_id"],
                )
            counts["renamed"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad node must not stop the repair
            log.error("repair failed for %r: %s", entry["canonical_id"], exc)
            counts["errors"] += 1
    return counts


def write_backup(plan: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for entry in plan:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


def _backup_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _REPO_ROOT / "scripts" / "out" / f"mojibake_entities_backup_{ts}.jsonl"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair Entity names mangled by the mail decode defect.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write a JSONL backup then repair (default: dry-run report).",
    )
    args = parser.parse_args(argv)

    _load_dotenv_into_environ()
    driver = _get_neo4j_driver()
    if driver is None:
        return _EXIT_NEO4J_UNAVAILABLE

    try:
        records = _fetch_entity_records(driver)
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        log.error("Neo4j query failed: %s", exc)
        return _EXIT_NEO4J_UNAVAILABLE

    plan = plan_repairs(records)
    log.info("Entities scanned: %d | mojibake: %d", len(records), len(plan))
    for entry in plan:
        log.info(
            "  %-16s %r -> %r", entry["action"], entry["name"], entry["fixed_name"],
        )

    if not args.apply:
        log.info("DRY-RUN — no changes made. Re-run with --apply to repair.")
        return _EXIT_OK
    if not plan:
        log.info("Nothing to repair.")
        return _EXIT_OK

    backup_path = _backup_path()
    write_backup(plan, backup_path)
    log.info("Backup written: %s (%d records)", backup_path, len(plan))

    counts = apply_plan(driver, plan)
    log.info("DONE — %s", counts)
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
