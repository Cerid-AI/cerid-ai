# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Neo4j data-access for the STRATA decomposition endpoint (Cycle 4).

Three aggregate Cypher queries on indexed entity properties:

  1. ``get_decomposition_tree`` — full tier tree: 11 domains, conditional
     subcategory groups (aggregated from e.primary_subcategory), 262 L1 +
     503 L0 communities with sizes/labels/mode-domain/purity, derived
     L0→L1 parent map, per-domain unclustered counts, size<4 rollup buckets.

  2. ``get_community_entities`` — entity leaves for one L0 community,
     each carrying ``path: [domain, sub?, l1, l0]`` for the search-palette
     path walk.

  3. ``_get_l1_parent_map`` — internal helper: derive L0→L1 parent from
     entity membership (no PARENT_OF edge exists).

Indexed properties used (all have entity_primary_domain_idx or community
indexes on the live schema):
  - e.primary_domain
  - e.community_id   (level-0 flat id, format "0:NNNN" or bare int)
  - e.primary_subcategory
  - c.level
  - c.id

No SubCategory node joins — those are broken for live data (see
grounding-hierarchy.md §1d). Subcategory tier aggregated from
e.primary_subcategory properties only.

no_communities_computed flag (A3): True iff zero Community nodes exist at
any level — distinguishes "Leiden never ran" from "empty KB".
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.graph.decomposition")

# Size threshold for L0 rollup buckets: communities smaller than this
# are collapsed into the "Smaller clusters" bucket per domain.
_L0_ROLLUP_THRESHOLD = 4

# Maximum top-hub entities included in the fallback label (A6).
_FALLBACK_HUB_COUNT = 3

# Regex that matches a bare numeric label — a first-clause guard
# detecting when _first_clause() would yield a number like "0.7143".
_NUMERIC_GARBAGE_RE = re.compile(r"^\d+(\.\d+)?$")


def _is_numeric_garbage(label: str) -> bool:
    """Return True if label is purely numeric / garbage (e.g. '0.7143')."""
    return bool(_NUMERIC_GARBAGE_RE.match(label.strip()))


