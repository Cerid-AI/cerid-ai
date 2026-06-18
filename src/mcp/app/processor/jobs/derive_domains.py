# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Derive Entity domain fields: primary_domain, domain_mix, domain_salience, top_tags, primary_subcategory, domains_updated_at.

Algorithm
---------
One read pass over (Artifact)-[:MENTIONS]->(Entity) aggregated per
(entity, domain, sub_category), then folded in Python. Each (entity,
domain) pair gets a **salience** score (Slice 6.1):

  salience = specificity(domain)
           × distinctiveness(domain)
           × quality_mass(entity, domain)
           × recency_decay(latest)

  - specificity:    'general'/'conversations' are uncategorised/ambient,
                    down-weighted to _LOW_SPECIFICITY; every other domain 1.0.
  - distinctiveness: inverse global domain frequency (computed purely in
                    the fold) — 30 mentions of a rare domain outweigh 60
                    generic ones.
  - quality_mass:   sum of artifact quality_score for the pair, falling
                    back to the raw mention count for pre-quality nodes.
  - recency_decay:  2^(-age_days / half_life) against an injected `now`
                    (NOT temporal.recency_score — that reads the wall clock
                    and would break run-to-run determinism).

primary_domain is the salience argmax, with the original deterministic
tie-break preserved for equal salience:

  (1) Highest salience (the mode itself).
  (2) Non-general beats general — 'general' is explicitly "uncategorized
      or cross-domain", so any specific domain wins.
  (3) Most recent max(a.updated_at) among still-tied domains.
  (4) Lexicographic ascending — pure determinism, idempotent across runs.

domain_mix stays a raw integer count map (downstream + endpoint tests treat
it as counts); domain_salience is a separate float map. Both persist sorted
desc. Salience is rounded to _SALIENCE_PRECISION so floats don't drift
across runs (idempotency contract).

top_tags (Slice 6.3) is the entity's top-N controlled-vocabulary tags,
salience-weighted (quality_mass × recency per tag) and vocabulary-filtered so
free-form tags never surface as navigation/sort affordances. Persisted as a
JSON list ordered by weight desc, name asc.

DOMAIN_AFFINITY is explicitly excluded: it covers only 6 of 13 live
domains (neither 'research' nor 'boardroom_foundation' appears in it),
and the 1.4% tie rate doesn't justify machinery without coverage.

Orphan entities (no MENTIONS path, ~32 live) get REMOVE on all derived
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

import config
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.derive_domains")

_WRITE_BATCH = int(os.getenv("DERIVE_DOMAINS_WRITE_BATCH", "500"))
_MAX_ENTITIES = int(os.getenv("DERIVE_DOMAINS_MAX_ENTITIES", "100000"))

_GENERAL_DOMAIN = "general"

# --- Salience tuning (Slice 6.1) — env-overridable like _WRITE_BATCH. -------
# Defensible defaults; tune at eval checkpoint 8.3 per plan decision-authority.
_SALIENCE_HALF_LIFE_DAYS = float(os.getenv("DERIVE_DOMAINS_SALIENCE_HALF_LIFE_DAYS", "30"))
# Ambient/uncategorised domains contribute, but shouldn't define an entity.
_LOW_SPECIFICITY = float(os.getenv("DERIVE_DOMAINS_LOW_SPECIFICITY", "0.25"))
_LOW_SPECIFICITY_DOMAINS = frozenset({"general", "conversations"})
# Decimal places salience is rounded to before persist/compare — the
# idempotency guard (raw floats would drift in the last digits run-to-run).
_SALIENCE_PRECISION = 4

# --- Entity top_tags rollup (Slice 6.3) -------------------------------------
_TOP_TAGS_N = int(os.getenv("DERIVE_DOMAINS_TOP_TAGS_N", "5"))
# Flat set of every controlled-vocabulary tag across all domains. Entity-level
# top_tags are vocabulary-only by construction — free-form stragglers never
# surface as sort/filter affordances (that's the metadata/sorting contract;
# free text stays a search concern, not a navigation one).
_VOCABULARY_TAGS: frozenset[str] = frozenset(
    tag for tags in config.TAG_VOCABULARY.values() for tag in tags
)


def _specificity(domain: str) -> float:
    """Down-weight ambient/uncategorised domains; everything specific is 1.0."""
    return _LOW_SPECIFICITY if domain in _LOW_SPECIFICITY_DOMAINS else 1.0


def _recency_decay(latest: str | None, now: datetime, half_life_days: float) -> float:
    """Exponential recency decay against an injected `now` (pure — no clock read).

    Mirrors core.utils.temporal.recency_score's tz-stripping and
    neutral-0.5-on-unparseable behaviour, but takes `now` as a parameter so
    the fold is deterministic across runs (same now + rows => same salience).
    """
    if not latest or half_life_days <= 0:
        # Neutral 0.5 on missing date OR a misconfigured (zero/negative)
        # half-life — never crash the nightly job or invert decay to growth.
        return 0.5
    try:
        dt = datetime.fromisoformat(latest)
    except (ValueError, TypeError):
        return 0.5
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    ref = now.replace(tzinfo=None) if now.tzinfo is not None else now
    age_days = (ref - dt).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    return 2.0 ** (-age_days / half_life_days)


