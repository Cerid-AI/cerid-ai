# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Taxonomy management endpoints.

Provides CRUD for the hierarchical taxonomy: domains -> sub-categories -> tags.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from db import neo4j as graph
from deps import get_neo4j

router = APIRouter()
logger = logging.getLogger("ai-companion.taxonomy")


# ── Pydantic models ──────────────────────────────────────────────────────────

class CreateDomainRequest(BaseModel):
    name: str
    description: str = ""
    icon: str = "file"
    sub_categories: list[str] = ["general"]


class CreateSubCategoryRequest(BaseModel):
    domain: str
    name: str


class UpdateArtifactTaxonomyRequest(BaseModel):
    artifact_id: str
    sub_category: str | None = None
    tags: list[str] | None = None


class MergeTagsRequest(BaseModel):
    source_tag: str
    target_tag: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/taxonomy")
async def get_taxonomy_endpoint():
    """Return the full taxonomy tree (domains, sub-categories, tags)."""
    try:
        driver = get_neo4j()
        return graph.get_taxonomy(driver)
    except Exception as e:
        logger.error(f"Get taxonomy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/taxonomy/domain")
async def create_domain_endpoint(req: CreateDomainRequest):
    """Create a new domain with optional sub-categories."""
    name = req.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Domain name is required")
    if name in config.DOMAINS:
        raise HTTPException(status_code=409, detail=f"Domain '{name}' already exists")

    try:
        driver = get_neo4j()
        result = graph.create_domain(
            driver,
            name=name,
            description=req.description,
            icon=req.icon,
            sub_categories=req.sub_categories,
        )
        # Update runtime DOMAINS list and TAXONOMY dict
        if name not in config.TAXONOMY:
            config.TAXONOMY[name] = {
                "description": req.description,
                "icon": req.icon,
                "sub_categories": req.sub_categories,
            }
            config.DOMAINS = list(config.TAXONOMY.keys())
        return result
    except Exception as e:
        logger.error(f"Create domain error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/taxonomy/subcategory")
async def create_subcategory_endpoint(req: CreateSubCategoryRequest):
    """Add a sub-category to an existing domain."""
    domain = req.domain.strip().lower()
    label = req.name.strip().lower()

    if not domain or not label:
        raise HTTPException(status_code=400, detail="Domain and name are required")
    if domain not in config.DOMAINS:
        raise HTTPException(status_code=404, detail=f"Domain '{domain}' not found")

    try:
        driver = get_neo4j()
        result = graph.create_sub_category(driver, domain=domain, label=label)
        # Update runtime TAXONOMY
        if domain in config.TAXONOMY:
            subs = list(config.TAXONOMY[domain].get("sub_categories", []))
            if label not in subs:
                subs.append(label)
                config.TAXONOMY[domain]["sub_categories"] = subs
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create sub-category error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tags")
async def list_tags_endpoint(
    limit: int = Query(100, ge=1, le=500),
):
    """List all tags with usage counts, sorted by popularity."""
    try:
        driver = get_neo4j()
        return graph.list_tags(driver, limit=limit)
    except Exception as e:
        logger.error(f"List tags error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/taxonomy/artifact")
