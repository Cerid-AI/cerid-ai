# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core ingestion service functions.

Extracted from routers/ingestion.py to eliminate the circular import
between agents/memory.py → routers/ingestion.py. Routers and agents
both import from this service layer.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import config
from deps import get_chroma, get_neo4j, get_redis
from utils import cache, graph
from utils.chunker import chunk_text, make_context_header
from utils.metadata import ai_categorize, extract_metadata
from utils.parsers import parse_file
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion")


def validate_file_path(file_path: str) -> Path:
    """Ensure file_path resolves within the configured archive directory."""
    allowed_root = Path(config.ARCHIVE_PATH).resolve()
    resolved = Path(file_path).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise ValueError(
            f"Path '{file_path}' is outside the allowed archive directory ({allowed_root})."
        )
    return resolved


# ── Private helpers ────────────────────────────────────────────────────────────

def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _check_duplicate(content_hash: str, domain: str) -> dict | None:
    try:
        driver = get_neo4j()
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact {content_hash: $hash})-[:BELONGS_TO]->(d:Domain) "
                "RETURN a.id AS id, a.filename AS filename, d.name AS domain",
                hash=content_hash,
            )
            record = result.single(strict=False)
            if record:
                return {
                    "id": record["id"],
                    "filename": record["filename"],
                    "domain": record["domain"],
                }
    except Exception as e:
        logger.warning(f"Dedup check failed (proceeding with ingest): {e}")
    return None


def _reingest_artifact(
    prev: dict, content: str, domain: str, metadata: dict | None, content_hash: str
) -> dict:
    """Update an existing artifact with new content. Preserves relationships."""
    chroma = get_chroma()
    coll_name = config.collection_name(domain)
    collection = chroma.get_or_create_collection(name=coll_name)
    artifact_id = prev["id"]

    # Delete old chunks from ChromaDB
    old_chunk_ids = json.loads(prev.get("chunk_ids", "[]") or "[]")
    if old_chunk_ids:
        try:
            collection.delete(ids=old_chunk_ids)
        except Exception as e:
            logger.warning(f"Failed to delete old chunks during re-ingest: {e}")

    # Create new chunks with contextual header
    filename = metadata.get("filename", "") if metadata else ""
    sub_cat = metadata.get("sub_category", "") if metadata else ""
    ctx_header = make_context_header(filename=filename, domain=domain, sub_category=sub_cat)
    chunks = chunk_text(
        content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
        context_header=ctx_header,
    )

    # Contextual enrichment — LLM-generated situational summaries per chunk
    if config.ENABLE_CONTEXTUAL_CHUNKS:
        try:
            from utils.contextual import contextualize_chunks
            chunks = contextualize_chunks(chunks, content, metadata)
        except Exception as e:
            logger.warning("Contextual enrichment skipped (re-ingest): %s", e)

    base_meta = {"domain": domain, "artifact_id": artifact_id, "ingested_at": utcnow_iso()}
    if metadata:
        base_meta.update(metadata)

    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # BM25 index
    try:
        from utils.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
    except Exception as e:
        logger.debug(f"BM25 indexing failed during re-ingest (non-blocking): {e}")

    # Compute quality_score for re-ingested content
    _summary = base_meta.get("summary", "")
    _tags = base_meta.get("tags_json", "[]")
    _sub_cat = base_meta.get("sub_category", "")
    _qscore = 0.0
    if _summary and _summary != content[:200]:
        _qscore += 0.20
    try:
        _tag_list = json.loads(_tags) if _tags else []
    except (json.JSONDecodeError, TypeError):
        _tag_list = []
    if _tag_list:
        _qscore += 0.15
    if len(chunks) > 1:
        _qscore += 0.15
    if len(content) > 500:
        _qscore += 0.15
    if domain:
        _qscore += 0.10
    if _sub_cat and _sub_cat != config.DEFAULT_SUB_CATEGORY:
        _qscore += 0.10
    _qscore += 0.15  # dedup passed
    quality_score = round(min(_qscore, 1.0), 2)

    # Update Neo4j artifact (preserves relationships)
    try:
        graph.update_artifact(
            get_neo4j(),
            artifact_id=artifact_id,
            keywords_json=base_meta.get("keywords", "[]"),
            summary=base_meta.get("summary", content[:200]),
            chunk_count=len(chunks),
            chunk_ids_json=json.dumps(chunk_ids),
            content_hash=content_hash,
            quality_score=quality_score,
        )
    except Exception as e:
        logger.error(f"Failed to update artifact in Neo4j during re-ingest: {e}")

    logger.info(f"Re-ingested artifact {artifact_id[:8]} ({base_meta.get('filename', '?')})")
    return {
        "status": "updated",
        "artifact_id": artifact_id,
        "domain": domain,
        "chunks": len(chunks),
        "timestamp": utcnow_iso(),
    }