def _pick_top_tags(weights: dict[str, float]) -> list[str]:
    """Top-N controlled-vocabulary tags for an entity, by salience weight.

    Deterministic: weights are rounded before sort (so float drift can't
    reorder near-ties) and ties break lexicographically. `weights` is already
    vocabulary-filtered by the caller, so this never surfaces free-form tags.
    """
    if not weights:
        return []
    ranked = sorted(
        weights.items(),
        key=lambda kv: (-round(kv[1], _SALIENCE_PRECISION), kv[0]),
    )
    return [tag for tag, _ in ranked[:_TOP_TAGS_N]]


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
        await progress_cb(0.45)

        tag_rows = await asyncio.to_thread(self._fetch_tag_rows, driver)
        await progress_cb(0.55)

        # Fold in Python — derive per-entity distributions + primary fields.
        # `now` is captured once and injected so the fold stays deterministic
        # (recency decay must not read the wall clock — see _recency_decay).
        now = datetime.now(timezone.utc)
        update_rows, orphan_ids = _fold_distributions(
            mention_rows, all_entity_ids, now, tag_rows
        )
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
                 sum(a.quality_score) AS qsum,
                 max(a.updated_at) AS latest
            RETURN
                e.canonical_id AS cid,
                domain,
                sub,
                n,
                qsum,
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

    def _fetch_tag_rows(self, driver: Any) -> list[dict[str, Any]]:
        """Per-(entity, tag) quality + recency aggregates over tagged mentions.

        Drives the salience-weighted Entity.top_tags rollup (6.3). One row per
        (entity, tag) the entity's artifacts carry via :TAGGED_WITH; the fold
        vocabulary-filters and ranks them. Entities with no tags simply produce
        no rows here (empty top_tags downstream)."""
        cypher = f"""
            MATCH (a:Artifact)-[:MENTIONS]->(e:Entity)
            MATCH (a)-[:TAGGED_WITH]->(t:Tag)
            WHERE e.canonical_id IS NOT NULL AND t.name IS NOT NULL
            WITH e.canonical_id AS cid, t.name AS tag,
                 count(DISTINCT a) AS n,
                 sum(a.quality_score) AS qsum,
                 max(a.updated_at) AS latest
            RETURN cid, tag, n, qsum, latest
            LIMIT {_MAX_ENTITIES}
        """
        try:
            with driver.session() as session:
                return list(session.run(cypher).data())
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("derive_domains._fetch_tag_rows", exc)
            return []

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
                e.domain_salience    = r.domain_salience,
                e.top_tags           = r.top_tags,
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
            REMOVE e.primary_domain, e.domain_mix, e.domain_salience, e.top_tags, e.primary_subcategory
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


def _quality_mass(qsum: float, n: int) -> float:
    """Quality-weighted mention mass: sum of artifact quality_score, falling
    back to the raw mention count for pre-quality nodes (qsum NULL/0)."""
    return qsum if qsum > 0 else float(n)


