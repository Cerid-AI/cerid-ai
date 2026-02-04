"""
AI Companion MCP Server
Personal Knowledge Base with ChromaDB, Neo4j, Redis
Exposes REST API + MCP over SSE
"""
from __future__ import annotations
import inspect
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.routing import Mount

# from src.mcp.server.fastmcp import FastMCP   # Commented out - missing module
# from mcp.server.transport_security import TransportSecuritySettings  # Commented out

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ai-companion")

# Environment variables
CHROMA_URL = os.getenv("CHROMA_URL", "http://ai-companion-chroma:8000")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://ai-companion-neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "REDACTED_PASSWORD")
REDIS_URL = os.getenv("REDIS_URL", "redis://ai-companion-redis:6379")

_chroma = None
_redis = None
_neo4j = None

def get_chroma():
    global _chroma
    if _chroma is None:
        host = CHROMA_URL.replace("http://", "").split(":")[0]
        port = int(CHROMA_URL.split(":")[-1])
        _chroma = chromadb.HttpClient(host=host, port=port, settings=ChromaSettings(anonymized_telemetry=False))
        _chroma.heartbeat()
        logger.info("ChromaDB connected")
    return _chroma

def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5)
        _redis.ping()
        logger.info("Redis connected")
    return _redis

def get_neo4j():
    global _neo4j
    if _neo4j is None:
        _neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), max_connection_lifetime=3600)
        _neo4j.verify_connectivity()
        logger.info("Neo4j connected")
    return _neo4j

class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = 3
    metadata_filter: Optional[Dict[str, Any]] = None

class QueryResponse(BaseModel):
    context: str
    sources: List[Dict[str, Any]]
    confidence: float
    timestamp: str

class IngestRequest(BaseModel):
    content: str
    domain: str = "general"
    metadata: Optional[Dict[str, Any]] = None
    chunk_size: int = 1000
    overlap: int = 200

def _query_knowledge(query, domain="general", top_k=3, metadata_filter=None):
    logger.info(f"Query: domain={domain}")
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    results = collection.query(query_texts=[query], n_results=top_k, where=metadata_filter)
    timestamp = datetime.utcnow().isoformat()
    if not results.get("documents") or not results["documents"][0]:
        return {"context": "", "sources": [], "confidence": 0.0, "timestamp": timestamp}
    sources = []
    context_parts = []
    docs = results["documents"][0]
    metas = results.get("metadatas", [[{}] * len(docs)])[0]
    dists = results.get("distances", [[0.0] * len(docs)])[0]
    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        relevance = max(0.0, 1.0 - float(dist))
        sources.append({"content": doc[:200], "metadata": meta or {}, "relevance": round(relevance, 3)})
        context_parts.append(f"[Source {i+1}]\n{doc}")
    context = "\n\n---\n\n".join(context_parts)
    confidence = sum(s["relevance"] for s in sources) / max(1, len(sources))
    return {"context": context, "sources": sources, "confidence": round(confidence, 3), "timestamp": timestamp}

def _ingest_content(content, domain="general", metadata=None, chunk_size=1000, overlap=200):
    logger.info(f"Ingest: domain={domain}, len={len(content)}")
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    chunks = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(content), step):
        chunk = content[i:i + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
    if not chunks:
        raise ValueError("No content chunks produced")
    ts = datetime.utcnow().isoformat()
    ids = [f"{domain}_{ts}_{i}" for i in range(len(chunks))]
    meta = (metadata or {}).copy()
    meta["ingested_at"] = ts
    metadatas = [meta.copy() for _ in chunks]
    collection.add(documents=chunks, metadatas=metadatas, ids=ids)
    return {"status": "success", "chunks_created": len(chunks), "domain": domain, "timestamp": ts}

# FastAPI REST API
api = FastAPI(title="AI Companion MCP Server", version="0.3.0")
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@api.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "0.3.0", "status": "running"}

@api.get("/health")
def health_check():
    services = {}
    status = "healthy"
    try:
        get_chroma().heartbeat()
        services["chromadb"] = "connected"
    except Exception as e:
        services["chromadb"] = f"error: {str(e)[:50]}"
        status = "degraded"
    try:
        get_redis().ping()
        services["redis"] = "connected"
    except Exception as e:
        services["redis"] = f"error: {str(e)[:50]}"
        status = "degraded"
    try:
        get_neo4j().verify_connectivity()
        services["neo4j"] = "connected"
    except Exception as e:
        services["neo4j"] = f"error: {str(e)[:50]}"
        status = "degraded"
    return {"status": status, "services": services, "timestamp": datetime.utcnow().isoformat()}

@api.get("/collections")
def list_collections():
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [{"name": c.name, "count": c.count()} for c in collections]}

@api.post("/query", response_model=QueryResponse)
async def query_knowledge(req: QueryRequest):
    result = _query_knowledge(query=req.query, domain=req.domain, top_k=req.top_k, metadata_filter=req.metadata_filter)
    return QueryResponse(**result)

@api.post("/ingest")
async def ingest_content(req: IngestRequest):
    return _ingest_content(content=req.content, domain=req.domain, metadata=req.metadata, chunk_size=req.chunk_size, overlap=req.overlap)

@api.get("/stats")
async def get_stats():
    chroma = get_chroma()
    r = get_redis()
    collections = chroma.list_collections()
    return {
        "total_collections": len(collections),
        "total_documents": sum(c.count() for c in collections),
        "collections": [{"name": c.name, "count": c.count()} for c in collections],
        "redis_keys": r.dbsize(),
        "timestamp": datetime.utcnow().isoformat()
    }

@api.on_event("shutdown")
def shutdown():
    global _neo4j
    if _neo4j:
        _neo4j.close()
        logger.info("Neo4j driver closed")

# MCP Server Setup - commented out until FastMCP implementation is restored
# security_settings = TransportSecuritySettings(...)
# mcp = FastMCP("ai-companion")
# @mcp.tool()
# def pkb_query(...):
#     ...
# @mcp.tool()
# def pkb_ingest(...):
#     ...
# @mcp.tool()
# def pkb_health():
#     ...
# @mcp.tool()
# def pkb_collections():
#     ...
# def _build_mcp_sse_app():
#     ...
# mcp_asgi_app = _build_mcp_sse_app()

# Combined ASGI app - only REST API for now
app = Starlette(routes=[
    # Mount("/mcp", app=mcp_asgi_app),   # commented out
    Mount("/", app=api)
])