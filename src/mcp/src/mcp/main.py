"""
AI Companion MCP Server v0.5.0
Fixed SSE flow for LibreChat compatibility
"""

from __future__ import annotations

import json
import logging
import os
import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from neo4j import GraphDatabase
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ai-companion")

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


def health_check_internal():
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


def list_collections_internal():
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [{"name": c.name, "count": c.count()} for c in collections]}


# MCP Tools
MCP_TOOLS = {
    "pkb_query": {
        "description": "Query the personal knowledge base for relevant context",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "domain": {"type": "string", "default": "general"},
                "top_k": {"type": "integer", "default": 3}
            },
            "required": ["query"]
        },
        "handler": lambda args: _query_knowledge(args.get("query"), args.get("domain", "general"), args.get("top_k", 3))
    },
    "pkb_ingest": {
        "description": "Ingest content into the personal knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to ingest"},
                "domain": {"type": "string", "default": "general"}
            },
            "required": ["content"]
        },
        "handler": lambda args: _ingest_content(args.get("content"), args.get("domain", "general"))
    },
    "pkb_health": {
        "description": "Check health of all backing services",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda args: health_check_internal()
    },
    "pkb_collections": {
        "description": "List all collections in the knowledge base",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda args: list_collections_internal()
    }
}


def handle_mcp_message(body: dict) -> dict:
    """Handle MCP JSON-RPC messages"""
    method = body.get("method", "")
    msg_id = body.get("id")
    params = body.get("params", {})
    
    logger.info(f"MCP method: {method}")
    
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ai-companion", "version": "0.5.0"}
            }
        }
    
    elif method == "notifications/initialized":
        return None  # No response for notifications
    
    elif method == "tools/list":
        tools = [
            {"name": name, "description": tool["description"], "inputSchema": tool["inputSchema"]}
            for name, tool in MCP_TOOLS.items()
        ]
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
    
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        
        if tool_name not in MCP_TOOLS:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        
        try:
            result = MCP_TOOLS[tool_name]["handler"](tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]}
            }
        except Exception as e:
            logger.error(f"Tool error: {e}")
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}
    
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    
    else:
        logger.warning(f"Unknown method: {method}")
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


# Session storage for SSE
sessions: Dict[str, asyncio.Queue] = {}


# FastAPI app
app = FastAPI(title="AI Companion MCP Server", version="0.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "0.5.0", "status": "running", "mcp_endpoint": "/mcp/sse"}


@app.get("/health")
def health_check():
    return health_check_internal()


@app.get("/collections")
def list_collections():
    return list_collections_internal()


@app.post("/query", response_model=QueryResponse)
async def query_knowledge(req: QueryRequest):
    result = _query_knowledge(query=req.query, domain=req.domain, top_k=req.top_k, metadata_filter=req.metadata_filter)
    return QueryResponse(**result)


@app.post("/ingest")
async def ingest_content(req: IngestRequest):
    return _ingest_content(content=req.content, domain=req.domain, metadata=req.metadata, chunk_size=req.chunk_size, overlap=req.overlap)


@app.get("/stats")
async def get_stats():
    chroma = get_chroma()
    r = get_redis()
    collections = chroma.list_collections()
    return {
        "total_collections": len(collections),
        "total_documents": sum(c.count() for c in collections),
        "redis_keys": r.dbsize(),
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/mcp/sse")
async def mcp_sse(request: Request):
    """MCP SSE endpoint with proper bidirectional message flow"""
    session_id = str(uuid.uuid4())
    logger.info(f"MCP SSE connect: {session_id}")
    
    response_queue = asyncio.Queue()
    sessions[session_id] = response_queue
    
    async def event_stream():
        try:
            # Send endpoint URL immediately
            endpoint = f"/mcp/messages?session_id={session_id}"
            yield f"event: endpoint\ndata: {endpoint}\n\n"
            logger.info(f"Sent endpoint: {endpoint}")
            
            # Keep connection alive and send responses
            while True:
                try:
                    # Wait for response messages to send back
                    msg = await asyncio.wait_for(response_queue.get(), timeout=25.0)
                    if msg:
                        yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                        logger.info(f"Sent message: {msg.get('id', 'notification')}")
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE cancelled: {session_id}")
        except Exception as e:
            logger.error(f"SSE error: {e}")
        finally:
            sessions.pop(session_id, None)
            logger.info(f"SSE closed: {session_id}")
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        }
    )


@app.post("/mcp/messages")
async def mcp_messages(request: Request, session_id: str = ""):
    """Handle MCP messages and queue responses for SSE"""
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}
    
    logger.info(f"MCP request: session={session_id}, method={body.get('method')}")
    
    # Handle the message
    response = handle_mcp_message(body)
    
    # If we have a session, queue the response for SSE delivery
    if session_id and session_id in sessions and response:
        await sessions[session_id].put(response)
        logger.info(f"Queued response for SSE: {response.get('id')}")
        # Return 202 Accepted - response will come via SSE
        return Response(status_code=202)
    
    # No session or notification - return directly
    if response:
        return response
    return Response(status_code=202)


@app.on_event("shutdown")
def shutdown():
    global _neo4j
    if _neo4j:
        _neo4j.close()
        logger.info("Neo4j driver closed")
