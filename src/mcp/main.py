"""
AI Companion MCP Server - MCP SSE Transport + Ingestion Pipeline
Responses go through SSE stream, not HTTP response body
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from neo4j import GraphDatabase
from pydantic import BaseModel

import config
from utils import cache, graph
from utils.chunker import chunk_text, count_tokens
from utils.metadata import ai_categorize, extract_metadata
from utils.parsers import parse_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai-companion")


# ============================================================
# MCP TOOLS - Full schemas
# ============================================================
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
]


# ============================================================
# DATABASE CONNECTIONS (using config.py)
# ============================================================
_chroma = None
_redis = None
_neo4j = None


def get_chroma():
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


def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.from_url(config.REDIS_URL, decode_responses=True, socket_connect_timeout=5)
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


# ============================================================
# TOOL IMPLEMENTATIONS
# ============================================================


def _query_knowledge(query: str, domain: str = "general", top_k: int = 3) -> Dict:
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

    # Build sources with real relevance scores and attribution
    sources = []
    for i, doc in enumerate(docs):
        # ChromaDB distance: lower = more similar. Convert to 0-1 relevance.
        # Cosine distance ranges 0-2; typical useful range 0-1.
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

    # Token budget: cap context at ~3500 tokens (~14000 chars)
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

    # Overall confidence: average relevance of returned sources
    avg_relevance = sum(s["relevance"] for s in sources) / len(sources) if sources else 0.0

    return {
        "context": context,
        "sources": sources,
        "confidence": round(avg_relevance, 3),
        "timestamp": timestamp,
    }


def _content_hash(content: str) -> str:
    """Generate a SHA-256 hash of text content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _check_duplicate(content_hash: str, domain: str) -> Optional[Dict]:
    """Check if content with this hash already exists in Neo4j."""
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


def _ingest_content(
    content: str,
    domain: str = "general",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict:
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)

    artifact_id = str(uuid.uuid4())
    content_hash = _content_hash(content)

    # Deduplication check
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

    # Chunk content
    chunks = chunk_text(content, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP)

    base_meta = {"domain": domain, "artifact_id": artifact_id}
    if metadata:
        base_meta.update(metadata)

    # Batch ChromaDB write (single call instead of per-chunk loop)
    chunk_ids = [f"{artifact_id}_chunk_{i}" for i in range(len(chunks))]
    chunk_metadatas = [{**base_meta, "chunk_index": i} for i in range(len(chunks))]
    collection.add(ids=chunk_ids, documents=chunks, metadatas=chunk_metadatas)

    # Neo4j artifact tracking (with content hash for deduplication)
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
            # Concurrent duplicate: another request wrote the same content_hash
            # Clean up the chunks we just wrote to ChromaDB
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

    # Redis audit log
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


async def _ingest_file(
    file_path: str,
    domain: str = "",
    tags: str = "",
    categorize_mode: str = "",
) -> Dict:
    """Parse a file, extract metadata, optionally AI-categorize, chunk, and store."""
    filename = Path(file_path).name

    # Parse file to text (parse_file handles existence check + empty file detection)
    parsed = parse_file(file_path)
    text = parsed["text"]

    # Extract core metadata (local, no API)
    meta = extract_metadata(text, filename, domain or config.DEFAULT_DOMAIN)

    # Determine categorization
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

    # Final domain fallback
    if not domain or domain not in config.DOMAINS:
        domain = config.DEFAULT_DOMAIN
    meta["domain"] = domain

    # Add tags
    if tags:
        meta["tags"] = tags

    # Add file-specific metadata
    meta["file_type"] = parsed.get("file_type", "")
    if parsed.get("page_count") is not None:
        meta["page_count"] = parsed["page_count"]

    # Ingest via shared path (chunks, Neo4j, Redis)
    result = _ingest_content(text, domain, metadata=meta)
    result["filename"] = filename
    result["categorize_mode"] = mode
    result["metadata"] = {
        k: v for k, v in meta.items()
        if k in ("filename", "domain", "keywords", "summary", "tags", "file_type", "estimated_tokens")
    }
    return result


