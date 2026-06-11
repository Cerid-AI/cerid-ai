# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Derive Entity.primary_domain, domain_mix, primary_subcategory, domains_updated_at.

Algorithm
---------
One read pass over (Artifact)-[:MENTIONS]->(Entity) aggregated per
(entity, domain, sub_category), then folded in Python with a
deterministic 4-rung tie-break:

  (1) Highest distinct-artifact count (the mode itself).
  (2) Non-general beats general — 'general' is explicitly "uncategorized
      or cross-domain", so any specific domain wins.
  (3) Most recent max(a.updated_at) among still-tied domains.
  (4) Lexicographic ascending — pure determinism, idempotent across runs.

DOMAIN_AFFINITY is explicitly excluded: it covers only 6 of 13 live
domains (neither 'research' nor 'boardroom_foundation' appears in it),
and the 1.4% tie rate doesn't justify machinery without coverage.

Orphan entities (no MENTIONS path, ~32 live) get REMOVE on all four
fields — honest absence, never coerced to 'general'.

Cost on live data: <2 s end-to-end (tens-of-ms read, negligible fold,
7 write batches of 500 for 3,313 entities).
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.derive_domains")

_WRITE_BATCH = int(os.getenv("DERIVE_DOMAINS_WRITE_BATCH", "500"))
_MAX_ENTITIES = int(os.getenv("DERIVE_DOMAINS_MAX_ENTITIES", "100000"))

_GENERAL_DOMAIN = "general"


