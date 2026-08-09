#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Resumable merge-migration for already-stored duplicate (:Entity) nodes.

Task 2.3 — collapses existing Entity nodes that resolve to the same new
canonical id (via ``resolve_canonical`` Tier A + B only) into a single
surviving node.  Re-points ``MENTIONS``, ``CO_MENTIONED``, and
``IN_COMMUNITY`` edges onto the survivor; sums ``mention_count``; keeps
the highest-confidence (or longest) surface-form ``name``.

Task 2.5 — wires Tier C (embedding nearest-canonical) into the grouping
pass.  When ``embed_fn`` is provided to ``run_merge`` AND
``settings.ENTITY_RESOLUTION_EMBED`` is True, ``_group_by_canonical_tier_c``
is used instead of the plain A+B grouper.  Tier C is incremental/online:
the ``existing`` index grows as entities are resolved, so later entities
can merge toward canonicals established earlier in the same scan.

Safe default: **dry-run unless ``--apply`` is passed.**  The script will
print the merge plan but make no writes unless explicitly opted in.

Resumable: the set of already-merged cluster keys is persisted to
``.cerid-state/merge_entity_aliases.json`` — exactly the same checkpoint
pattern as ``backfill_entities.py``.  Pass ``--reset`` to start fresh.

APOC decision: we do NOT rely on ``apoc.refactor.mergeNodes``.  APOC is
not guaranteed in all deployment environments and its merge semantics for
list properties differ subtly from what we need (explicit weight summation
for ``mention_count``, highest-confidence ``name`` selection).  Instead
we use a hand-rolled Cypher sequence — one transaction per cluster — that
is explicit, auditable, and has no APOC dependency.  The Cypher is
compact enough that the added verbosity is worth the portability.

Usage (inside the Docker MCP container):

    # Preview what would be merged (no writes):
    python -m scripts.merge_entity_aliases

    # Apply the merges:
    python -m scripts.merge_entity_aliases --apply

    # Apply only a pilot batch of 50 clusters:
    python -m scripts.merge_entity_aliases --apply --limit 50

    # Reset checkpoint and re-scan from scratch:
    python -m scripts.merge_entity_aliases --reset --apply

    # Apply with Tier C embedding merge enabled (set env first):
    # ENTITY_RESOLUTION_EMBED=true python -m scripts.merge_entity_aliases --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("entity-alias-merge")

CHECKPOINT_PATH = Path(".cerid-state/merge_entity_aliases.json")

# ---------------------------------------------------------------------------
# Checkpoint helpers (mirrors backfill_entities.py pattern)
# ---------------------------------------------------------------------------


def _load_checkpoint() -> set[str]:
    if not CHECKPOINT_PATH.exists():
        return set()
    try:
        return set(json.loads(CHECKPOINT_PATH.read_text()).get("merged", []))
    except Exception:  # noqa: BLE001 — corrupted checkpoint → start fresh
        logger.warning("Checkpoint at %s unreadable — starting fresh", CHECKPOINT_PATH)
        return set()


def _save_checkpoint(merged: set[str]) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps({"merged": sorted(merged)}, indent=2))


# ---------------------------------------------------------------------------
# Entity scanning
# ---------------------------------------------------------------------------


def _fetch_all_entities(driver: Any) -> list[dict[str, Any]]:
    """Return all (:Entity) rows from Neo4j."""
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (e:Entity)
            RETURN e.canonical_id AS canonical_id,
                   e.name AS name,
                   e.entity_type AS entity_type,
                   coalesce(e.mention_count, 1) AS mention_count,
                   e.confidence AS confidence
            ORDER BY e.canonical_id
            """
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_by_canonical(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group entity rows by the canonical id returned by resolve_canonical.

    Tiers A + B only (embed=None) — structural string merge, not semantic.
    """
    from core.agents.entity_resolution import resolve_canonical

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        canonical_id = resolve_canonical(row["name"], row["entity_type"])
        groups.setdefault(canonical_id, []).append(row)
    return groups


