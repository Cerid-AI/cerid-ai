# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j taxonomy CRUD — domains, sub-categories, tags."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import config
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph")


def get_taxonomy(driver) -> Dict[str, Any]:
    """Return the full taxonomy tree from Neo4j (domains, sub-categories, tags)."""
    domains: Dict[str, Any] = {}
    tags: List[Dict[str, Any]] = []

    with driver.session() as session:
        result = session.run(
            "MATCH (d:Domain) "
            "OPTIONAL MATCH (a:Artifact)-[:BELONGS_TO]->(d) "
            "RETURN d.name AS name, d.description AS description, d.icon AS icon, "
            "count(a) AS artifact_count "
            "ORDER BY d.name"
        )
        for record in result:
            domains[record["name"]] = {
                "description": record["description"] or "",
                "icon": record["icon"] or "file",
                "sub_categories": [],
                "artifact_count": record["artifact_count"],
            }

        result = session.run(
            "MATCH (sc:SubCategory)-[:BELONGS_TO]->(d:Domain) "
            "OPTIONAL MATCH (a:Artifact)-[:CATEGORIZED_AS]->(sc) "
            "RETURN sc.label AS label, d.name AS domain, count(a) AS artifact_count "
            "ORDER BY d.name, sc.label"
        )
        for record in result:
            domain_name = record["domain"]
            if domain_name in domains:
                domains[domain_name]["sub_categories"].append({
                    "name": record["label"],
                    "artifact_count": record["artifact_count"],
                })

        result = session.run(
            "MATCH (t:Tag) "
            "OPTIONAL MATCH (a:Artifact)-[:TAGGED_WITH]->(t) "
            "RETURN t.name AS name, count(a) AS usage_count "
            "ORDER BY count(a) DESC "
            "LIMIT 200"
        )
        tags = [
            {"name": record["name"], "usage_count": record["usage_count"]}
            for record in result
        ]

    return {"domains": domains, "tags": tags}


def create_domain(
    driver,
    name: str,
    description: str = "",
    icon: str = "file",
    sub_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new domain with optional sub-categories."""
    now = utcnow_iso()
    subs = sub_categories or ["general"]

    with driver.session() as session:
        session.run(
            "MERGE (d:Domain {name: $name}) "
            "ON CREATE SET d.description = $desc, d.icon = $icon, d.created_at = $now",
            name=name,
            desc=description,
            icon=icon,
            now=now,
        )
        for sub in subs:
            sc_name = f"{name}/{sub}"
            session.run(
                "MERGE (sc:SubCategory {name: $sc_name}) "
                "ON CREATE SET sc.domain = $domain, sc.label = $label, sc.created_at = $now "
                "WITH sc "
                "MATCH (d:Domain {name: $domain}) "
                "MERGE (sc)-[:BELONGS_TO]->(d)",
                sc_name=sc_name,
                domain=name,
                label=sub,
                now=now,
            )

    logger.info(f"Created domain '{name}' with {len(subs)} sub-categories")
    return {"name": name, "description": description, "icon": icon, "sub_categories": subs}


def create_sub_category(
    driver,
    domain: str,
    label: str,
) -> Dict[str, str]:
    """Add a sub-category to an existing domain."""
    now = utcnow_iso()
    sc_name = f"{domain}/{label}"

    with driver.session() as session:
        result = session.run(
            "MATCH (d:Domain {name: $domain}) RETURN d.name AS name",
            domain=domain,
        )
        if not result.single():
            raise ValueError(f"Domain not found: {domain}")

        session.run(
            "MERGE (sc:SubCategory {name: $sc_name}) "
            "ON CREATE SET sc.domain = $domain, sc.label = $label, sc.created_at = $now "
            "WITH sc "
            "MATCH (d:Domain {name: $domain}) "
            "MERGE (sc)-[:BELONGS_TO]->(d)",
            sc_name=sc_name,
            domain=domain,
            label=label,
            now=now,
        )

    logger.info(f"Created sub-category '{domain}/{label}'")
    return {"domain": domain, "sub_category": label}


def list_tags(driver, limit: int = 100) -> List[Dict[str, Any]]:
    """List all tags with usage counts, sorted by popularity."""
    with driver.session() as session:
        result = session.run(
            "MATCH (t:Tag) "
            "OPTIONAL MATCH (a:Artifact)-[:TAGGED_WITH]->(t) "
            "RETURN t.name AS name, count(a) AS usage_count "
            "ORDER BY count(a) DESC "
            "LIMIT $limit",
            limit=limit,
        )
        return [
            {"name": record["name"], "usage_count": record["usage_count"]}
            for record in result
        ]


def update_artifact_taxonomy(
    driver,
    artifact_id: str,
    sub_category: Optional[str] = None,
    tags_json: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an artifact's sub-category and/or tags."""
    now = utcnow_iso()

    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {id: $aid}) RETURN a.domain AS domain, "
            "a.sub_category AS sub_category, a.tags AS tags",
            aid=artifact_id,
        )
        record = result.single()
        if not record:
            raise ValueError(f"Artifact not found: {artifact_id}")

        domain = record["domain"]

        if sub_category is not None:
            session.run(
                "MATCH (a:Artifact {id: $aid}) "
                "SET a.sub_category = $sc, a.modified_at = $now",
                aid=artifact_id,
                sc=sub_category,
                now=now,
            )
            session.run(
                "MATCH (a:Artifact {id: $aid})-[r:CATEGORIZED_AS]->() DELETE r",
                aid=artifact_id,
            )
            sc_name = f"{domain}/{sub_category}"
            session.run(
                "MATCH (a:Artifact {id: $aid}), (sc:SubCategory {name: $sc_name}) "
                "MERGE (a)-[:CATEGORIZED_AS]->(sc)",
                aid=artifact_id,
                sc_name=sc_name,
            )

        if tags_json is not None:
            session.run(
                "MATCH (a:Artifact {id: $aid}) "
                "SET a.tags = $tags, a.modified_at = $now",
                aid=artifact_id,
                tags=tags_json,
                now=now,
            )
            session.run(
                "MATCH (a:Artifact {id: $aid})-[r:TAGGED_WITH]->() DELETE r",
                aid=artifact_id,
            )
            try:
                tag_list = json.loads(tags_json) if tags_json else []
            except (json.JSONDecodeError, TypeError):
                tag_list = []
            for tag in tag_list:
                tag_lower = tag.strip().lower()
                if tag_lower:
                    session.run(
                        "MERGE (t:Tag {name: $tag}) "
                        "ON CREATE SET t.created_at = $now, t.usage_count = 1 "
                        "ON MATCH SET t.usage_count = t.usage_count + 1 "
                        "WITH t "
                        "MATCH (a:Artifact {id: $aid}) "
                        "MERGE (a)-[:TAGGED_WITH]->(t)",
                        tag=tag_lower,
                        aid=artifact_id,
                        now=now,
                    )

    return {"artifact_id": artifact_id, "sub_category": sub_category, "tags": tags_json}
