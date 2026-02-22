# Phase 4A: Modular Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split `src/mcp/main.py` (1,321 lines) into FastAPI `APIRouter` modules with zero behavior change.

**Architecture:** `deps.py` holds lazy DB singletons (moved from main.py). Service functions co-locate with their endpoint routers. `mcp_sse.py` imports public service functions from sibling routers — one-way import graph, no circular deps. `main.py` becomes ~100 lines of app wiring.

**Tech Stack:** FastAPI APIRouter, Pydantic v2, Python contextlib lifespan (replaces deprecated `@app.on_event`)

---

## Resulting File Structure

```
src/mcp/
├── main.py              (~100 lines) — was 1,321 lines
├── deps.py              (new) — DB connection singletons
└── routers/
    ├── __init__.py      (new, empty)
    ├── health.py        (new) — /health, /collections, /stats
    ├── query.py         (new) — /query
    ├── ingestion.py     (new) — /ingest, /ingest_file, /ingest_log
    ├── artifacts.py     (new) — /artifacts, /recategorize
    ├── agents.py        (new) — /agent/*
    └── mcp_sse.py       (new) — /mcp/sse, /mcp/messages, MCP_TOOLS
```

---

## Task 1: Create `deps.py`

**Files:**
- Create: `src/mcp/deps.py`

**Step 1: Create the file**

`src/mcp/deps.py`:
```python
"""Database connection dependencies — lazy singletons shared across routers."""
from __future__ import annotations

import logging

import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from neo4j import GraphDatabase

import config

logger = logging.getLogger("ai-companion")

_chroma = None
_redis = None
_neo4j = None


def get_chroma() -> chromadb.HttpClient:
    global _chroma
    if _chroma is None:
        host = config.CHROMA_URL.replace("http://", "").split(":")[0]
        port = int(config.CHROMA_URL.split(":")[-1])
        _chroma = chromadb.HttpClient(
            host=host, port=port, settings=ChromaSettings(anonymized_telemetry=False)
        )
        _chroma.heartbeat()
        logger.info("ChromaDB connected")
    return _chroma


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            config.REDIS_URL, decode_responses=True, socket_connect_timeout=5
        )
        _redis.ping()
        logger.info("Redis connected")
    return _redis


def get_neo4j():
    global _neo4j
    if _neo4j is None:
        _neo4j = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )
        _neo4j.verify_connectivity()
        logger.info("Neo4j connected")
    return _neo4j


def close_neo4j():
    """Called from main.py lifespan on shutdown."""
    global _neo4j
    if _neo4j:
        _neo4j.close()
        _neo4j = None
        logger.info("Neo4j connection closed")
```

**Step 2: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/deps.py && echo "OK"
```
Expected: `OK`

---

## Task 2: Create `routers/__init__.py` and `routers/health.py`

**Files:**
- Create: `src/mcp/routers/__init__.py`
- Create: `src/mcp/routers/health.py`

**Step 1: Create `routers/__init__.py`** (empty)

```python
```

**Step 2: Create `routers/health.py`**

```python
"""Health check and collection listing endpoints."""
from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter

from deps import get_chroma, get_neo4j, get_redis

router = APIRouter()
logger = logging.getLogger("ai-companion")


def health_check() -> Dict:
    """Public — also called by mcp_sse.py execute_tool."""
    status = {"chromadb": "unknown", "redis": "unknown", "neo4j": "unknown"}
    try:
        get_chroma()
        status["chromadb"] = "connected"
    except Exception:
        status["chromadb"] = "error"
    try:
        get_redis()
        status["redis"] = "connected"
    except Exception:
        status["redis"] = "error"
    try:
        get_neo4j()
        status["neo4j"] = "connected"
    except Exception:
        status["neo4j"] = "error"
    return {
        "status": "healthy" if all(v == "connected" for v in status.values()) else "degraded",
        "services": status,
    }


def list_collections() -> Dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


@router.get("/health")
def health_check_endpoint():
    return health_check()


@router.get("/collections")
def list_collections_endpoint():
    return list_collections()


@router.get("/stats")
def stats_endpoint():
    return list_collections()
```

**Step 3: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/routers/health.py && echo "OK"
```
Expected: `OK`