async def update_artifact_taxonomy_endpoint(req: UpdateArtifactTaxonomyRequest):
    """Update an artifact's sub-category and/or tags."""
    try:
        driver = get_neo4j()
        tags_json = json.dumps(req.tags) if req.tags is not None else None
        return graph.update_artifact_taxonomy(
            driver,
            artifact_id=req.artifact_id,
            sub_category=req.sub_category,
            tags_json=tags_json,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Update artifact taxonomy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tags/suggest")
async def suggest_tags_endpoint(
    domain: str | None = Query(None, description="Filter vocabulary by domain"),
    prefix: str = Query("", description="Prefix filter for typeahead"),
    limit: int = Query(30, ge=1, le=100),
):
    """Suggest tags for typeahead: vocabulary tags first, then popular existing tags.

    Returns a list of {name, source, usage_count} objects.
    source is "vocabulary" for controlled vocabulary tags, "existing" for user-created tags.
    """
    prefix_lower = prefix.strip().lower()

    # Vocabulary tags for the requested domain (or all domains)
    vocab_tags: list[str] = []
    if domain and domain in config.TAG_VOCABULARY:
        vocab_tags = list(config.TAG_VOCABULARY[domain])
    else:
        seen: set[str] = set()
        for tags in config.TAG_VOCABULARY.values():
            for tag in tags:
                if tag not in seen:
                    seen.add(tag)
                    vocab_tags.append(tag)

    if prefix_lower:
        vocab_tags = [t for t in vocab_tags if t.startswith(prefix_lower)]

    # Get popular existing tags from Neo4j
    try:
        driver = get_neo4j()
        existing_tags = graph.list_tags(driver, limit=200)
    except Exception as e:
        logger.error(f"Tag suggest error: {e}")
        existing_tags = []

    vocab_set = set(vocab_tags)
    results: list[dict] = []

    for tag in vocab_tags[:limit]:
        usage = next((t["usage_count"] for t in existing_tags if t["name"] == tag), 0)
        results.append({"name": tag, "source": "vocabulary", "usage_count": usage})

    for tag_info in existing_tags:
        if len(results) >= limit:
            break
        name = tag_info["name"]
        if name in vocab_set:
            continue
        if prefix_lower and not name.startswith(prefix_lower):
            continue
        results.append({"name": name, "source": "existing", "usage_count": tag_info["usage_count"]})

    return results


@router.post("/tags/merge")
async def merge_tags_endpoint(req: MergeTagsRequest):
    """Merge source_tag into target_tag (rename/consolidate)."""
    source = req.source_tag.strip().lower()
    target = req.target_tag.strip().lower()
    if not source or not target:
        raise HTTPException(status_code=400, detail="Both source_tag and target_tag are required")
    if source == target:
        raise HTTPException(status_code=400, detail="Source and target tags must be different")

    try:
        driver = get_neo4j()
        with driver.session() as session:
            # Use explicit transaction for atomicity
            with session.begin_transaction() as tx:
                # Find all artifacts tagged with source
                result = tx.run(
                    "MATCH (a:Artifact)-[r:TAGGED_WITH]->(t:Tag {name: $source}) "
                    "RETURN a.id AS aid, a.tags AS tags",
                    source=source,
                )
                updated = 0
                # Collect records first to avoid interleaving reads/writes
                records = list(result)
                for record in records:
                    aid = record["aid"]
                    # Update artifact tags property
                    try:
                        tag_list = json.loads(record["tags"] or "[]")
                    except (json.JSONDecodeError, TypeError):
                        tag_list = []
                    tag_list = [target if t == source else t for t in tag_list]
                    # Deduplicate
                    tag_list = list(dict.fromkeys(tag_list))
                    tx.run(
                        "MATCH (a:Artifact {id: $aid}) SET a.tags = $tags",
                        aid=aid,
                        tags=json.dumps(tag_list),
                    )
                    updated += 1

                # Move relationships: delete source TAGGED_WITH, create target TAGGED_WITH
                tx.run(
                    "MATCH (a:Artifact)-[r:TAGGED_WITH]->(t:Tag {name: $source}) "
                    "DELETE r "
                    "WITH a "
                    "MERGE (t2:Tag {name: $target}) "
                    "MERGE (a)-[:TAGGED_WITH]->(t2)",
                    source=source,
                    target=target,
                )
                # Delete source tag node if no remaining relationships
                tx.run(
                    "MATCH (t:Tag {name: $source}) "
                    "WHERE NOT (t)<-[:TAGGED_WITH]-() "
                    "DELETE t",
                    source=source,
                )
                tx.commit()

        logger.info(f"Merged tag '{source}' → '{target}' ({updated} artifacts updated)")
        return {"status": "success", "source": source, "target": target, "artifacts_updated": updated}
    except Exception as e:
        logger.error(f"Merge tags error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