def get_decomposition_tree(driver: Any) -> dict[str, Any]:
    """Build the full STRATA decomposition tree payload from Neo4j.

    Returns a dict whose field names match the TypeScript contract exactly:

        {
            no_communities_computed: bool,   # A3: True iff Leiden never ran
            domains: list[DomainNode],
            parent_map: dict[str, str],      # L0 id → L1 id
            uncategorized_count: int,        # entities with no primary_domain
            computed_at: str | None,
        }

    DomainNode:
        {
            id: str,              # domain key, e.g. "research"
            label: str,           # human-readable (title-cased domain)
            entity_count: int,
            unclustered: {count: int},
            subcategories: list[SubCategoryNode] | None,   # None = single sub
            communities: list[L1Node],
        }

    SubCategoryNode:
        {
            id: str,              # subcategory key
            label: str,
            entity_count: int,
            children: list[L1Node],
        }

    L1Node:
        {
            id: str,              # "1:NNNN"
            size: int,            # entity count
            label: str | None,
            mode_domain: str,
            purity: float,
            top_hubs: list[{id, name, degree}],
            children: list[L0Node | L0RollupBucket],
        }

    L0Node:
        {
            id: str,              # "0:NNNN"
            size: int,
            label: str | None,
            mode_domain: str,
            purity: float,
            top_hubs: list[{id, name, degree}],
        }

    L0RollupBucket:
        {
            kind: "rollup",
            community_count: int,
            entity_count: int,
        }
    """
    # ---- 1. Check if Leiden has ever run (A3) --------------------------------
    no_communities_computed = _check_no_communities(driver)

    # ---- 2. Load Community nodes: id, level, name + summary (for labels) ----
    community_summaries: dict[str, str] = {}  # community_id → summary text
    community_names: dict[str, str] = {}  # community_id → curated short name
    community_ids_by_level: dict[int, list[str]] = {}  # level → [ids]
    if not no_communities_computed:
        try:
            with driver.session() as s:
                rows = s.run(
                    "MATCH (c:Community) WHERE c.level IN [0, 1] "
                    "RETURN c.id AS cid, c.level AS level, "
                    "       c.name AS name, c.summary AS summary"
                )
                for row in rows:
                    cid = str(row["cid"] or "")
                    level = int(row["level"] or 0)
                    summary = str(row["summary"] or "") if row["summary"] else ""
                    name = str(row["name"] or "") if row.get("name") else ""
                    if cid:
                        if summary:
                            community_summaries[cid] = summary
                        if name:
                            community_names[cid] = name
                        community_ids_by_level.setdefault(level, []).append(cid)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("decomposition.load_communities", exc)

    # ---- 3. Entity-level aggregate: per-domain, per-community_id, per-subcategory
    #         Three queries: (a) domain counts (b) community membership (c) derived_at
    entity_domain_counts: dict[str, int] = {}
    entity_by_community: dict[str, list[dict[str, Any]]] = {}  # L0 cid → [{id, name, degree, domain}]
    unclustered_by_domain: dict[str, int] = {}
    derived_at: str | None = None
    uncategorized_count = 0

    try:
        with driver.session() as s:
            # 3a. Per-domain entity counts
            rows = s.run(
                "MATCH (e:Entity) WHERE e.primary_domain IS NOT NULL "
                "RETURN e.primary_domain AS domain, count(e) AS cnt"
            )
            for row in rows:
                entity_domain_counts[str(row["domain"])] = int(row["cnt"])

            # Entities with no primary_domain — the actual uncategorized set.
            # (Counting falsy keys of entity_domain_counts is always 0: those
            # keys are NOT-NULL domain strings, all truthy.)
            urow = s.run(
                "MATCH (e:Entity) WHERE e.primary_domain IS NULL "
                "RETURN count(e) AS cnt"
            ).single()
            uncategorized_count = int(urow["cnt"]) if urow else 0

            # 3b. Entity community membership (level 0) + domain + sub + degree
            rows = s.run(
                "MATCH (e:Entity) WHERE e.primary_domain IS NOT NULL "
                "OPTIONAL MATCH (e)-[:CO_MENTIONED]-() "
                "WITH e, count(*) AS deg "
                "RETURN "
                "  e.canonical_id AS entity_id, "
                "  e.name AS name, "
                "  e.primary_domain AS domain, "
                "  e.primary_subcategory AS sub, "
                "  e.community_id AS community_id, "
                "  deg AS degree"
            )
            for row in rows:
                eid = str(row["entity_id"] or "")
                domain = str(row["domain"] or "")
                sub = str(row["sub"] or "") if row["sub"] else ""
                comm_id = _normalize_community_id(row["community_id"], level=0)
                degree = int(row["degree"] or 0)

                if not eid:
                    continue

                if not comm_id:
                    # unclustered
                    unclustered_by_domain[domain] = unclustered_by_domain.get(domain, 0) + 1
                else:
                    entity_by_community.setdefault(comm_id, []).append({
                        "id": eid,
                        "name": str(row["name"] or eid),
                        "domain": domain,
                        "sub": sub,
                        "degree": degree,
                    })

            # 3c. derived_at
            rows = s.run(
                "MATCH (e:Entity) WHERE e.domains_updated_at IS NOT NULL "
                "RETURN max(e.domains_updated_at) AS derived_at"
            )
            row = rows.single()
            if row and row["derived_at"]:
                derived_at = str(row["derived_at"])

    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("decomposition.entity_aggregates", exc)

    # ---- 4. Derive L0 → L1 parent map from entity co-membership ---------------
    l0_to_l1 = _derive_l0_to_l1_map(driver, entity_by_community)

    # ---- 5. Build L1 community index (L1 cid → L0 children) ------------------
    l1_children: dict[str, list[str]] = {}  # L1 cid → [L0 cid]
    for l0_cid, l1_cid in l0_to_l1.items():
        l1_children.setdefault(l1_cid, []).append(l0_cid)

    # All L1 community ids from our community node list + any from parent map
    all_l1_ids = set(community_ids_by_level.get(1, []))
    all_l1_ids.update(l0_to_l1.values())

    # ---- 6. Assemble per-L0 community metadata --------------------------------
    # We need: mode_domain, purity, label, size, top_hubs
    l0_meta: dict[str, dict[str, Any]] = {}
    for l0_cid, members in entity_by_community.items():
        l0_meta[l0_cid] = _build_community_meta(
            l0_cid, members, community_summaries, community_names, level=0
        )

    # ---- 7. Assemble per-L1 community metadata --------------------------------
    # L1 entity count derived from its L0 children
    l1_meta: dict[str, dict[str, Any]] = {}
    for l1_cid in all_l1_ids:
        children = l1_children.get(l1_cid, [])
        # Aggregate entity list across all L0 children
        l1_members: list[dict[str, Any]] = []
        for l0_cid in children:
            l1_members.extend(entity_by_community.get(l0_cid, []))
        if l1_members or l1_cid in community_summaries:
            l1_meta[l1_cid] = _build_community_meta(
                l1_cid, l1_members, community_summaries, community_names, level=1
            )

    # ---- 8. Build the domain tree ---------------------------------------------
    all_domains = sorted(
        entity_domain_counts.keys(),
        key=lambda d: -entity_domain_counts.get(d, 0),
    )

    domain_nodes: list[dict[str, Any]] = []
    for domain in all_domains:
        ec = entity_domain_counts.get(domain, 0)
        unclustered = unclustered_by_domain.get(domain, 0)

        # Determine which L1/L0 communities belong to this domain
        # (by mode_domain of the L0 community or direct L1 aggregate)
        domain_l0: dict[str, dict[str, Any]] = {
            cid: meta
            for cid, meta in l0_meta.items()
            if meta.get("mode_domain") == domain
        }

        domain_l1_ids: set[str] = set()
        for l0_cid in domain_l0:
            l1_cid_opt = l0_to_l1.get(l0_cid)
            if l1_cid_opt:
                domain_l1_ids.add(l1_cid_opt)

        # For any L1 without matched L0 children via mode_domain,
        # also include by L1 mode_domain
        for l1_cid, meta in l1_meta.items():
            if meta.get("mode_domain") == domain:
                domain_l1_ids.add(l1_cid)

        # Build L1 nodes (contract field names: id, size, children, top_hubs)
        l1_nodes: list[dict[str, Any]] = []
        for l1_cid in sorted(domain_l1_ids):
            meta = l1_meta.get(l1_cid, {})
            children_ids = l1_children.get(l1_cid, [])

            l0_nodes_full: list[dict[str, Any]] = []
            rollup_children: list[dict[str, Any]] = []
            rollup_entity_count = 0

            for l0_cid in sorted(children_ids):
                l0m = l0_meta.get(l0_cid, {})
                if not l0m:
                    continue
                l0_node = {
                    "id": l0_cid,
                    "size": l0m.get("size", 0),
                    "label": l0m.get("label") or None,
                    "mode_domain": l0m.get("mode_domain", domain),
                    "purity": l0m.get("purity", 1.0),
                    "top_hubs": l0m.get("top_hubs", []),
                }
                if l0m.get("size", 0) < _L0_ROLLUP_THRESHOLD:
                    rollup_children.append(l0_node)
                    rollup_entity_count += l0m.get("size", 0)
                else:
                    l0_nodes_full.append(l0_node)

            # Sort L0 by size desc
            l0_nodes_full.sort(key=lambda n: -n["size"])
            rollup_children.sort(key=lambda n: -n["size"])

            # Append rollup bucket at the end if any small communities exist.
            # The bucket carries its member communities (UX-13) so the client
            # can drill into them instead of rendering an inert count.
            l1_children_list: list[dict[str, Any]] = list(l0_nodes_full)
            if rollup_children:
                l1_children_list.append({
                    "kind": "rollup",
                    "community_count": len(rollup_children),
                    "entity_count": rollup_entity_count,
                    "communities": rollup_children,
                })

            l1_size = meta.get("size", 0)
            if l1_size == 0:
                l1_size = sum(
                    l0_meta.get(c, {}).get("size", 0)
                    for c in children_ids
                )

            l1_nodes.append({
                "id": l1_cid,
                "size": l1_size,
                "label": meta.get("label") or None,
                "mode_domain": meta.get("mode_domain", domain),
                "purity": meta.get("purity", 1.0),
                "top_hubs": meta.get("top_hubs", []),
                "children": l1_children_list,
            })

        # Sort L1 by size desc
        l1_nodes.sort(key=lambda n: -n["size"])

        # Flat community list per domain. A prior "subcategory" tier assigned the
        # SAME l1_nodes to EVERY subcategory, so the frontend — which always
        # flattens `subcategories.flatMap(children)` and never renders per-sub
        # headers — rendered each community once per subcategory (2–3× dupes).
        # Emit the deduplicated flat list the frontend actually consumes.
        node: dict[str, Any] = {
            "id": domain,
            "label": domain.replace("_", " ").title(),
            "entity_count": ec,
            "unclustered": {"count": unclustered},
            "communities": l1_nodes,
        }

        domain_nodes.append(node)

    # uncategorized_count computed in the session block above.
    return {
        "no_communities_computed": no_communities_computed,
        "domains": domain_nodes,
        "parent_map": l0_to_l1,
        "uncategorized_count": uncategorized_count,
        "computed_at": derived_at,
    }