---

## Task 3: Create `routers/query.py`

**Files:**
- Create: `src/mcp/routers/query.py`

**Step 1: Create the file**

```python
"""Query endpoint and query_knowledge service function."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict

from fastapi import APIRouter
from pydantic import BaseModel

import config
from deps import get_chroma

router = APIRouter()
logger = logging.getLogger("ai-companion")


def query_knowledge(query: str, domain: str = "general", top_k: int = 3) -> Dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "distances", "metadatas"],
    )
    timestamp = datetime.utcnow().isoformat()
    if not results.get("documents") or not results["documents"][0]:
        return {"context": "", "sources": [], "confidence": 0.0, "timestamp": timestamp}

    docs = results["documents"][0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    sources = []
    for i, doc in enumerate(docs):
        dist = distances[i] if i < len(distances) else 1.0
        relevance = max(0.0, min(1.0, 1.0 - dist))
        meta = metadatas[i] if i < len(metadatas) else {}
        sources.append({
            "content": doc[:200],
            "relevance": round(relevance, 3),
            "artifact_id": meta.get("artifact_id", ""),
            "filename": meta.get("filename", ""),
            "domain": meta.get("domain", domain),
            "chunk_index": meta.get("chunk_index", 0),
        })

    TOKEN_BUDGET_CHARS = 14000
    context_parts = []
    char_count = 0
    for doc in docs:
        if char_count + len(doc) > TOKEN_BUDGET_CHARS:
            remaining = TOKEN_BUDGET_CHARS - char_count
            if remaining > 200:
                context_parts.append(doc[:remaining] + "\n[...truncated for token budget...]")
            break
        context_parts.append(doc)
        char_count += len(doc)

    context = "\n\n".join(context_parts)
    avg_relevance = sum(s["relevance"] for s in sources) / len(sources) if sources else 0.0

    return {
        "context": context,
        "sources": sources,
        "confidence": round(avg_relevance, 3),
        "timestamp": timestamp,
    }


class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = 3


@router.post("/query")
async def query_endpoint(req: QueryRequest):
    return query_knowledge(req.query, req.domain, req.top_k)
```

**Step 2: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/routers/query.py && echo "OK"
```
Expected: `OK`

---

## Task 4: Create `routers/ingestion.py`

This is the largest router — contains `ingest_content` and `ingest_file` which are also imported by `agents.py` and `mcp_sse.py`.

**Files:**
- Create: `src/mcp/routers/ingestion.py`

**Step 1: Create the file**

```python
"""Ingestion endpoints and core ingest service functions."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
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

router = APIRouter()
logger = logging.getLogger("ai-companion")


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
            "timestamp": datetime.utcnow().isoformat(),
            "duplicate_of": existing["filename"],
        }

    chunks = chunk_text(content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP)
    base_meta = {"domain": domain, "artifact_id": artifact_id}
    if metadata:
        base_meta.update(metadata)

    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

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
        )
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
                "timestamp": datetime.utcnow().isoformat(),
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

    return {
        "status": "success",
        "artifact_id": artifact_id,
        "domain": domain,
        "chunks": len(chunks),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def ingest_file(
    file_path: str,
    domain: str = "",
    tags: str = "",
    categorize_mode: str = "",
) -> Dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store."""
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
    if not domain or domain not in config.DOMAINS:
        domain = config.DEFAULT_DOMAIN
    meta["domain"] = domain
    if tags:
        meta["tags"] = tags
    meta["file_type"] = parsed.get("file_type", "")
    if parsed.get("page_count") is not None:
        meta["page_count"] = parsed["page_count"]
    result = ingest_content(text, domain, metadata=meta)
    result["filename"] = filename
    result["categorize_mode"] = mode
    result["metadata"] = {
        k: v for k, v in meta.items()
        if k in ("filename", "domain", "keywords", "summary", "tags", "file_type", "estimated_tokens")
    }
    return result


# ── Pydantic models ────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    content: str
    domain: str = "general"


class IngestFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    tags: str = ""
    categorize_mode: str = ""


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_endpoint(req: IngestRequest):
    return ingest_content(req.content, req.domain)