# ── Public service functions ───────────────────────────────────────────────────

def ingest_content(
    content: str,
    domain: str = "general",
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Core ingest path. Called by REST endpoints, agents, and MCP tool dispatcher."""
    chroma = get_chroma()
    coll_name = config.collection_name(domain)
    collection = chroma.get_or_create_collection(name=coll_name)

    artifact_id = str(uuid.uuid4())
    content_hash = _content_hash(content)

    existing = _check_duplicate(content_hash, domain)
    if existing:
        fname = (metadata or {}).get("filename", "?")
        logger.info(
            f"Duplicate detected: '{fname}' matches "
            f"existing artifact {existing['id']} ('{existing['filename']}' in {existing['domain']})"
        )
        return {
            "status": "duplicate",
            "artifact_id": existing["id"],
            "domain": existing["domain"],
            "chunks": 0,
            "timestamp": utcnow_iso(),
            "duplicate_of": existing["filename"],
        }

    # Semantic deduplication (Pro feature)
    near_dup = None
    try:
        from utils.features import is_feature_enabled

        if is_feature_enabled("semantic_dedup"):
            from utils.dedup import check_semantic_duplicate

            near_dup = check_semantic_duplicate(
                text=content,
                domain=domain,
                chroma_client=get_chroma(),
            )
    except Exception as e:
        logger.debug(f"Semantic dedup check skipped: {e}")

    # Re-ingestion check: same filename, different content
    fname = (metadata or {}).get("filename", "text_input")
    if fname != "text_input":
        try:
            prev = graph.find_artifact_by_filename(get_neo4j(), fname, domain)
            if prev and prev["content_hash"] != content_hash:
                return _reingest_artifact(prev, content, domain, metadata, content_hash)
        except Exception as e:
            logger.warning(f"Re-ingest check failed (proceeding as new): {e}")

    fname_for_header = (metadata or {}).get("filename", "")
    sub_cat_for_header = (metadata or {}).get("sub_category", "")
    ctx_header = make_context_header(
        filename=fname_for_header, domain=domain, sub_category=sub_cat_for_header,
    )
    chunks = chunk_text(
        content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
        context_header=ctx_header,
    )

    # Contextual enrichment — LLM-generated situational summaries per chunk
    if config.ENABLE_CONTEXTUAL_CHUNKS:
        try:
            from utils.contextual import contextualize_chunks
            chunks = contextualize_chunks(chunks, content, metadata)
        except Exception as e:
            logger.warning("Contextual enrichment skipped: %s", e)

    ingested_at = utcnow_iso()
    base_meta = {"domain": domain, "artifact_id": artifact_id, "ingested_at": ingested_at}
    if metadata:
        base_meta.update(metadata)

    # Tag near-duplicate in metadata
    if near_dup:
        base_meta["near_duplicate_of"] = near_dup["artifact_id"]
        base_meta["near_duplicate_similarity"] = str(near_dup["similarity"])

    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # Index for BM25 hybrid search
    try:
        from utils.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
    except Exception as e:
        logger.warning(f"BM25 indexing failed (non-blocking): {e}")

    # Compute quality_score using weighted 4-dimension formula
    from utils.quality import compute_quality_score as _compute_quality

    quality_score = _compute_quality(
        summary=base_meta.get("summary", ""),
        keywords=base_meta.get("keywords_json", "[]"),
        tags=base_meta.get("tags_json", "[]"),
        sub_category=base_meta.get("sub_category", ""),
        default_sub_category=config.DEFAULT_SUB_CATEGORY,
        ingested_at=base_meta.get("ingested_at"),
    )

    artifact_created = False
    try:
        driver = get_neo4j()
        graph.create_artifact(
            driver,
            artifact_id=artifact_id,
            filename=base_meta.get("filename", "text_input"),
            domain=domain,
            keywords_json=base_meta.get("keywords", "[]"),
            summary=base_meta.get("summary", content[:200]),
            chunk_count=len(chunks),
            chunk_ids_json=json.dumps(chunk_ids),
            content_hash=content_hash,
            sub_category=base_meta.get("sub_category", config.DEFAULT_SUB_CATEGORY),
            tags_json=base_meta.get("tags_json", "[]"),
            quality_score=quality_score,
        )
        artifact_created = True
    except Exception as e:
        err_msg = str(e).lower()
        if "constraint" in err_msg and "content_hash" in err_msg:
            logger.info(f"Concurrent duplicate detected via constraint: {base_meta.get('filename', '?')}")
            try:
                collection.delete(ids=chunk_ids)
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up chunks after concurrent duplicate: {cleanup_err}")
            return {
                "status": "duplicate",
                "artifact_id": artifact_id,
                "domain": domain,
                "chunks": 0,
                "timestamp": utcnow_iso(),
                "duplicate_of": "(concurrent)",
            }
        logger.error(f"Neo4j artifact creation failed: {e}")
        try:
            collection.delete(ids=chunk_ids)
        except Exception as ce:
            logger.warning(f"Failed to roll back chunks after Neo4j failure: {ce}")

    try:
        cache.log_event(
            get_redis(),
            event_type="ingest",
            artifact_id=artifact_id,
            domain=domain,
            filename=base_meta.get("filename", "text_input"),
        )
    except Exception as e:
        logger.error(f"Redis log failed: {e}")

    # Discover and create relationships with existing artifacts
    relationships_created = 0
    if artifact_created:
        try:
            relationships_created = graph.discover_relationships(
                driver=get_neo4j(),
                artifact_id=artifact_id,
                filename=base_meta.get("filename", "text_input"),
                domain=domain,
                keywords_json=base_meta.get("keywords", "[]"),
                content=content[:5000],  # limit content scan for performance
            )
        except Exception as e:
            logger.warning(f"Relationship discovery failed (non-blocking): {e}")

    # Fire webhook notification
    try:
        from utils.webhooks import notify_ingestion_complete
        asyncio.get_running_loop().create_task(
            notify_ingestion_complete(artifact_id, domain, base_meta.get("filename", "text_input"), len(chunks))
        )
    except RuntimeError:
        pass  # no running loop (e.g. sync context) — webhook skipped
    except Exception as e:
        logger.debug(f"Webhook notification failed (non-blocking): {e}")

    # Surface related artifacts in response
    related = []
    if relationships_created > 0:
        try:
            found = graph.find_related_artifacts(
                get_neo4j(), artifact_ids=[artifact_id], depth=1, max_results=5,
            )
            related = [
                {"id": r["id"], "filename": r["filename"], "domain": r["domain"],
                 "relationship_type": r.get("relationship_type", "")}
                for r in found
            ]
        except Exception as e:
            logger.debug(f"Related artifacts lookup failed (non-blocking): {e}")

    result = {
        "status": "success",
        "artifact_id": artifact_id,
        "domain": domain,
        "chunks": len(chunks),
        "relationships_created": relationships_created,
        "related": related,
        "timestamp": utcnow_iso(),
    }

    # Include near-duplicate info if detected
    if near_dup:
        result["near_duplicate_of"] = {
            "artifact_id": near_dup["artifact_id"],
            "filename": near_dup["filename"],
            "similarity": near_dup["similarity"],
        }

    return result


async def ingest_file(
    file_path: str,
    domain: str = "",
    sub_category: str = "",
    tags: str = "",
    categorize_mode: str = "",
) -> dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store."""
    validate_file_path(file_path)
    filename = Path(file_path).name
    parsed = parse_file(file_path)
    text = parsed["text"]
    meta = extract_metadata(text, filename, domain or config.DEFAULT_DOMAIN)
    mode = categorize_mode or (
        "manual" if domain and domain in config.DOMAINS else config.CATEGORIZE_MODE
    )
    if mode != "manual" and not domain:
        ai_result = await ai_categorize(text, filename, mode)
        if ai_result.get("suggested_domain"):
            domain = ai_result["suggested_domain"]
            meta["ai_categorized"] = "true"
            meta["categorize_mode"] = mode
        if ai_result.get("keywords"):
            meta["keywords"] = json.dumps(ai_result["keywords"])
        if ai_result.get("summary"):
            meta["summary"] = ai_result["summary"]
        # Sub-category and tags from AI
        if ai_result.get("sub_category") and not sub_category:
            sub_category = ai_result["sub_category"]
        if ai_result.get("tags") and not tags:
            tags = json.dumps(ai_result["tags"])
    if not domain or domain not in config.DOMAINS:
        domain = config.DEFAULT_DOMAIN
    meta["domain"] = domain
    meta["sub_category"] = sub_category or config.DEFAULT_SUB_CATEGORY
    # Normalize tags: accept JSON array string or comma-separated
    if tags:
        if tags.startswith("["):
            meta["tags_json"] = tags
        else:
            meta["tags_json"] = json.dumps([t.strip().lower() for t in tags.split(",") if t.strip()])
    meta["file_type"] = parsed.get("file_type", "")
    if parsed.get("page_count") is not None:
        meta["page_count"] = parsed["page_count"]
    result = ingest_content(text, domain, metadata=meta)
    result["filename"] = filename
    result["categorize_mode"] = mode
    result["metadata"] = {
        k: v for k, v in meta.items()
        if k in ("filename", "domain", "sub_category", "keywords", "summary", "tags_json", "file_type", "estimated_tokens")
    }
    return result