def _group_by_canonical_tier_c(
    rows: list[dict[str, Any]],
    embed: Callable[[str], list[float]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Group entity rows using Tiers A + B + C (embedding nearest-canonical).

    When ``embed`` is not None AND ``settings.ENTITY_RESOLUTION_EMBED`` is
    True, Tier C is active: the ``existing`` index grows incrementally as
    entities are processed.  Each resolved canonical's surface name is added
    to ``existing[entity_type]`` so subsequent entities can merge toward it.

    Resilience contract
    -------------------
    - If the embed callable raises for a specific name, that entity falls back
      to A+B for that call (the exception is logged via log_swallowed_error;
      the migration continues).
    - Embedding calls are cached by name within this run — no name is embedded
      more than once.
    - Cross-type merge is impossible by construction: ``existing`` is keyed by
      entity_type, and ``resolve_canonical`` enforces type isolation in Tier C.
    - If embed=None or the flag is off, this function is identical to
      ``_group_by_canonical`` (A+B only).

    Parameters
    ----------
    rows:
        Entity rows from Neo4j (list of dicts with name, entity_type, ...).
    embed:
        Optional callable ``(name: str) -> list[float]``.  None → A+B only.
    """
    import config.settings as _settings
    from core.agents.entity_resolution import resolve_canonical
    from core.utils.swallowed import log_swallowed_error

    use_tier_c = embed is not None and _settings.ENTITY_RESOLUTION_EMBED

    # existing[entity_type] = list of canonical surface names already assigned.
    # This is the exact shape resolve_canonical's Tier C expects.
    existing: dict[str, list[str]] = {}

    # embed_cache[name] = vector — avoids re-embedding the same surface form.
    embed_cache: dict[str, list[float]] = {}

    def _safe_embed(name: str) -> list[float] | None:
        """Call embed(name) with caching and error containment."""
        if name in embed_cache:
            return embed_cache[name]
        assert embed is not None
        try:
            vec = embed(name)
            embed_cache[name] = vec
            return vec
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "scripts.merge_entity_aliases._group_by_canonical_tier_c.embed",
                exc,
                context={"entity_name": name},
            )
            return None

    groups: dict[str, list[dict[str, Any]]] = {}

    # Track which canonical_ids have already been registered in `existing`
    # so each canonical group adds its representative name only once.
    registered_canonical_ids: set[str] = set()

    def _cached_embed_fn(n: str) -> list[float]:
        """Embed with cache; raises on error (caller handles)."""
        if n in embed_cache:
            return embed_cache[n]
        assert embed is not None
        result = embed(n)
        embed_cache[n] = result
        return result

    for row in rows:
        name = row["name"]
        entity_type = row["entity_type"]

        if use_tier_c:
            # Attempt Tier C resolution: embed(name) must succeed to enable it.
            vec = _safe_embed(name)
            if vec is not None:
                try:
                    canonical_id = resolve_canonical(
                        name,
                        entity_type,
                        embed=_cached_embed_fn,
                        existing=existing,
                    )
                except Exception as exc:  # noqa: BLE001
                    log_swallowed_error(
                        "scripts.merge_entity_aliases.tier_c",
                        exc,
                        context={"entity_name": name},
                    )
                    # Candidate-side embed raised inside _tier_c → A+B fallback
                    canonical_id = resolve_canonical(name, entity_type)
            else:
                # embed failed for this name → A+B fallback
                canonical_id = resolve_canonical(name, entity_type)
        else:
            canonical_id = resolve_canonical(name, entity_type)

        groups.setdefault(canonical_id, []).append(row)

        # Register the canonical surface form so future entities can merge
        # toward this canonical.  Only the first entity establishing a
        # canonical_id registers a name; subsequent merges into that same
        # canonical ID don't add duplicates.
        # Names here are already in embed_cache; the try/except around
        # resolve_canonical is the backstop if that ever changes.
        if canonical_id not in registered_canonical_ids:
            registered_canonical_ids.add(canonical_id)
            existing.setdefault(entity_type, []).append(name)

    return groups


# ---------------------------------------------------------------------------
# Survivor selection
# ---------------------------------------------------------------------------


def _pick_survivor_name(rows: list[dict[str, Any]]) -> str:
    """Return the best surface-form name for the surviving node.

    Priority:
    1. Row with the highest ``confidence`` value.
    2. Tiebreak: longest name string (more complete ≈ better).
    """
    with_confidence = [r for r in rows if r.get("confidence") is not None]
    if with_confidence:
        best = max(with_confidence, key=lambda r: float(r["confidence"]))
        return str(best["name"])
    # No confidence info — pick longest
    return str(max(rows, key=lambda r: len(str(r["name"])))["name"])


# ---------------------------------------------------------------------------
# Hand-rolled merge Cypher (no APOC required)
#
# Merge strategy per cluster:
#   1. MATCH the survivor (by resolved canonical_id) or CREATE it if it
#      doesn't exist yet (e.g. the cluster key differs from all member ids).
#   2. For each non-survivor ("loser") in the cluster:
#      a. Re-point every MENTIONS edge: CREATE the survivor version, DELETE
#         the loser version (MERGE on (artifact, survivor) deduplicates).
#      b. Re-point CO_MENTIONED edges (both directions).
#      c. Re-point IN_COMMUNITY edges.
#      d. Sum the loser's mention_count into the survivor.
#      e. DELETE the loser node.
# ---------------------------------------------------------------------------

_CYPHER_REPOINT_MENTIONS = """\
MATCH (loser:Entity {canonical_id: $loser_id})
MATCH (survivor:Entity {canonical_id: $survivor_id})
OPTIONAL MATCH (a:Artifact)-[m_old:MENTIONS]->(loser)
WITH loser, survivor, collect({artifact: a, rel: m_old}) AS old_rels
UNWIND (CASE WHEN size(old_rels) = 0 THEN [null] ELSE old_rels END) AS pair
WITH loser, survivor, pair, pair.artifact AS art, pair.rel AS oldrel
FOREACH (_ IN CASE WHEN art IS NULL THEN [] ELSE [1] END |
    MERGE (art)-[m_new:MENTIONS]->(survivor)
    ON CREATE SET
        m_new.confidence = oldrel.confidence,
        m_new.chunk_ids  = oldrel.chunk_ids,
        m_new.created_at = oldrel.created_at
    DELETE oldrel
)
WITH DISTINCT loser, survivor
SET survivor.mention_count = coalesce(survivor.mention_count, 0)
                           + coalesce(loser.mention_count, 0)
"""

_CYPHER_REPOINT_CO_MENTIONED_OUT = """\
MATCH (loser:Entity {canonical_id: $loser_id})
MATCH (survivor:Entity {canonical_id: $survivor_id})
OPTIONAL MATCH (loser)-[co_out:CO_MENTIONED]->(other:Entity)
WHERE other.canonical_id <> $survivor_id
WITH survivor, collect({other: other, rel: co_out}) AS out_rels
UNWIND (CASE WHEN size(out_rels) = 0 THEN [null] ELSE out_rels END) AS pair
WITH survivor, pair, pair.other AS nbr, pair.rel AS oldrel
FOREACH (_ IN CASE WHEN nbr IS NULL THEN [] ELSE [1] END |
    MERGE (survivor)-[r_new:CO_MENTIONED]->(nbr)
    ON CREATE SET r_new.weight = oldrel.weight
    ON MATCH SET  r_new.weight = r_new.weight + coalesce(oldrel.weight, 0)
    DELETE oldrel
)
"""

_CYPHER_REPOINT_CO_MENTIONED_IN = """\
MATCH (loser:Entity {canonical_id: $loser_id})
MATCH (survivor:Entity {canonical_id: $survivor_id})
OPTIONAL MATCH (other2:Entity)-[co_in:CO_MENTIONED]->(loser)
WHERE other2.canonical_id <> $survivor_id
WITH survivor, collect({other: other2, rel: co_in}) AS in_rels
UNWIND (CASE WHEN size(in_rels) = 0 THEN [null] ELSE in_rels END) AS pair
WITH survivor, pair, pair.other AS nbr, pair.rel AS oldrel
FOREACH (_ IN CASE WHEN nbr IS NULL THEN [] ELSE [1] END |
    MERGE (nbr)-[r_new:CO_MENTIONED]->(survivor)
    ON CREATE SET r_new.weight = oldrel.weight
    ON MATCH SET  r_new.weight = r_new.weight + coalesce(oldrel.weight, 0)
    DELETE oldrel
)
"""

_CYPHER_REPOINT_IN_COMMUNITY = """\
MATCH (loser:Entity {canonical_id: $loser_id})
MATCH (survivor:Entity {canonical_id: $survivor_id})
OPTIONAL MATCH (loser)-[ic_old:IN_COMMUNITY]->(c:Community)
WITH survivor, collect({community: c, rel: ic_old}) AS ic_rels
UNWIND (CASE WHEN size(ic_rels) = 0 THEN [null] ELSE ic_rels END) AS pair
WITH survivor, pair, pair.community AS comm, pair.rel AS oldrel
FOREACH (_ IN CASE WHEN comm IS NULL THEN [] ELSE [1] END |
    MERGE (survivor)-[:IN_COMMUNITY]->(comm)
    DELETE oldrel
)
"""

_CYPHER_DELETE_LOSER = """\
MATCH (loser:Entity {canonical_id: $loser_id})
DETACH DELETE loser
"""

_CYPHER_UPSERT_SURVIVOR = """\
MERGE (e:Entity {canonical_id: $canonical_id})
ON CREATE SET
    e.name         = $name,
    e.entity_type  = $entity_type,
    e.mention_count = 0,
    e.created_at   = $now
ON MATCH SET
    e.name         = CASE WHEN size($name) > size(coalesce(e.name, '')) THEN $name ELSE e.name END,
    e.updated_at   = $now
"""


def _merge_cluster(
    driver: Any,
    survivor_id: str,
    survivor_name: str,
    entity_type: str,
    members: list[dict[str, Any]],
) -> None:
    """Collapse all members → survivor in one logical unit (sequential txns).

    Neo4j Community Edition doesn't support multi-statement transactions via
    the Python driver, so we issue each Cypher as a separate session.run()
    call within one session context.  The sequence is safe to re-run
    (MERGE/DELETE is idempotent if a loser was already deleted).
    """
    from core.utils.time import utcnow_iso

    now = utcnow_iso()
    losers = [m for m in members if m["canonical_id"] != survivor_id]

    with driver.session() as session:
        # Ensure survivor node exists (may not if the canonical_id differs
        # from every member's stored id — e.g. "elon-r-musk" normalises to
        # "elon-musk" which already exists, so this is a no-op MERGE).
        session.run(
            _CYPHER_UPSERT_SURVIVOR,
            canonical_id=survivor_id,
            name=survivor_name,
            entity_type=entity_type,
            now=now,
        )
        for loser in losers:
            loser_id = loser["canonical_id"]
            session.run(
                _CYPHER_REPOINT_MENTIONS,
                loser_id=loser_id,
                survivor_id=survivor_id,
            )
            session.run(
                _CYPHER_REPOINT_CO_MENTIONED_OUT,
                loser_id=loser_id,
                survivor_id=survivor_id,
            )
            session.run(
                _CYPHER_REPOINT_CO_MENTIONED_IN,
                loser_id=loser_id,
                survivor_id=survivor_id,
            )
            session.run(
                _CYPHER_REPOINT_IN_COMMUNITY,
                loser_id=loser_id,
                survivor_id=survivor_id,
            )
            session.run(_CYPHER_DELETE_LOSER, loser_id=loser_id)


# ---------------------------------------------------------------------------
# Serving-cache invalidation (mirrors compute_umap_3d._bust_serving_cache)
# ---------------------------------------------------------------------------


def _bust_emb3d_serving_cache() -> None:
    """Best-effort drop of the ``cerid:graph:emb3d:*`` serving cache.

    A committed merge changes the served node set, so the Constellation's 3D
    embedding cache is stale for up to 24 h. Mirror
    ``compute_umap_3d.ComputeUmap3DJob._bust_serving_cache`` so an operator's
    merge run leaves a fresh cache. This offline script may run without a live
    Redis (e.g. against a dump), so the bust degrades gracefully — it logs and
    returns, never aborting a merge that already committed.
    """
    try:
        from app.deps import get_redis

        redis = get_redis()
        if redis is None:
            logger.warning("emb3d cache bust skipped — no Redis available.")
            return
        dropped = 0
        for key in redis.scan_iter(match="cerid:graph:emb3d:*", count=200):
            redis.delete(key)
            dropped += 1
        logger.info("emb3d serving-cache busted: %d key(s)", dropped)
    except Exception as exc:  # noqa: BLE001 — best-effort; merge already committed
        logger.warning("emb3d cache bust failed (merge already committed): %s", exc)


# ---------------------------------------------------------------------------
# Main entry: run_merge (importable by tests)
# ---------------------------------------------------------------------------


def run_merge(
    driver: Any,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> dict[str, Any]:
    """Scan all (:Entity) nodes, group by canonical id, merge duplicates.

    Parameters
    ----------
    driver:
        Live Neo4j driver (or mock in tests).
    dry_run:
        When True (default) print the plan but do NOT write anything.
    limit:
        If set, process at most this many clusters before stopping.
    embed_fn:
        Optional embedding callable ``(name: str) -> list[float]``.  When
        provided AND ``settings.ENTITY_RESOLUTION_EMBED`` is True, Tier C
        (embedding nearest-canonical) is activated for the grouping pass.
        When None (default) or when the flag is off, only Tiers A + B run
        (unchanged behaviour).

    Returns a summary dict with counters for callers / tests.
    """
    started = time.time()

    entity_rows = _fetch_all_entities(driver)
    if embed_fn is not None:
        groups = _group_by_canonical_tier_c(entity_rows, embed=embed_fn)
    else:
        groups = _group_by_canonical(entity_rows)
    merged_checkpoint = _load_checkpoint()

    duplicate_clusters = {
        cid: members
        for cid, members in groups.items()
        if len(members) >= 2 and cid not in merged_checkpoint
    }
    singleton_count = sum(1 for members in groups.values() if len(members) < 2)

    logger.info(
        "Entity scan: %d total entities, %d duplicate clusters (%d singletons), "
        "%d already checkpointed",
        len(entity_rows),
        len(duplicate_clusters),
        singleton_count,
        len(merged_checkpoint),
    )

    if dry_run:
        for cid, members in duplicate_clusters.items():
            survivor_name = _pick_survivor_name(members)
            total_mentions = sum(m.get("mention_count", 1) for m in members)
            member_ids = [m["canonical_id"] for m in members]
            logger.info(
                "[DRY-RUN] cluster=%s survivor_name=%r members=%s total_mentions=%d",
                cid, survivor_name, member_ids, total_mentions,
            )
        return {
            "dry_run": True,
            "clusters_found": len(duplicate_clusters),
            "merged": 0,
            "singletons_skipped": singleton_count,
            "elapsed_seconds": round(time.time() - started, 2),
        }

    # --- apply path ---
    merged_count = 0
    clusters_list = list(duplicate_clusters.items())
    if limit is not None:
        clusters_list = clusters_list[:limit]

    for i, (survivor_id, members) in enumerate(clusters_list, start=1):
        survivor_name = _pick_survivor_name(members)
        entity_type = members[0]["entity_type"]
        total_mentions = sum(m.get("mention_count", 1) for m in members)
        member_ids = [m["canonical_id"] for m in members]
        logger.info(
            "[%d/%d] Merging cluster=%s survivor=%r members=%s total_mentions=%d",
            i, len(clusters_list),
            survivor_id, survivor_name, member_ids, total_mentions,
        )
        try:
            _merge_cluster(driver, survivor_id, survivor_name, entity_type, members)
        except Exception as exc:  # noqa: BLE001 — per-cluster error must not abort the batch
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error("scripts.merge_entity_aliases.merge_cluster", exc)
            logger.error("Cluster %s failed — skipping: %s", survivor_id, exc)
            continue

        merged_count += 1
        merged_checkpoint.add(survivor_id)
        _save_checkpoint(merged_checkpoint)

    logger.info(
        "Merge complete in %.1fs: %d clusters merged, %d singletons untouched",
        time.time() - started, merged_count, singleton_count,
    )
    if merged_count:
        _bust_emb3d_serving_cache()
    return {
        "dry_run": False,
        "clusters_found": len(duplicate_clusters),
        "merged": merged_count,
        "singletons_skipped": singleton_count,
        "elapsed_seconds": round(time.time() - started, 2),
    }


# ---------------------------------------------------------------------------
# Phase 4.2 — embedding-resolution flow (pair candidates → adjudicate → merge)
#
# This is the "Tier-C for real" path: instead of the incremental A+B+C grouping
# above, it generates high-similarity PAIRS from precomputed entity embeddings,
# auto-merges the confident band, sends the borderline band to the LLM
# adjudicator (bounded per run), and applies confirmed merges through the
# reversible/chunked ``app.db.neo4j.entity.merge_entities`` machinery.
#
# Dry-run by default (mirrors this script's --apply discipline): it prints the
# would-merge plan and writes nothing unless dry_run=False.
# ---------------------------------------------------------------------------


def _fetch_entities_with_embeddings(driver: Any) -> list[dict[str, Any]]:
    """Return entities carrying an ``embedding`` property (parsed to list[float])."""
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (e:Entity)
            WHERE e.embedding IS NOT NULL AND e.canonical_id IS NOT NULL
            RETURN e.canonical_id AS canonical_id,
                   e.name AS name,
                   e.entity_type AS entity_type,
                   coalesce(e.mention_count, 0) AS mention_count,
                   e.primary_domain AS primary_domain,
                   e.embedding AS embedding
            """
        ).data()

    entities: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get("embedding")
        try:
            emb = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            emb = None
        if not emb:
            continue
        entities.append({
            "canonical_id": row["canonical_id"],
            "name": row.get("name") or row["canonical_id"],
            "entity_type": row.get("entity_type") or "",
            "mention_count": int(row.get("mention_count") or 0),
            "primary_domain": row.get("primary_domain") or "",
            "embedding": emb,
        })
    return entities


def _cluster_pairs(
    pairs: list[Any],
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union-find connected components of merge pairs → survivor + losers.

    Survivor per component = highest mention_count (tiebreak: longest name).
    Returns dicts: {survivor_id, loser_ids, entity_type, survivor_name,
    merge_method, merge_confidence}.
    """
    meta = {e["canonical_id"]: e for e in entities}

    parent: dict[str, str] = {}

    def _find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    # component_key → aggregated (min similarity, any-adjudicated flag)
    for pair in pairs:
        _union(pair.a_id, pair.b_id)

    components: dict[str, list[str]] = {}
    for pair in pairs:
        for cid in (pair.a_id, pair.b_id):
            root = _find(cid)
            members = components.setdefault(root, [])
            if cid not in members:
                members.append(cid)

    # Per-component provenance: adjudicated if any constituent pair was
    # borderline; confidence = the lowest pairwise similarity in the component.
    comp_min_sim: dict[str, float] = {}
    comp_adjudicated: dict[str, bool] = {}
    from core.agents.entity_resolution import MergePair  # noqa: PLC0415

    for pair in pairs:
        root = _find(pair.a_id)
        prior = comp_min_sim.get(root)
        comp_min_sim[root] = pair.similarity if prior is None else min(prior, pair.similarity)
        if isinstance(pair, MergePair) and pair.band == "adjudicate":
            comp_adjudicated[root] = True

    from app.db.neo4j.entity import (  # noqa: PLC0415
        MERGE_METHOD_EMBEDDING_ADJUDICATED,
        MERGE_METHOD_EMBEDDING_AUTO,
    )

    clusters: list[dict[str, Any]] = []
    for root, members in components.items():
        def _rank(cid: str) -> tuple[int, int]:
            info = meta.get(cid, {})
            return (int(info.get("mention_count") or 0), len(str(info.get("name") or "")))

        survivor = max(members, key=_rank)
        losers = [m for m in members if m != survivor]
        if not losers:
            continue
        survivor_info = meta.get(survivor, {})
        clusters.append({
            "survivor_id": survivor,
            "loser_ids": losers,
            "entity_type": survivor_info.get("entity_type") or "",
            "survivor_name": survivor_info.get("name") or survivor,
            "merge_method": (
                MERGE_METHOD_EMBEDDING_ADJUDICATED
                if comp_adjudicated.get(root)
                else MERGE_METHOD_EMBEDDING_AUTO
            ),
            "merge_confidence": comp_min_sim.get(root),
        })
    return clusters


async def _resolve_and_plan(
    entities: list[dict[str, Any]],
    *,
    llm_call: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Generate candidate pairs, adjudicate the borderline band, cluster them."""
    from core.agents.entity_resolution import (
        adjudicate_merge_pairs,
        generate_merge_candidates,
    )

    candidates = generate_merge_candidates(entities)
    auto_pairs = [p for p in candidates if p.band == "auto"]
    borderline = [p for p in candidates if p.band == "adjudicate"]

    contexts = {
        e["canonical_id"]: e.get("primary_domain", "")
        for e in entities
        if e.get("primary_domain")
    }
    confirmed = await adjudicate_merge_pairs(
        borderline, contexts=contexts, llm_call=llm_call
    )

    merge_pairs = auto_pairs + confirmed
    clusters = _cluster_pairs(merge_pairs, entities)
    return {
        "candidates": len(candidates),
        "auto_pairs": auto_pairs,
        "borderline_pairs": borderline,
        "confirmed_pairs": confirmed,
        "clusters": clusters,
    }


def run_embedding_resolution(
    driver: Any,
    *,
    dry_run: bool = True,
    llm_call: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Pair-based embedding entity resolution with LLM adjudication.

    Gated on ``settings.ENTITY_RESOLUTION_EMBED`` — a no-op when the flag is
    off (matching the ingest-time Tier-C gate). Dry-run by default: prints the
    plan and writes nothing. When ``dry_run=False`` each cluster is collapsed
    via the reversible ``merge_entities`` machinery.
    """
    import config.settings as _settings

    started = time.time()
    if not _settings.ENTITY_RESOLUTION_EMBED:
        logger.info("ENTITY_RESOLUTION_EMBED is off — embedding resolution skipped.")
        return {"skipped": "disabled", "merged_clusters": 0}

    entities = _fetch_entities_with_embeddings(driver)
    logger.info("embedding-resolution: %d entities with embeddings", len(entities))

    plan = asyncio.run(_resolve_and_plan(entities, llm_call=llm_call))
    clusters = plan["clusters"]

    logger.info(
        "embedding-resolution plan: %d candidate pairs (%d auto, %d borderline "
        "→ %d LLM-confirmed) → %d merge clusters",
        plan["candidates"],
        len(plan["auto_pairs"]),
        len(plan["borderline_pairs"]),
        len(plan["confirmed_pairs"]),
        len(clusters),
    )

    if dry_run:
        for cluster in clusters:
            logger.info(
                "[DRY-RUN] merge survivor=%s losers=%s method=%s confidence=%s",
                cluster["survivor_id"], cluster["loser_ids"],
                cluster["merge_method"], cluster["merge_confidence"],
            )
        return {
            "dry_run": True,
            "candidates": plan["candidates"],
            "auto_pairs": len(plan["auto_pairs"]),
            "borderline_pairs": len(plan["borderline_pairs"]),
            "confirmed_pairs": len(plan["confirmed_pairs"]),
            "merge_clusters": len(clusters),
            "merged_clusters": 0,
            "elapsed_seconds": round(time.time() - started, 2),
        }

    from app.db.neo4j.entity import merge_entities

    merged = 0
    for cluster in clusters:
        try:
            merge_entities(
                driver,
                cluster["survivor_id"],
                cluster["loser_ids"],
                survivor_name=cluster["survivor_name"],
                entity_type=cluster["entity_type"],
                merge_method=cluster["merge_method"],
                merge_confidence=cluster["merge_confidence"],
            )
        except Exception as exc:  # noqa: BLE001 — one bad cluster must not abort the run
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error("scripts.merge_entity_aliases.run_embedding_resolution", exc)
            logger.error("Cluster survivor=%s failed: %s", cluster["survivor_id"], exc)
            continue
        merged += 1

    logger.info(
        "embedding-resolution done in %.1fs: %d clusters merged",
        time.time() - started, merged,
    )
    if merged:
        _bust_emb3d_serving_cache()
    return {
        "dry_run": False,
        "candidates": plan["candidates"],
        "auto_pairs": len(plan["auto_pairs"]),
        "borderline_pairs": len(plan["borderline_pairs"]),
        "confirmed_pairs": len(plan["confirmed_pairs"]),
        "merge_clusters": len(clusters),
        "merged_clusters": merged,
        "elapsed_seconds": round(time.time() - started, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Merge already-stored duplicate (:Entity) nodes into one canonical node. "
            "Safe default: dry-run (prints plan only). Pass --apply to write."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write merges to Neo4j. Without this flag the script is a no-op (dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N duplicate clusters in this run.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear the checkpoint and re-scan from scratch.",
    )
    parser.add_argument(
        "--mode",
        choices=("structural", "embedding"),
        default="structural",
        help=(
            "structural (default): A+B[+incremental-C] canonical-id grouping. "
            "embedding: pair-based Tier-C resolution with LLM adjudication over "
            "precomputed entity embeddings (requires ENTITY_RESOLUTION_EMBED=true "
            "and a prior compute_entity_embeddings run)."
        ),
    )
    args = parser.parse_args()

    import config.settings as _settings
    from app.deps import get_neo4j

    driver = get_neo4j()

    if args.reset and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        logger.info("Checkpoint cleared.")

    if args.mode == "embedding":
        result = run_embedding_resolution(driver, dry_run=not args.apply)
        logger.info("Result: %s", result)
        return 0

    # Build the embed callable for Tier C when the flag is on.
    # Uses the same get_embedding_function() singleton as compute_entity_embeddings.
    embed_fn: Callable[[str], list[float]] | None = None
    if _settings.ENTITY_RESOLUTION_EMBED:
        try:
            from core.utils.embeddings import get_embedding_function
            ef = get_embedding_function()
            if ef is not None:
                # ef(["name"]) → list[list[float]]; wrap to the (str→list[float]) signature
                def embed_fn(name: str, _ef: Any = ef) -> list[float]:  # type: ignore[misc]
                    result = _ef([name])
                    return list(result[0]) if result else []
            else:
                logger.warning(
                    "ENTITY_RESOLUTION_EMBED=true but EMBEDDING_MODEL is the server "
                    "default (no client-side embedder). Tier C disabled for this run."
                )
        except Exception as exc:  # noqa: BLE001
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error("scripts.merge_entity_aliases.main.embed_init", exc)
            logger.warning("Tier C embed init failed — falling back to A+B only.")

    if embed_fn is not None:
        logger.info("Tier C (embedding) enabled for this run.")
    else:
        logger.info("Tier C disabled — A+B grouping only.")

    result = run_merge(driver, dry_run=not args.apply, limit=args.limit, embed_fn=embed_fn)
    logger.info("Result: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