class DeriveDomainsJob(BaseJob):
    """Nightly job: derive domain fields on every Entity node.

    Idempotent: each run is a full-snapshot overwrite — deterministic
    tie-breaks make repeated runs converge to identical writes.
    """

    job_type = "derive_domains"

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="cpu/cypher",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        import asyncio  # noqa: PLC0415

        await progress_cb(0.0)
        from app.deps import get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        if driver is None:
            logger.warning("derive_domains: neo4j unavailable, skipping")
            return JobResult(
                job_id=f"derive_domains:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "skipped", "reason": "neo4j unavailable"},
            )

        await progress_cb(0.1)
        mention_rows = await asyncio.to_thread(self._fetch_mention_rows, driver)
        await progress_cb(0.4)

        all_entity_ids = await asyncio.to_thread(self._fetch_all_entity_ids, driver)
        await progress_cb(0.5)

        # Fold in Python — derive per-entity distributions + primary fields
        update_rows, orphan_ids = _fold_distributions(mention_rows, all_entity_ids)
        await progress_cb(0.7)

        written = await asyncio.to_thread(self._write_updates, driver, update_rows)
        await progress_cb(0.85)
        removed = await asyncio.to_thread(self._write_orphan_removes, driver, orphan_ids)
        await progress_cb(1.0)

        logger.info(
            "derive_domains.done written=%d orphans_cleared=%d",
            written,
            removed,
        )
        return JobResult(
            job_id=f"derive_domains:{self._tenant_id}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "written": written,
                "orphans_cleared": removed,
            },
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def _fetch_mention_rows(self, driver: Any) -> list[dict[str, Any]]:
        """One indexed read pass: per-(entity, domain, sub_category) distinct counts."""
        cypher = f"""
            MATCH (a:Artifact)-[:MENTIONS]->(e:Entity)
            WHERE e.canonical_id IS NOT NULL
            WITH e, a.domain AS domain, a.sub_category AS sub,
                 count(DISTINCT a) AS n,
                 max(a.updated_at) AS latest
            RETURN
                e.canonical_id AS cid,
                domain,
                sub,
                n,
                latest
            LIMIT {_MAX_ENTITIES}
        """
        try:
            with driver.session() as session:
                return list(session.run(cypher).data())
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("derive_domains._fetch_mention_rows", exc)
            return []

    def _fetch_all_entity_ids(self, driver: Any) -> set[str]:
        """Canonical IDs of every Entity node (to identify orphans)."""
        cypher = """
            MATCH (e:Entity)
            WHERE e.canonical_id IS NOT NULL
            RETURN e.canonical_id AS cid
        """
        try:
            with driver.session() as session:
                rows = list(session.run(cypher).data())
            return {str(r["cid"]) for r in rows if r.get("cid")}
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("derive_domains._fetch_all_entity_ids", exc)
            return set()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def _write_updates(self, driver: Any, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        cypher = """
            UNWIND $rows AS r
            MATCH (e:Entity {canonical_id: r.cid})
            SET e.primary_domain     = r.primary_domain,
                e.domain_mix         = r.domain_mix,
                e.primary_subcategory = r.primary_subcategory,
                e.domains_updated_at = $now
        """
        written = 0
        try:
            with driver.session() as session:
                for i in range(0, len(rows), _WRITE_BATCH):
                    batch = rows[i : i + _WRITE_BATCH]
                    session.run(cypher, rows=batch, now=now)
                    written += len(batch)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("derive_domains._write_updates", exc)
        return written

    def _write_orphan_removes(self, driver: Any, orphan_ids: list[str]) -> int:
        if not orphan_ids:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        cypher = """
            UNWIND $ids AS cid
            MATCH (e:Entity {canonical_id: cid})
            REMOVE e.primary_domain, e.domain_mix, e.primary_subcategory
            SET e.domains_updated_at = $now
        """
        removed = 0
        try:
            with driver.session() as session:
                for i in range(0, len(orphan_ids), _WRITE_BATCH):
                    batch = orphan_ids[i : i + _WRITE_BATCH]
                    session.run(cypher, ids=batch, now=now)
                    removed += len(batch)
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("derive_domains._write_orphan_removes", exc)
        return removed


# ---------------------------------------------------------------------------
# Pure fold — no I/O, fully unit-testable
# ---------------------------------------------------------------------------


def _fold_distributions(
    mention_rows: list[dict[str, Any]],
    all_entity_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fold raw (entity, domain, sub) rows into per-entity update dicts.

    Returns:
        update_rows: list of {cid, primary_domain, domain_mix, primary_subcategory}
        orphan_ids:  entity canonical_ids with no MENTIONS path
    """
    # domain_agg[cid][domain] = {"n": int, "latest": str | None}
    domain_agg: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {"n": 0, "latest": None}))
    # sub_agg[cid][domain][sub] = int  (for primary_subcategory derivation)
    sub_agg: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    mentioned_cids: set[str] = set()

    for row in mention_rows:
        cid = str(row.get("cid") or "")
        domain = str(row.get("domain") or "")
        sub = str(row.get("sub") or "")
        n = int(row.get("n") or 0)
        latest = row.get("latest")
        if latest is not None:
            latest = str(latest)

        if not cid or not domain:
            continue

        mentioned_cids.add(cid)
        d_slot = domain_agg[cid][domain]
        d_slot["n"] += n
        # Keep max latest across sub-rows for same (entity, domain)
        if latest is not None:
            if d_slot["latest"] is None or latest > d_slot["latest"]:
                d_slot["latest"] = latest

        sub_agg[cid][domain][sub] += n

    # Orphans: entities that exist but have no MENTIONS path
    orphan_ids = sorted(all_entity_ids - mentioned_cids)

    update_rows: list[dict[str, Any]] = []
    for cid in mentioned_cids:
        d_map = domain_agg[cid]

        # Build domain_mix (sorted desc by count, then name for stability)
        domain_mix: dict[str, int] = {d: info["n"] for d, info in d_map.items()}

        primary_domain = _pick_primary_domain(d_map)
        primary_subcategory = _pick_primary_subcategory(sub_agg[cid].get(primary_domain, {}))

        update_rows.append({
            "cid": cid,
            "primary_domain": primary_domain,
            "domain_mix": json.dumps(
                dict(sorted(domain_mix.items(), key=lambda kv: (-kv[1], kv[0])))
            ),
            "primary_subcategory": primary_subcategory,
        })

    return update_rows, orphan_ids


def _pick_primary_domain(d_map: dict[str, dict[str, Any]]) -> str:
    """4-rung deterministic tie-break over a per-domain {n, latest} map.

    Rung 1: highest distinct-artifact count.
    Rung 2: non-general beats general.
    Rung 3: latest max(updated_at) among tied.
    Rung 4: lexicographic ascending.
    """
    if not d_map:
        return _GENERAL_DOMAIN  # shouldn't happen — caller guards

    # Rung 1: find max count
    max_n = max(info["n"] for info in d_map.values())
    candidates = [d for d, info in d_map.items() if info["n"] == max_n]

    if len(candidates) == 1:
        return candidates[0]

    # Rung 2: prefer non-general
    non_general = [d for d in candidates if d != _GENERAL_DOMAIN]
    if len(non_general) == 1:
        return non_general[0]
    if non_general:
        candidates = non_general

    if len(candidates) == 1:
        return candidates[0]

    # Rung 3: most recent max(updated_at) — treat None as "" (sorts last)
    def _latest(domain: str) -> str:
        return d_map[domain].get("latest") or ""

    max_latest = max(_latest(d) for d in candidates)
    candidates = [d for d in candidates if _latest(d) == max_latest]

    if len(candidates) == 1:
        return candidates[0]

    # Rung 4: lexicographic ascending
    return sorted(candidates)[0]


def _pick_primary_subcategory(sub_counts: dict[str, int]) -> str | None:
    """Mode sub_category for a single domain's artifact mentions.

    Returns None when every artifact carries the default sub_category
    (the ingest default is 'general', seeded from taxonomy.py; all-default
    = no signal, so store null rather than noise).
    """
    if not sub_counts:
        return None

    # The default sub_category written at ingest (taxonomy.py default)
    _DEFAULT_SUB = "general"

    max_count = max(sub_counts.values())
    top_subs = sorted(
        [s for s, c in sub_counts.items() if c == max_count]
    )  # lexicographic tiebreak within sub-mode
    winner = top_subs[0]

    # Null if the winner is the default — no signal
    return None if winner == _DEFAULT_SUB else winner
