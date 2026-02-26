"""Ingestion endpoints and core ingest service functions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from deps import get_chroma, get_neo4j, get_redis
from utils import cache, graph
from utils.chunker import chunk_text
from utils.metadata import ai_categorize, extract_metadata
from utils.parsers import parse_file
from utils.time import utcnow, utcnow_iso

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Concurrency limiter for ingestion (Phase 4C.3)
_ingest_semaphore = asyncio.Semaphore(3)


def _validate_file_path(file_path: str) -> Path:
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


def _check_duplicate(content_hash: str, domain: str) -> Optional[Dict]:
    try:
        driver = get_neo4j()
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact {content_hash: $hash})-[:BELONGS_TO]->(d:Domain) "
                "RETURN a.id AS id, a.filename AS filename, d.name AS domain",
                hash=content_hash,
            )
            record = result.single()
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
    prev: Dict, content: str, domain: str, metadata: Optional[Dict], content_hash: str
) -> Dict:
    """Update an existing artifact with new content. Preserves relationships."""
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    artifact_id = prev["id"]

    # Delete old chunks from ChromaDB
    old_chunk_ids = json.loads(prev.get("chunk_ids", "[]") or "[]")
    if old_chunk_ids:
        try:
            collection.delete(ids=old_chunk_ids)
        except Exception as e:
            logger.warning(f"Failed to delete old chunks during re-ingest: {e}")

    # Create new chunks
    chunks = chunk_text(content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP)
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
    except Exception:
        pass

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
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Core ingest path. Called by REST endpoints, agents.py triage, and mcp_sse execute_tool."""
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)

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

    # Phase 8B: Semantic deduplication (Pro feature)
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

    # Re-ingestion check: same filename, different content (Phase 4C.3)
    fname = (metadata or {}).get("filename", "text_input")
    if fname != "text_input":
        try:
            prev = graph.find_artifact_by_filename(get_neo4j(), fname, domain)
            if prev and prev["content_hash"] != content_hash:
                return _reingest_artifact(prev, content, domain, metadata, content_hash)
        except Exception as e:
            logger.warning(f"Re-ingest check failed (proceeding as new): {e}")

    chunks = chunk_text(content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP)
    ingested_at = utcnow_iso()
    base_meta = {"domain": domain, "artifact_id": artifact_id, "ingested_at": ingested_at}
    if metadata:
        base_meta.update(metadata)

    # Phase 8B: tag near-duplicate in metadata
    if near_dup:
        base_meta["near_duplicate_of"] = near_dup["artifact_id"]
        base_meta["near_duplicate_similarity"] = str(near_dup["similarity"])

    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # Index for BM25 hybrid search (Phase 4B.1)
    try:
        from utils.bm25 import index_chunks
        index_chunks(domain, chunk_ids, chunks)
    except Exception as e:
        logger.warning(f"BM25 indexing failed (non-blocking): {e}")

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
        )
        artifact_created = True
    except Exception as e:
        err_msg = str(e).lower()
        if "constraint" in err_msg and "content_hash" in err_msg:
            logger.info(f"Concurrent duplicate detected via constraint: {base_meta.get('filename', '?')}")
            try:
                collection.delete(ids=chunk_ids)
            except Exception:
                pass
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
        cache.log_event(
            get_redis(),
            event_type="ingest",
            artifact_id=artifact_id,
            domain=domain,
            filename=base_meta.get("filename", "text_input"),
        )
    except Exception as e:
        logger.error(f"Redis log failed: {e}")

    # Discover and create relationships with existing artifacts (Phase 4B.2)
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

    # Fire webhook notification (Phase 4C.4)
    try:
        from utils.webhooks import notify_ingestion_complete
        asyncio.get_running_loop().create_task(
            notify_ingestion_complete(artifact_id, domain, base_meta.get("filename", "text_input"), len(chunks))
        )
    except RuntimeError:
        pass  # no running loop (e.g. sync context) — webhook skipped
    except Exception:
        pass

    # Surface related artifacts in response (Phase 4C.2)
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
        except Exception:
            pass

    result = {
        "status": "success",
        "artifact_id": artifact_id,
        "domain": domain,
        "chunks": len(chunks),
        "relationships_created": relationships_created,
        "related": related,
        "timestamp": utcnow_iso(),
    }

    # Phase 8B: Include near-duplicate info if detected
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
) -> Dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store."""
    _validate_file_path(file_path)
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
        # Phase 8C: sub-category and tags from AI
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


# ── Pydantic models ────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    content: str
    domain: str = "general"


class IngestFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    sub_category: str = ""
    tags: str = ""
    categorize_mode: str = ""


class FeedbackIngestRequest(BaseModel):
    user_message: str
    assistant_response: str
    model: str = ""
    conversation_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_endpoint(req: IngestRequest):
    async with _ingest_semaphore:
        result = ingest_content(req.content, req.domain)
    try:
        from utils.query_cache import invalidate_all
        invalidate_all()
    except Exception:
        pass
    return result


@router.post("/ingest_file")
async def ingest_file_endpoint(req: IngestFileRequest):
    try:
        async with _ingest_semaphore:
            result = await ingest_file(
                file_path=req.file_path,
                domain=req.domain,
                sub_category=req.sub_category,
                tags=req.tags,
                categorize_mode=req.categorize_mode,
            )
        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception:
            pass
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ingest file error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/feedback")
async def ingest_feedback_endpoint(req: FeedbackIngestRequest):
    """Ingest a chat turn into the conversations domain for the feedback loop."""
    # Backend gate: reject if feedback loop is disabled server-side
    if not config.ENABLE_FEEDBACK_LOOP:
        return {"status": "skipped", "reason": "Feedback loop disabled (ENABLE_FEEDBACK_LOOP=false)"}

    try:
        convo_prefix = req.conversation_id[:8] if req.conversation_id else "unknown"
        timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{convo_prefix}_{timestamp}"
        content = (
            f"User: {req.user_message}\n\n"
            f"Assistant ({req.model}): {req.assistant_response}"
        )
        metadata = {
            "filename": filename,
            "conversation_id": req.conversation_id,
            "model": req.model,
            "summary": req.user_message[:200],
        }
        async with _ingest_semaphore:
            result = ingest_content(content, "conversations", metadata=metadata)

        # Log with conversation_id for audit trail
        try:
            cache.log_event(
                get_redis(),
                event_type="feedback",
                artifact_id=result.get("artifact_id", ""),
                domain="conversations",
                filename=filename,
                conversation_id=req.conversation_id,
            )
        except Exception:
            pass

        try:
            from utils.query_cache import invalidate_all
            invalidate_all()
        except Exception:
            pass

        # Trigger hallucination check if enabled (async, non-blocking)
        if config.ENABLE_HALLUCINATION_CHECK and result.get("status") == "success":
            try:
                from agents.hallucination import check_hallucinations
                asyncio.get_running_loop().create_task(
                    check_hallucinations(
                        response_text=req.assistant_response,
                        conversation_id=req.conversation_id,
                        chroma_client=get_chroma(),
                        neo4j_driver=get_neo4j(),
                        redis_client=get_redis(),
                    )
                )
            except RuntimeError:
                pass  # no running loop

        # Log conversation metrics if tokens provided
        if req.input_tokens or req.output_tokens:
            try:
                from agents.audit import log_conversation_metrics
                log_conversation_metrics(
                    get_redis(),
                    conversation_id=req.conversation_id,
                    model=req.model,
                    input_tokens=req.input_tokens,
                    output_tokens=req.output_tokens,
                    latency_ms=req.latency_ms,
                )
            except Exception:
                pass

        return result
    except Exception as e:
        logger.error(f"Feedback ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest_log")
async def ingest_log_endpoint(limit: int = Query(50, ge=1, le=500)):
    try:
        return cache.get_log(get_redis(), limit=limit)
    except Exception as e:
        logger.error(f"Ingest log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