def get_community_entities(
    driver: Any,
    community_id: str,
    l0_to_l1: dict[str, str] | None = None,
) -> list[dict[str, Any]] | None:
    """Return entity leaves for one L0 community.

    Each entity carries ``path: [domain, sub?, l1, l0]`` for the
    search-palette path walk.

    Returns None if the community does not exist.
    Returns an empty list if no entities are clustered in it.

    Args:
        driver: live Neo4j driver.
        community_id: L0 community id in "0:NNNN" or bare int format.
        l0_to_l1: optional pre-computed parent map (avoids re-deriving).
    """
    norm_id = _normalize_community_id(community_id, level=0)
    bare = _bare_community_id(community_id)

    try:
        with driver.session() as s:
            rows = s.run(
                "MATCH (e:Entity) "
                "WHERE e.community_id = $bare_id OR e.community_id = $norm_id "
                "OPTIONAL MATCH (e)-[:CO_MENTIONED]-() "
                "WITH e, count(*) AS degree "
                "RETURN "
                "  e.canonical_id AS entity_id, "
                "  e.name AS name, "
                "  e.entity_type AS entity_type, "
                "  e.primary_domain AS domain, "
                "  e.primary_subcategory AS sub, "
                "  e.community_id AS community_id, "
                "  coalesce(e.trust_state, 'unknown') AS trust_state, "
                "  coalesce(e.mention_count, 0) AS mention_count, "
                "  degree "
                "ORDER BY degree DESC",
                bare_id=bare,
                norm_id=norm_id,
            )
            data = list(rows.data())
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "decomposition.get_community_entities",
            exc,
            context={"community_id": community_id},
        )
        return None

    if not data:
        # Check if community node exists at all
        try:
            with driver.session() as s:
                exists_row = s.run(
                    "MATCH (c:Community) WHERE c.id = $cid OR c.id = $norm_id "
                    "RETURN count(c) AS cnt",
                    cid=bare,
                    norm_id=norm_id,
                ).single()
                if not exists_row or int(exists_row["cnt"]) == 0:
                    return None
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("decomposition.check_community_exists", exc)
        return []

    # Build path per entity
    entities: list[dict[str, Any]] = []
    for row in data:
        eid = str(row.get("entity_id") or "")
        domain = str(row.get("domain") or "")
        sub = str(row.get("sub") or "") if row.get("sub") else None
        l0_cid = norm_id
        l1_cid = (l0_to_l1 or {}).get(l0_cid, "")

        # Build path array: [domain, sub?, l1, l0]
        path: list[str] = [domain]
        if sub:
            path.append(sub)
        if l1_cid:
            path.append(l1_cid)
        path.append(l0_cid)

        entities.append({
            "id": eid,
            "name": str(row.get("name") or eid),
            "type": str(row.get("entity_type") or "OTHER"),
            "trust_state": str(row.get("trust_state") or "unknown"),
            "path": path,
        })

    return entities