def _fold_distributions(
    mention_rows: list[dict[str, Any]],
    all_entity_ids: set[str],
    now: datetime,
    tag_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fold raw (entity, domain, sub) rows into per-entity update dicts.

    `now` is injected (not read from the clock) so salience is deterministic
    run-to-run. `tag_rows` (per-(entity, tag) aggregates) drive the
    vocabulary-only top_tags rollup; omit them and top_tags is empty. Returns:
        update_rows: {cid, primary_domain, domain_mix, domain_salience,
                      top_tags, primary_subcategory}
        orphan_ids:  entity canonical_ids with no MENTIONS path
    """
    # Pass 0 — global domain frequency (across ALL entities) for distinctiveness.
    global_domain_total: dict[str, int] = defaultdict(int)
    for row in mention_rows:
        domain = str(row.get("domain") or "")
        if domain:
            global_domain_total[domain] += int(row.get("n") or 0)
    total_all = sum(global_domain_total.values())
    num_domains = len(global_domain_total)

    def _distinctiveness(domain: str) -> float:
        """Inverse global frequency, normalised so a uniform corpus => 1.0.
        Rare domains score >1 (a mention there is more telling)."""
        gt = global_domain_total.get(domain, 0)
        if gt <= 0 or total_all <= 0 or num_domains <= 0:
            return 1.0
        return total_all / (num_domains * gt)

    # domain_agg[cid][domain] = {"n": int, "latest": str | None, "qsum": float}
    domain_agg: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"n": 0, "latest": None, "qsum": 0.0})
    )
    # sub_weight[cid][domain][sub] = float — salience-weighted sub counts
    # (quality_mass × recency per row) so a high-quality recent sub outranks
    # many stale low-quality ones, mirroring domain-level salience.
    sub_weight: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )

    mentioned_cids: set[str] = set()

    for row in mention_rows:
        cid = str(row.get("cid") or "")
        domain = str(row.get("domain") or "")
        sub = str(row.get("sub") or "")
        n = int(row.get("n") or 0)
        latest = row.get("latest")
        if latest is not None:
            latest = str(latest)
        qsum_raw = row.get("qsum")
        try:
            qrow = float(qsum_raw) if qsum_raw is not None else 0.0
        except (TypeError, ValueError):
            qrow = 0.0

        if not cid or not domain:
            continue

        mentioned_cids.add(cid)
        d_slot = domain_agg[cid][domain]
        d_slot["n"] += n
        d_slot["qsum"] += qrow
        # Keep max latest across sub-rows for same (entity, domain)
        if latest is not None:
            if d_slot["latest"] is None or latest > d_slot["latest"]:
                d_slot["latest"] = latest

        sub_weight[cid][domain][sub] += _quality_mass(qrow, n) * _recency_decay(
            latest, now, _SALIENCE_HALF_LIFE_DAYS
        )

    # tag_weight[cid][tag] = salience-like weight (quality_mass × recency),
    # vocabulary-filtered up front so non-vocabulary tags never accumulate.
    tag_weight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in tag_rows or []:
        cid = str(row.get("cid") or "")
        tag = str(row.get("tag") or "")
        if not cid or tag not in _VOCABULARY_TAGS:
            continue
        n = int(row.get("n") or 0)
        latest = row.get("latest")
        if latest is not None:
            latest = str(latest)
        qsum_raw = row.get("qsum")
        try:
            qrow = float(qsum_raw) if qsum_raw is not None else 0.0
        except (TypeError, ValueError):
            qrow = 0.0
        tag_weight[cid][tag] += _quality_mass(qrow, n) * _recency_decay(
            latest, now, _SALIENCE_HALF_LIFE_DAYS
        )

    # Orphans: entities that exist but have no MENTIONS path
    orphan_ids = sorted(all_entity_ids - mentioned_cids)

    update_rows: list[dict[str, Any]] = []
    for cid in mentioned_cids:
        d_map = domain_agg[cid]

        # Per-(entity, domain) salience, and a slot enriched with it for the picker.
        salience_map: dict[str, float] = {}
        picker_map: dict[str, dict[str, Any]] = {}
        for domain, info in d_map.items():
            mass = _quality_mass(info["qsum"], info["n"])
            sal = round(
                _specificity(domain)
                * _distinctiveness(domain)
                * mass
                * _recency_decay(info["latest"], now, _SALIENCE_HALF_LIFE_DAYS),
                _SALIENCE_PRECISION,
            )
            salience_map[domain] = sal
            picker_map[domain] = {"n": info["n"], "latest": info["latest"], "salience": sal}

        # domain_mix stays raw integer counts (downstream contract); salience
        # rides in its own float map. Both sorted desc, then name for stability.
        domain_mix: dict[str, int] = {d: info["n"] for d, info in d_map.items()}

        primary_domain = _pick_primary_domain(picker_map)
        primary_subcategory = _pick_primary_subcategory(sub_weight[cid].get(primary_domain, {}))
        top_tags = _pick_top_tags(tag_weight.get(cid, {}))

        update_rows.append({
            "cid": cid,
            "primary_domain": primary_domain,
            "domain_mix": json.dumps(
                dict(sorted(domain_mix.items(), key=lambda kv: (-kv[1], kv[0])))
            ),
            "domain_salience": json.dumps(
                dict(sorted(salience_map.items(), key=lambda kv: (-kv[1], kv[0])))
            ),
            "top_tags": json.dumps(top_tags),
            "primary_subcategory": primary_subcategory,
        })

    return update_rows, orphan_ids


def _pick_primary_domain(d_map: dict[str, dict[str, Any]]) -> str:
    """4-rung deterministic tie-break over a per-domain {n, latest, salience} map.

    Rung 1: highest salience (quality- and recency-weighted, distinctiveness-scaled).
    Rung 2: non-general beats general.
    Rung 3: latest max(updated_at) among tied.
    Rung 4: lexicographic ascending.
    """
    if not d_map:
        return _GENERAL_DOMAIN  # shouldn't happen — caller guards

    # Rung 1: find max salience (rounded upstream, so equality is stable)
    max_s = max(info["salience"] for info in d_map.values())
    candidates = [d for d, info in d_map.items() if info["salience"] == max_s]

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


def _pick_primary_subcategory(sub_counts: dict[str, float]) -> str | None:
    """Salience-weighted mode sub_category for a single domain's mentions.

    `sub_counts` carries quality-and-recency weighted mass per sub (see
    sub_weight in the fold), so a recent high-quality sub outranks many
    stale low-quality ones. Returns None when the winner is the default
    sub_category ('general', seeded from taxonomy.py; all-default = no
    signal, so store null rather than noise).
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