def _recategorize(artifact_id: str, new_domain: str, tags: str = "") -> Dict:
    """Move an artifact's chunks to a new domain collection."""
    if new_domain not in config.DOMAINS:
        raise ValueError(f"Invalid domain: {new_domain}. Valid: {config.DOMAINS}")

    driver = get_neo4j()
    chroma = get_chroma()

    # Get artifact from Neo4j
    artifact = graph.get_artifact(driver, artifact_id)
    if not artifact:
        raise ValueError(f"Artifact not found: {artifact_id}")

    old_domain = artifact["domain"]
    if old_domain == new_domain:
        raise ValueError(f"Artifact already in domain '{new_domain}'")

    chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
    if not chunk_ids:
        raise ValueError(f"No chunk IDs found for artifact {artifact_id}")

    # Fetch chunks from source collection
    source_collection = chroma.get_or_create_collection(
        name=f"domain_{old_domain.replace(' ', '_').lower()}"
    )
    fetched = source_collection.get(ids=chunk_ids, include=["documents", "metadatas"])

    if not fetched["ids"]:
        raise ValueError(f"No chunks found in ChromaDB for artifact {artifact_id}")

    # Add to destination collection with updated metadata
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

    # Delete from source
    source_collection.delete(ids=chunk_ids)

    # Update Neo4j
    domains = graph.recategorize_artifact(driver, artifact_id, new_domain)

    # Redis audit
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


def _health_check() -> Dict:
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


def _list_collections() -> Dict:
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