def get_bucket_entities(
    driver: Any,
    *,
    bucket: str,
    domain: str | None = None,
) -> list[dict[str, Any]] | None:
    """Entity leaves for a non-community bucket (UX-13 drill paths).

    ``bucket="unclustered"`` — entities in ``domain`` with no community
    membership (``domain`` required).
    ``bucket="uncategorized"`` — entities with no primary_domain at all
    (``domain`` ignored).

    Returns None for an unknown bucket / missing required domain; the leaf
    shape matches ``get_community_entities``.
    """
    if bucket == "unclustered":
        if not domain:
            return None
        where = "e.primary_domain = $domain AND e.community_id IS NULL"
    elif bucket == "uncategorized":
        where = "e.primary_domain IS NULL"
    else:
        return None

    try:
        with driver.session() as s:
            rows = s.run(
                "MATCH (e:Entity) "
                f"WHERE {where} "
                "OPTIONAL MATCH (e)-[:CO_MENTIONED]-() "
                "WITH e, count(*) AS degree "
                "RETURN "
                "  e.canonical_id AS entity_id, "
                "  e.name AS name, "
                "  e.entity_type AS entity_type, "
                "  e.primary_domain AS domain, "
                "  e.primary_subcategory AS sub, "
                "  coalesce(e.trust_state, 'unknown') AS trust_state, "
                "  degree "
                "ORDER BY degree DESC",
                domain=domain,
            )
            data = list(rows.data())
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "decomposition.get_bucket_entities",
            exc,
            context={"bucket": bucket, "domain": domain},
        )
        return None

    entities: list[dict[str, Any]] = []
    for row in data:
        eid = str(row.get("entity_id") or "")
        if not eid:
            continue
        row_domain = str(row.get("domain") or "")
        sub = str(row.get("sub") or "") if row.get("sub") else None
        path: list[str] = [row_domain] if row_domain else []
        if sub:
            path.append(sub)
        entities.append({
            "id": eid,
            "name": str(row.get("name") or eid),
            "type": str(row.get("entity_type") or "OTHER"),
            "trust_state": str(row.get("trust_state") or "unknown"),
            "path": path,
        })
    return entities


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_no_communities(driver: Any) -> bool:
    """Return True iff zero Community nodes exist (Leiden never ran). A3."""
    try:
        with driver.session() as s:
            row = s.run("MATCH (c:Community) RETURN count(c) AS cnt LIMIT 1").single()
            if row is None:
                return True
            return int(row["cnt"]) == 0
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("decomposition._check_no_communities", exc)
        return True  # fail safe: report as no-communities