@router.post("/ingest_file")
async def ingest_file_endpoint(req: IngestFileRequest):
    try:
        return await ingest_file(
            file_path=req.file_path,
            domain=req.domain,
            tags=req.tags,
            categorize_mode=req.categorize_mode,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ingest file error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest_log")
async def ingest_log_endpoint(limit: int = Query(50, ge=1, le=500)):
    try:
        return cache.get_log(get_redis(), limit=limit)
    except Exception as e:
        logger.error(f"Ingest log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 2: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/routers/ingestion.py && echo "OK"
```
Expected: `OK`

---

## Task 5: Create `routers/artifacts.py`

**Files:**
- Create: `src/mcp/routers/artifacts.py`

**Step 1: Create the file**

```python
"""Artifact listing and recategorization endpoints."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from deps import get_chroma, get_neo4j, get_redis
from utils import cache, graph

router = APIRouter()
logger = logging.getLogger("ai-companion")


def recategorize(artifact_id: str, new_domain: str, tags: str = "") -> Dict:
    """Public — also called by mcp_sse.py execute_tool."""
    if new_domain not in config.DOMAINS:
        raise ValueError(f"Invalid domain: {new_domain}. Valid: {config.DOMAINS}")

    driver = get_neo4j()
    chroma = get_chroma()

    artifact = graph.get_artifact(driver, artifact_id)
    if not artifact:
        raise ValueError(f"Artifact not found: {artifact_id}")

    old_domain = artifact["domain"]
    if old_domain == new_domain:
        raise ValueError(f"Artifact already in domain '{new_domain}'")

    chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
    if not chunk_ids:
        raise ValueError(f"No chunk IDs found for artifact {artifact_id}")

    source_collection = chroma.get_or_create_collection(
        name=f"domain_{old_domain.replace(' ', '_').lower()}"
    )
    fetched = source_collection.get(ids=chunk_ids, include=["documents", "metadatas"])

    if not fetched["ids"]:
        raise ValueError(f"No chunks found in ChromaDB for artifact {artifact_id}")

    dest_collection = chroma.get_or_create_collection(
        name=f"domain_{new_domain.replace(' ', '_').lower()}"
    )
    updated_metadatas = []
    for meta in fetched["metadatas"]:
        meta = dict(meta)
        meta["domain"] = new_domain
        meta["recategorized_at"] = datetime.utcnow().isoformat()
        if tags:
            meta["tags"] = tags
        updated_metadatas.append(meta)

    dest_collection.add(
        ids=fetched["ids"],
        documents=fetched["documents"],
        metadatas=updated_metadatas,
    )
    source_collection.delete(ids=chunk_ids)

    domains = graph.recategorize_artifact(driver, artifact_id, new_domain)

    try:
        cache.log_event(
            get_redis(),
            event_type="recategorize",
            artifact_id=artifact_id,
            domain=new_domain,
            filename=artifact.get("filename", ""),
            extra={"old_domain": old_domain},
        )
    except Exception as e:
        logger.error(f"Redis log failed: {e}")

    return {
        "status": "success",
        "artifact_id": artifact_id,
        "old_domain": domains["old_domain"],
        "new_domain": domains["new_domain"],
        "chunks_moved": len(chunk_ids),
    }


class RecategorizeRequest(BaseModel):
    artifact_id: str
    new_domain: str
    tags: str = ""


@router.get("/artifacts")
async def list_artifacts_endpoint(
    domain: Optional[str] = Query(None, description="Filter by domain"),
    limit: int = Query(50, ge=1, le=500),
):
    try:
        driver = get_neo4j()
        return graph.list_artifacts(driver, domain=domain, limit=limit)
    except Exception as e:
        logger.error(f"List artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recategorize")
async def recategorize_endpoint(req: RecategorizeRequest):
    try:
        return recategorize(req.artifact_id, req.new_domain, req.tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Recategorize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 2: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/routers/artifacts.py && echo "OK"
```
Expected: `OK`

---

## Task 6: Create `routers/agents.py`

**Files:**
- Create: `src/mcp/routers/agents.py`

**Step 1: Create the file**

```python
"""Agent endpoints — thin wrappers over agent modules."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import get_chroma, get_neo4j, get_redis
from routers.ingestion import ingest_content

router = APIRouter()
logger = logging.getLogger("ai-companion")


class AgentQueryRequest(BaseModel):
    query: str
    domains: Optional[List[str]] = None
    top_k: int = 10
    use_reranking: bool = True


class TriageFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    categorize_mode: str = ""
    tags: str = ""


class TriageBatchRequest(BaseModel):
    files: List[Dict[str, str]]
    default_mode: str = ""


class RectifyRequest(BaseModel):
    checks: Optional[List[str]] = None
    auto_fix: bool = False
    stale_days: int = 90


class AuditRequest(BaseModel):
    reports: Optional[List[str]] = None
    hours: int = 24


class MaintenanceRequest(BaseModel):
    actions: Optional[List[str]] = None
    stale_days: int = 90
    auto_purge: bool = False


@router.post("/agent/query")
async def agent_query_endpoint(req: AgentQueryRequest):
    try:
        from agents.query_agent import agent_query
        return await agent_query(
            query=req.query,
            domains=req.domains,
            top_k=req.top_k,
            use_reranking=req.use_reranking,
            chroma_client=get_chroma(),
            redis_client=get_redis(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Agent query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage")
async def triage_file_endpoint(req: TriageFileRequest):
    try:
        from agents.triage import triage_file
        triage_result = await triage_file(
            file_path=req.file_path,
            domain=req.domain,
            categorize_mode=req.categorize_mode,
            tags=req.tags,
        )
        if triage_result.get("status") == "error":
            raise HTTPException(status_code=400, detail=triage_result.get("error", "Triage failed"))
        result = ingest_content(
            triage_result["parsed_text"],
            triage_result["domain"],
            metadata=triage_result["metadata"],
        )
        result["filename"] = triage_result["filename"]
        result["categorize_mode"] = triage_result.get("categorize_mode", "")
        result["triage_status"] = triage_result["status"]
        result["is_structured"] = triage_result.get("is_structured", False)
        return result
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/triage/batch")
async def triage_batch_endpoint(req: TriageBatchRequest):
    try:
        from agents.triage import triage_batch
        triage_results = await triage_batch(
            files=req.files,
            default_mode=req.default_mode,
        )
        final_results = []
        for triage_result in triage_results:
            if triage_result.get("status") == "error":
                final_results.append({
                    "filename": triage_result.get("filename", ""),
                    "status": "error",
                    "error": triage_result.get("error", ""),
                })
                continue
            try:
                result = ingest_content(
                    triage_result["parsed_text"],
                    triage_result["domain"],
                    metadata=triage_result["metadata"],
                )
                result["filename"] = triage_result["filename"]
                result["triage_status"] = triage_result["status"]
                final_results.append(result)
            except Exception as e:
                final_results.append({
                    "filename": triage_result.get("filename", ""),
                    "status": "error",
                    "error": str(e),
                })
        succeeded = sum(1 for r in final_results if r.get("status") == "success")
        failed = sum(1 for r in final_results if r.get("status") == "error")
        duplicates = sum(1 for r in final_results if r.get("status") == "duplicate")
        return {
            "total": len(final_results),
            "succeeded": succeeded,
            "failed": failed,
            "duplicates": duplicates,
            "results": final_results,
        }
    except Exception as e:
        logger.error(f"Batch triage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/rectify")
async def rectify_endpoint(req: RectifyRequest):
    try:
        from agents.rectify import rectify
        return await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=req.checks,
            auto_fix=req.auto_fix,
            stale_days=req.stale_days,
        )
    except Exception as e:
        logger.error(f"Rectify error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/audit")
async def audit_endpoint(req: AuditRequest):
    try:
        from agents.audit import audit
        return await audit(
            redis_client=get_redis(),
            reports=req.reports,
            hours=req.hours,
        )
    except Exception as e:
        logger.error(f"Audit error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/maintain")
async def maintain_endpoint(req: MaintenanceRequest):
    try:
        from agents.maintenance import maintain
        return await maintain(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            actions=req.actions,
            stale_days=req.stale_days,
            auto_purge=req.auto_purge,
        )
    except Exception as e:
        logger.error(f"Maintenance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 2: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/routers/agents.py && echo "OK"
```
Expected: `OK`

---

## Task 7: Create `routers/mcp_sse.py`

This is the largest router. It contains `MCP_TOOLS`, `execute_tool`, `build_response`, and all `/mcp/*` endpoints.

**Files:**
- Create: `src/mcp/routers/mcp_sse.py`

**Step 1: Create the file**

```python
"""MCP SSE transport — responses go through the SSE stream, not HTTP response body."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

import config
from deps import get_chroma, get_neo4j, get_redis
from routers.artifacts import recategorize
from routers.health import health_check, list_collections
from routers.ingestion import ingest_content, ingest_file
from routers.query import query_knowledge
from utils import graph

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Session message queues (shared between GET /mcp/sse and POST /mcp/messages)
_sessions: Dict[str, asyncio.Queue] = {}


def clear_sessions():
    """Called from main.py lifespan on shutdown."""
    _sessions.clear()
    logger.info("MCP sessions cleared on shutdown")


# ── MCP Tool Definitions ───────────────────────────────────────────────────────

MCP_TOOLS = [
    {
        "name": "pkb_query",
        "description": "Query the personal knowledge base for relevant context",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "domain": {
                    "type": "string",
                    "description": f"Knowledge domain ({', '.join(config.DOMAINS)})",
                    "default": "general",
                },
                "top_k": {"type": "integer", "description": "Number of results", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "pkb_ingest",
        "description": "Ingest text content into the knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to ingest"},
                "domain": {
                    "type": "string",
                    "description": f"Knowledge domain ({', '.join(config.DOMAINS)})",
                    "default": "general",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "pkb_ingest_file",
        "description": "Ingest a file from the archive into the knowledge base with metadata extraction",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to file (e.g. /archive/coding/script.py)",
                },
                "domain": {
                    "type": "string",
                    "description": f"Knowledge domain ({', '.join(config.DOMAINS)}). Empty for auto-detect.",
                    "default": "",
                },
                "categorize_mode": {
                    "type": "string",
                    "description": "Categorization tier: manual, smart, or pro",
                    "default": "",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "pkb_health",
        "description": "Check knowledge base service health",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pkb_collections",
        "description": "List available knowledge base collections",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pkb_agent_query",
        "description": "Multi-domain knowledge base search with intelligent reranking and context assembly",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"List of domains to search ({', '.join(config.DOMAINS)}). Empty for all domains.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results per domain",
                    "default": 10,
                },
                "use_reranking": {
                    "type": "boolean",
                    "description": "Enable intelligent reranking",
                    "default": True,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "pkb_artifacts",
        "description": "List ingested artifacts in the knowledge base, optionally filtered by domain",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": f"Filter by domain ({', '.join(config.DOMAINS)}). Empty for all.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of artifacts to return",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "pkb_recategorize",
        "description": "Move an artifact from one domain to another in the knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "UUID of the artifact to move",
                },
                "new_domain": {
                    "type": "string",
                    "description": f"Target domain ({', '.join(config.DOMAINS)})",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional tags to apply after recategorization",
                    "default": "",
                },
            },
            "required": ["artifact_id", "new_domain"],
        },
    },
    {
        "name": "pkb_triage",
        "description": "Triage a file through the intelligent ingestion pipeline with LangGraph routing",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to file (e.g. /archive/inbox/report.pdf)",
                },
                "domain": {
                    "type": "string",
                    "description": f"Target domain ({', '.join(config.DOMAINS)}). Empty for auto-detect.",
                    "default": "",
                },
                "categorize_mode": {
                    "type": "string",
                    "description": "Categorization tier: manual, smart, or pro",
                    "default": "",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "pkb_rectify",
        "description": "Run knowledge base health checks: find duplicates, stale artifacts, orphaned chunks, and domain distribution",
        "inputSchema": {
            "type": "object",
            "properties": {
                "checks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Checks to run: duplicates, stale, orphans, distribution. Empty for all.",
                },
                "auto_fix": {
                    "type": "boolean",
                    "description": "Automatically resolve duplicates and clean orphans",
                    "default": False,
                },
                "stale_days": {
                    "type": "integer",
                    "description": "Days threshold for stale artifact detection",
                    "default": 90,
                },
            },
        },
    },
    {
        "name": "pkb_audit",
        "description": "Generate audit reports: activity summary, ingestion stats, cost estimates, and query patterns",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reports": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Reports to generate: activity, ingestion, costs, queries. Empty for all.",
                },
                "hours": {
                    "type": "integer",
                    "description": "Time window in hours for activity report",
                    "default": 24,
                },
            },
        },
    },
    {
        "name": "pkb_maintain",
        "description": "Run maintenance routines: system health check, stale artifact detection, collection analysis, orphan cleanup",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Actions to run: health, stale, collections, orphans. Empty for all.",
                },
                "stale_days": {
                    "type": "integer",
                    "description": "Days threshold for stale artifact detection",
                    "default": 90,
                },
                "auto_purge": {
                    "type": "boolean",
                    "description": "Automatically purge stale artifacts and orphaned chunks",
                    "default": False,
                },
            },
        },
    },
]


# ── Tool execution ─────────────────────────────────────────────────────────────

async def execute_tool(name: str, arguments: Dict) -> Any:
    if name == "pkb_query":
        return query_knowledge(**arguments)
    elif name == "pkb_ingest":
        return ingest_content(arguments.get("content", ""), arguments.get("domain", "general"))
    elif name == "pkb_ingest_file":
        return await ingest_file(**arguments)
    elif name == "pkb_health":
        return health_check()
    elif name == "pkb_collections":
        return list_collections()
    elif name == "pkb_agent_query":
        from agents.query_agent import agent_query
        return await agent_query(
            query=arguments.get("query", ""),
            domains=arguments.get("domains"),
            top_k=arguments.get("top_k", 10),
            use_reranking=arguments.get("use_reranking", True),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
        )
    elif name == "pkb_artifacts":
        domain = arguments.get("domain", "") or None
        limit = arguments.get("limit", 50)
        driver = get_neo4j()
        return graph.list_artifacts(driver, domain=domain, limit=limit)
    elif name == "pkb_recategorize":
        return recategorize(
            artifact_id=arguments["artifact_id"],
            new_domain=arguments["new_domain"],
            tags=arguments.get("tags", ""),
        )
    elif name == "pkb_triage":
        from agents.triage import triage_file
        triage_result = await triage_file(
            file_path=arguments.get("file_path", ""),
            domain=arguments.get("domain", ""),
            categorize_mode=arguments.get("categorize_mode", ""),
        )
        if triage_result.get("status") == "error":
            return {"status": "error", "error": triage_result.get("error", "Unknown error")}
        result = ingest_content(
            triage_result["parsed_text"],
            triage_result["domain"],
            metadata=triage_result["metadata"],
        )
        result["filename"] = triage_result["filename"]
        result["categorize_mode"] = triage_result.get("categorize_mode", "")
        result["triage_status"] = triage_result["status"]
        return result
    elif name == "pkb_rectify":
        from agents.rectify import rectify
        return await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=arguments.get("checks"),
            auto_fix=arguments.get("auto_fix", False),
            stale_days=arguments.get("stale_days", 90),
        )
    elif name == "pkb_audit":
        from agents.audit import audit
        return await audit(
            redis_client=get_redis(),
            reports=arguments.get("reports"),
            hours=arguments.get("hours", 24),
        )
    elif name == "pkb_maintain":
        from agents.maintenance import maintain
        return await maintain(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            actions=arguments.get("actions"),
            stale_days=arguments.get("stale_days", 90),
            auto_purge=arguments.get("auto_purge", False),
        )
    raise ValueError(f"Unknown tool: {name}")


async def build_response(msg_id, method: str, params: dict) -> dict:
    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "cerid-ai-companion", "version": "1.0.0"},
            },
        }
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS}}
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result = await execute_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            }
        except Exception as e:
            logger.error(f"Tool call error {tool_name}: {e}")
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Unknown: {method}"},
        }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.head("/mcp/sse")
async def mcp_sse_head():
    return Response(status_code=200, headers={"Content-Type": "text/event-stream"})


@router.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """SSE endpoint — responses to POSTs come through here."""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sessions[session_id] = queue
    logger.info(f"[MCP] SSE opened: {session_id}")

    async def event_stream():
        try:
            endpoint_url = f"http://ai-companion-mcp:8888/mcp/messages?sessionId={session_id}"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"
            logger.info(f"[MCP] Sent endpoint: {endpoint_url}")
            count = 0
            while True:
                if await request.is_disconnected():
                    break
                if count % 3 == 0:
                    ping = {
                        "jsonrpc": "2.0",
                        "method": "ping",
                        "params": {},
                        "id": f"server-ping-{count}",
                    }
                    await queue.put(ping)
                    logger.debug(f"[MCP] Sent keep-alive ping: {session_id}")
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=8.0)
                    data = json.dumps(msg)
                    yield f"event: message\ndata: {data}\n\n"
                    logger.info(f"[MCP] Sent via SSE: {msg.get('id', 'notification')}")
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                count += 1
        finally:
            _sessions.pop(session_id, None)
            logger.info(f"[MCP] SSE closed: {session_id}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Accept, Cache-Control, Content-Type",
            "Transfer-Encoding": "chunked",
        },
    )


@router.post("/mcp/sse")
async def mcp_sse_post(request: Request):
    """Handle probes to /mcp/sse."""
    return Response(status_code=200, content="", media_type="text/plain")


@router.post("/mcp/messages")
async def mcp_messages(request: Request):
    """Receive JSON-RPC, send response via SSE stream."""
    session_id = request.query_params.get("sessionId")
    try:
        body = await request.body()
        body_text = body.decode("utf-8").strip()
        if not body_text or body_text == "{}":
            return Response(status_code=202)
        msg = json.loads(body_text)
    except Exception as e:
        logger.error(f"[MCP] Parse error: {e}")
        return Response(status_code=400, content=str(e))

    method = msg.get("method", "")
    params = msg.get("params", {})
    msg_id = msg.get("id")
    logger.info(f"[MCP] Received: {method} (id={msg_id}, session={session_id})")

    if method in ("initialized", "notifications/initialized"):
        logger.info("[MCP] Client initialized")
        return Response(status_code=202)

    response = await build_response(msg_id, method, params)

    if session_id and session_id in _sessions:
        await _sessions[session_id].put(response)
        logger.info(f"[MCP] Queued response for SSE: {method}")
        return Response(status_code=202)
    else:
        logger.warning(f"[MCP] No session, returning directly: {method}")
        return Response(
            status_code=200,
            content=json.dumps(response),
            media_type="application/json",
        )
```

**Step 2: Syntax check**

```bash
docker exec ai-companion-mcp python -m py_compile /app/routers/mcp_sse.py && echo "OK"
```
Expected: `OK`

---

## Task 8: Replace `main.py`

**⚠️ This is the atomic cutover step.** All router files must exist and pass syntax checks before this.

**Files:**
- Modify: `src/mcp/main.py` (full replacement)

**Step 1: Verify all router files exist**

```bash
ls /Users/sunrunner/Develop/cerid-ai/src/mcp/routers/
```
Expected: `__init__.py  agents.py  artifacts.py  health.py  ingestion.py  mcp_sse.py  query.py`

**Step 2: Replace `main.py` with the new wired version**

Full content of new `src/mcp/main.py`:

```python
"""
AI Companion MCP Server - MCP SSE Transport + Ingestion Pipeline
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deps import close_neo4j, get_neo4j
from routers import agents, artifacts, health, ingestion, mcp_sse, query
from utils import graph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai-companion")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize Neo4j schema
    try:
        graph.init_schema(get_neo4j())
    except Exception as e:
        logger.warning(f"Neo4j schema init failed (will retry on first use): {e}")

    yield

    # Shutdown: close DB driver, clear MCP sessions
    close_neo4j()
    mcp_sse.clear_sessions()


app = FastAPI(
    title="AI Companion MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(query.router)
app.include_router(ingestion.router)
app.include_router(artifacts.router)
app.include_router(agents.router)
app.include_router(mcp_sse.router)


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}
```

**Step 3: Syntax check the new main.py**

```bash
docker exec ai-companion-mcp python -m py_compile /app/main.py && echo "OK"
```
Expected: `OK`

---

## Task 9: Rebuild Container + Smoke Test All Endpoints

**Step 1: Rebuild and restart**

```bash
cd /Users/sunrunner/Develop/cerid-ai/src/mcp && docker compose up -d --build
```

Wait ~30 seconds for startup. Then:

```bash
docker logs ai-companion-mcp --tail 20
```
Expected: No errors. Should see `Neo4j connected`, `ChromaDB connected` or startup messages.

**Step 2: Health check**

```bash
curl -s http://localhost:8888/health | python3 -m json.tool
```
Expected:
```json
{
  "status": "healthy",
  "services": {
    "chromadb": "connected",
    "redis": "connected",
    "neo4j": "connected"
  }
}
```

**Step 3: Collections**

```bash
curl -s http://localhost:8888/collections | python3 -m json.tool
```
Expected: `{"total": N, "collections": [...]}`

**Step 4: Query**

```bash
curl -s -X POST http://localhost:8888/query \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "domain": "general", "top_k": 3}' | python3 -m json.tool
```
Expected: `{"context": "...", "sources": [...], "confidence": ...}`

**Step 5: Ingest**

```bash
curl -s -X POST http://localhost:8888/ingest \
  -H "Content-Type: application/json" \
  -d '{"content": "smoke test content", "domain": "general"}' | python3 -m json.tool
```
Expected: `{"status": "success", "artifact_id": "...", ...}`

**Step 6: Artifacts**

```bash
curl -s "http://localhost:8888/artifacts?domain=general&limit=5" | python3 -m json.tool
```
Expected: `{"artifacts": [...], "total": N}`

**Step 7: Ingest log**

```bash
curl -s "http://localhost:8888/ingest_log?limit=5" | python3 -m json.tool
```
Expected: Array of recent log entries

**Step 8: Agent query**

```bash
curl -s -X POST http://localhost:8888/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "top_k": 3}' | python3 -m json.tool
```
Expected: Response with `context`, `sources`, `domains_searched`

**Step 9: Agent rectify (read-only)**

```bash
curl -s -X POST http://localhost:8888/agent/rectify \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```
Expected: Health check results dict (no errors)

**Step 10: Agent audit**

```bash
curl -s -X POST http://localhost:8888/agent/audit \
  -H "Content-Type: application/json" \
  -d '{"hours": 24}' | python3 -m json.tool
```
Expected: Audit report dict

**Step 11: Root endpoint**

```bash
curl -s http://localhost:8888/ | python3 -m json.tool
```
Expected: `{"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}`

**Step 12: MCP SSE handshake**

```bash
curl -s -N --max-time 3 http://localhost:8888/mcp/sse 2>&1 | head -5
```
Expected: First line is `event: endpoint` followed by a data line with the session URL

---

## Task 10: Mark Complete + Commit

**Step 1: Update `docs/PHASE4_PLAN.md` — mark 4A complete**

In `docs/PHASE4_PLAN.md`, change:
```markdown
### 4A: Modular Refactor
- [ ] **Complete**
```
to:
```markdown
### 4A: Modular Refactor
- [x] **Complete**
```

**Step 2: Commit**

```bash
cd /Users/sunrunner/Develop/cerid-ai
git add src/mcp/deps.py src/mcp/routers/ src/mcp/main.py docs/PHASE4_PLAN.md docs/plans/
git commit -m "Phase 4A: split main.py into FastAPI routers

- New deps.py: DB connection singletons (get_chroma/redis/neo4j)
- New routers/: health, query, ingestion, artifacts, agents, mcp_sse
- main.py reduced from 1321 to ~100 lines
- lifespan replaces deprecated @app.on_event
- Zero behavior change — all endpoints identical"
```

---

## Verification Summary

Per `docs/PHASE4_PLAN.md` checklist:
- [ ] All REST endpoints return identical responses
- [ ] All agent endpoints respond without error
- [ ] MCP SSE connection works (event: endpoint received)
- [ ] Docker container builds and starts cleanly
- [ ] No deprecation warnings in logs