async def execute_tool(name: str, arguments: Dict) -> Any:
    if name == "pkb_query":
        return _query_knowledge(**arguments)
    elif name == "pkb_ingest":
        return _ingest_content(arguments.get("content", ""), arguments.get("domain", "general"))
    elif name == "pkb_ingest_file":
        return await _ingest_file(**arguments)
    elif name == "pkb_health":
        return _health_check()
    elif name == "pkb_collections":
        return _list_collections()
    elif name == "pkb_agent_query":
        from agents.query_agent import agent_query
        return await agent_query(
            query=arguments.get("query", ""),
            domains=arguments.get("domains"),
            top_k=arguments.get("top_k", 10),
            use_reranking=arguments.get("use_reranking", True),
            chroma_client=get_chroma(),
            redis_client=get_redis()
        )
    elif name == "pkb_artifacts":
        domain = arguments.get("domain", "") or None
        limit = arguments.get("limit", 50)
        driver = get_neo4j()
        return graph.list_artifacts(driver, domain=domain, limit=limit)
    elif name == "pkb_recategorize":
        return _recategorize(
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
        # Triage prepared the data; now write to DBs
        result = _ingest_content(
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
    raise ValueError(f"Unknown tool: {name}")


async def build_response(msg_id, method: str, params: dict) -> dict:
    """Build JSON-RPC response for a method"""
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


# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="AI Companion MCP Server", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session message queues for SSE responses
_sessions: Dict[str, asyncio.Queue] = {}


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================


@app.on_event("startup")
def startup():
    """Initialize Neo4j schema on startup."""
    try:
        driver = get_neo4j()
        graph.init_schema(driver)
    except Exception as e:
        logger.warning(f"Neo4j schema init failed (will retry on first use): {e}")


@app.on_event("shutdown")
def shutdown():
    global _neo4j
    if _neo4j:
        _neo4j.close()
    _sessions.clear()
    logger.info("MCP sessions cleared on shutdown")


# ============================================================
# REST ENDPOINTS
# ============================================================


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}


@app.get("/health")
def health_check():
    return _health_check()


@app.get("/collections")
def list_collections():
    return _list_collections()


@app.get("/stats")
async def get_stats():
    return _list_collections()


class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = 3


@app.post("/query")
async def query_knowledge(req: QueryRequest):
    return _query_knowledge(req.query, req.domain, req.top_k)


class IngestRequest(BaseModel):
    content: str
    domain: str = "general"


@app.post("/ingest")
async def ingest_content(req: IngestRequest):
    return _ingest_content(req.content, req.domain)


# ============================================================
# PHASE 1: FILE INGESTION ENDPOINTS
# ============================================================


class IngestFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    tags: str = ""
    categorize_mode: str = ""


@app.post("/ingest_file")
async def ingest_file(req: IngestFileRequest):
    """Ingest a file with parsing, metadata extraction, and optional AI categorization."""
    try:
        result = await _ingest_file(
            file_path=req.file_path,
            domain=req.domain,
            tags=req.tags,
            categorize_mode=req.categorize_mode,
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ingest file error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class RecategorizeRequest(BaseModel):
    artifact_id: str
    new_domain: str
    tags: str = ""


@app.post("/recategorize")
async def recategorize(req: RecategorizeRequest):
    """Move an artifact to a different domain (moves chunks between ChromaDB collections)."""
    try:
        return _recategorize(req.artifact_id, req.new_domain, req.tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Recategorize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# PHASE 2: AGENT QUERY ENDPOINT
# ============================================================


class AgentQueryRequest(BaseModel):
    query: str
    domains: Optional[List[str]] = None
    top_k: int = 10
    use_reranking: bool = True


@app.post("/agent/query")
async def agent_query_endpoint(req: AgentQueryRequest):
    """
    Enhanced multi-domain query using Query Agent.

    Provides:
    - Multi-domain parallel retrieval across ChromaDB collections
    - Deduplication by artifact_id + chunk_index
    - Intelligent reranking (future: LLM-powered)
    - Token budget enforcement (14k char limit)
    - Source attribution with confidence scoring

    Args:
        query: Natural language search query
        domains: Optional list of domains to search (default: all domains)
        top_k: Number of results per domain (default: 10)
        use_reranking: Enable intelligent reranking (default: true)

    Returns:
        {
            "context": "assembled context string...",
            "sources": [{filename, domain, relevance, ...}, ...],
            "confidence": 0.85,
            "domains_searched": ["coding", "finance"],
            "total_results": 42,
            "token_budget_used": 12500
        }
    """
    try:
        from agents.query_agent import agent_query

        result = await agent_query(
            query=req.query,
            domains=req.domains,
            top_k=req.top_k,
            use_reranking=req.use_reranking,
            chroma_client=get_chroma(),
            redis_client=get_redis()
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Agent query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# PHASE 2: TRIAGE ENDPOINT
# ============================================================


class TriageFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    categorize_mode: str = ""
    tags: str = ""


@app.post("/agent/triage")
async def triage_file_endpoint(req: TriageFileRequest):
    """
    Triage a file through the LangGraph ingestion pipeline.

    Runs the file through validation, parsing, categorization routing,
    metadata extraction, and chunking. Then writes to ChromaDB + Neo4j + Redis.
    """
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

        # Write prepared data to databases
        result = _ingest_content(
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


class RectifyRequest(BaseModel):
    checks: Optional[List[str]] = None
    auto_fix: bool = False
    stale_days: int = 90


@app.post("/agent/rectify")
async def rectify_endpoint(req: RectifyRequest):
    """
    Run knowledge base health checks and optional auto-fix.

    Checks: duplicates, stale, orphans, distribution.
    """
    try:
        from agents.rectify import rectify

        result = await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=req.checks,
            auto_fix=req.auto_fix,
            stale_days=req.stale_days,
        )
        return result
    except Exception as e:
        logger.error(f"Rectify error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class TriageBatchRequest(BaseModel):
    files: List[Dict[str, str]]
    default_mode: str = ""


@app.post("/agent/triage/batch")
async def triage_batch_endpoint(req: TriageBatchRequest):
    """
    Triage a batch of files. Each file is processed independently.
    Failures don't stop the batch.
    """
    try:
        from agents.triage import triage_batch

        triage_results = await triage_batch(
            files=req.files,
            default_mode=req.default_mode,
        )

        # Write successful triages to databases
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
                result = _ingest_content(
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


@app.get("/artifacts")
async def list_artifacts(
    domain: Optional[str] = Query(None, description="Filter by domain"),
    limit: int = Query(50, ge=1, le=500),
):
    """List ingested artifacts from Neo4j."""
    try:
        driver = get_neo4j()
        return graph.list_artifacts(driver, domain=domain, limit=limit)
    except Exception as e:
        logger.error(f"List artifacts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ingest_log")
async def ingest_log(limit: int = Query(50, ge=1, le=500)):
    """View recent ingest/recategorize events from Redis audit trail."""
    try:
        return cache.get_log(get_redis(), limit=limit)
    except Exception as e:
        logger.error(f"Ingest log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# MCP SSE TRANSPORT - Responses via SSE stream
# ============================================================


@app.head("/mcp/sse")
async def mcp_sse_head():
    return Response(status_code=200, headers={"Content-Type": "text/event-stream"})


@app.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """SSE endpoint - responses to POSTs come through here"""
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


@app.post("/mcp/sse")
async def mcp_sse_post(request: Request):
    """Handle probes and JSON-RPC to /mcp/sse"""
    return Response(status_code=200, content="", media_type="text/plain")


@app.post("/mcp/messages")
async def mcp_messages(request: Request):
    """Receive JSON-RPC, send response via SSE stream"""
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