def _normalize_community_id(raw: Any, *, level: int) -> str:
    """Normalize a community id to "{level}:{native_id}" format.

    Entity.community_id is stored as the bare native_id scalar (e.g. ``2546``)
    OR as the full "{level}:{native_id}" string.  Community.id is always the
    full form.  We normalize to the full form for consistent keying.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if ":" in s:
        return s  # already normalized
    return f"{level}:{s}"


def _bare_community_id(community_id: str) -> str:
    """Extract the bare native_id scalar from a community id."""
    s = str(community_id).strip()
    if ":" in s:
        parts = s.split(":", 1)
        return parts[1]
    return s


def _derive_l0_to_l1_map(
    driver: Any,
    entity_by_community: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    """Derive L0 → L1 community parent map from entity co-membership.

    Algorithm:
    1. Fetch all entities and their level-1 community ids.
    2. For each L0 community (keyed by level-0 entity membership),
       find the L1 community that the majority of its members belong to.

    No PARENT_OF edge exists in the schema; this is the correct derivation.
    """
    if not entity_by_community:
        return {}

    entity_l1_map: dict[str, str] = {}  # entity_id → L1 community_id
    try:
        with driver.session() as s:
            rows = s.run(
                "MATCH (e:Entity)-[:IN_COMMUNITY]->(c:Community {level: 1}) "
                "WHERE e.canonical_id IS NOT NULL "
                "RETURN e.canonical_id AS eid, c.id AS l1_id"
            )
            for row in rows:
                eid = str(row["eid"] or "")
                l1_id = str(row["l1_id"] or "")
                if eid and l1_id:
                    entity_l1_map[eid] = l1_id
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("decomposition._derive_l0_to_l1_map", exc)
        return {}

    l0_to_l1: dict[str, str] = {}
    for l0_cid, members in entity_by_community.items():
        # Vote: which L1 do most members of this L0 belong to?
        votes: dict[str, int] = {}
        for member in members:
            l1 = entity_l1_map.get(member["id"])
            if l1:
                votes[l1] = votes.get(l1, 0) + 1
        if votes:
            l0_to_l1[l0_cid] = max(votes, key=lambda k: votes[k])

    return l0_to_l1


def _build_community_meta(
    community_id: str,
    members: list[dict[str, Any]],
    summaries: dict[str, str],
    names: dict[str, str] | None = None,
    *,
    level: int,
) -> dict[str, Any]:
    """Compute label, mode_domain, purity, size, top_hubs for a community.

    Field names match the TypeScript contract (id/size/top_hubs).
    Label ladder: curated Community.name → summary first clause → A6 fallback.
    """
    size = len(members)

    # Mode domain
    domain_counts: dict[str, int] = {}
    for m in members:
        d = m.get("domain", "")
        if d:
            domain_counts[d] = domain_counts.get(d, 0) + 1

    if domain_counts:
        mode_domain = max(domain_counts, key=lambda k: domain_counts[k])
        purity = domain_counts[mode_domain] / size if size else 1.0
    else:
        mode_domain = ""
        purity = 1.0

    # top_hubs: top-degree entities (up to _FALLBACK_HUB_COUNT), sorted by degree desc
    sorted_members = sorted(
        members,
        key=lambda m: (-m.get("degree", 0), m.get("name", "")),
    )
    top_hubs = [
        {"id": m.get("id", ""), "name": m.get("name", ""), "degree": m.get("degree", 0)}
        for m in sorted_members[:_FALLBACK_HUB_COUNT]
        if m.get("id") or m.get("name")
    ]

    # Label ladder: curated Community.name → summary first-clause → A6 fallback
    bare = _bare_community_id(community_id)
    label: str | None = None
    if names:
        label = (
            names.get(community_id)
            or names.get(bare)
            or names.get(f"{level}:{bare}")
            or None
        )

    if not label:
        summary_text = (
            summaries.get(community_id, "")
            or summaries.get(bare, "")
            or summaries.get(f"{level}:{bare}", "")
        )
        if summary_text:
            first = _first_clause_safe(summary_text)
            if first:
                label = first

    if not label:
        # A6: deterministic fallback label — also None-safe for optional field
        label = _fallback_label(community_id, members, size)

    return {
        "id": community_id,
        "size": size,
        "label": label or None,
        "mode_domain": mode_domain,
        "purity": round(purity, 3),
        "top_hubs": top_hubs,
    }


def _first_clause_safe(text: str, max_chars: int = 48) -> str:
    """Extract first clause from a summary, guard against numeric garbage.

    Returns empty string if the result would be numeric garbage (A6 guard).
    Truncates at a word boundary with an ellipsis — never mid-word (UX-15).
    """
    if not text:
        return ""
    from app.db.neo4j.community_summaries import _BOILERPLATE_OPENER  # noqa: PLC0415

    # Strip LLM boilerplate lead-ins
    stripped = re.sub(_BOILERPLATE_OPENER, "", text.strip(), flags=re.IGNORECASE)
    parts = re.split(r"[.:\n,]", stripped, maxsplit=1)
    first = parts[0].strip()
    if not first:
        return ""
    # Numeric garbage guard
    if _is_numeric_garbage(first):
        return ""
    first = first[0].upper() + first[1:]
    if len(first) <= max_chars:
        return first
    cut = first[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def _fallback_label(
    community_id: str,
    members: list[dict[str, Any]],
    entity_count: int,
) -> str:
    """A6: deterministic fallback label for an unlabeled community.

    Format: "Community of N — top entities: A, B, C"
    Members ordered by degree descending (top-hub first).
    Same community always renders same label across sessions.
    """
    n = entity_count or len(members)
    if not members:
        return f"Community of {n}"
    # Sort by degree descending, then name ascending (deterministic tie-break)
    sorted_members = sorted(
        members,
        key=lambda m: (-m.get("degree", 0), m.get("name", "")),
    )
    top_names = [
        m.get("name") or m.get("id", "")
        for m in sorted_members[:_FALLBACK_HUB_COUNT]
        if m.get("name") or m.get("id")
    ]
    if top_names:
        hubs = ", ".join(top_names)
        return f"Community of {n} — top entities: {hubs}"
    return f"Community of {n}"
