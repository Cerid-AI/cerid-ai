"""
AI Companion MCP Server - MCP SSE Transport
Responses go through SSE stream, not HTTP response body
"""

from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict
import json
import asyncio

import chromadb
import redis
from chromadb.config import Settings as ChromaSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from neo4j import GraphDatabase
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ai-companion")

# ============================================================
# MCP TOOLS
# ============================================================

MCP_TOOLS = [
    {
        "name": "pkb_query",
        "description": "Query the personal knowledge base for relevant context",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "domain": {"type": "string", "description": "Knowledge domain", "default": "general"},
                "top_k": {"type": "integer", "description": "Number of results", "default": 3}
            },
            "required": ["query"]
        }
    },
    {
        "name": "pkb_ingest",
        "description": "Ingest content into the knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to ingest"},
                "domain": {"type": "string", "description": "Knowledge domain", "default": "general"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "pkb_health",
        "description": "Check knowledge base service health",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "pkb_collections",
        "description": "List available knowledge base collections",
        "inputSchema": {"type": "object", "properties": {}}
    }
]

# ============================================================
# DATABASE CONNECTIONS
# ============================================================

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
        _neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        _neo4j.verify_connectivity()
        logger.info("Neo4j connected")
    return _neo4j


def _query_knowledge(query: str, domain: str = "general", top_k: int = 3) -> Dict:
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    results = collection.query(query_texts=[query], n_results=top_k)
    timestamp = datetime.utcnow().isoformat()
    if not results.get("documents") or not results["documents"][0]:
        return {"context": "", "sources": [], "confidence": 0.0, "timestamp": timestamp}
    sources = [{"content": doc[:200], "relevance": 0.8} for doc in results["documents"][0]]
    context = "\n\n".join(results["documents"][0])
    return {"context": context, "sources": sources, "confidence": 0.8, "timestamp": timestamp}


def _ingest_content(content: str, domain: str = "general") -> Dict:
    chroma = get_chroma()
    collection_name = f"domain_{domain.replace(' ', '_').lower()}"
    collection = chroma.get_or_create_collection(name=collection_name)
    doc_id = str(uuid.uuid4())
    collection.add(ids=[doc_id], documents=[content], metadatas=[{"domain": domain}])
    return {"status": "success", "id": doc_id, "domain": domain, "timestamp": datetime.utcnow().isoformat()}


def _health_check() -> Dict:
    status = {"chromadb": "unknown", "redis": "unknown", "neo4j": "unknown"}
    try:
        get_chroma()
        status["chromadb"] = "connected"
    except:
        status["chromadb"] = "error"
    try:
        get_redis()
        status["redis"] = "connected"
    except:
        status["redis"] = "error"
    try:
        get_neo4j()
        status["neo4j"] = "connected"
    except:
        status["neo4j"] = "error"
    return {"status": "healthy" if all(v == "connected" for v in status.values()) else "degraded", "services": status}


def _list_collections() -> Dict:
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


def execute_tool(name: str, arguments: Dict) -> Any:
    if name == "pkb_query":
        return _query_knowledge(**arguments)
    elif name == "pkb_ingest":
        return _ingest_content(**arguments)
    elif name == "pkb_health":
        return _health_check()
    elif name == "pkb_collections":
        return _list_collections()
    raise ValueError(f"Unknown tool: {name}")


def build_response(msg_id, method: str, params: dict) -> dict:
    """Build JSON-RPC response for a method"""
    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "cerid-ai-companion", "version": "0.7.0"}
            }
        }
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": MCP_TOOLS}
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result = execute_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
            }
        except Exception as e:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(e)}}
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    else:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown: {method}"}}


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="AI Companion MCP Server", version="0.7.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Session message queues for SSE responses
_sessions: Dict[str, asyncio.Queue] = {}


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "0.7.0", "status": "running"}


@app.get("/health")
def health_check():
    return _health_check()


@app.get("/collections")
def list_collections():
    return _list_collections()


class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = 3


class IngestRequest(BaseModel):
    content: str
    domain: str = "general"


@app.post("/query")
async def query_knowledge(req: QueryRequest):
    return _query_knowledge(req.query, req.domain, req.top_k)


@app.post("/ingest")
async def ingest_content(req: IngestRequest):
    return _ingest_content(req.content, req.domain)


@app.get("/stats")
async def get_stats():
    return _list_collections()


@app.on_event("shutdown")
def shutdown():
    global _neo4j
    if _neo4j:
        _neo4j.close()


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
            # First: send endpoint event with session ID in URL
            endpoint_url = f"http://ai-companion-mcp:8888/mcp/messages?sessionId={session_id}"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"
            logger.info(f"[MCP] Sent endpoint: {endpoint_url}")
            
            # Stream responses from queue
            while True:
                if await request.is_disconnected():
                    break
                
                try:
                    # Wait for messages with timeout for keepalive
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    data = json.dumps(msg)
                    yield f"event: message\ndata: {data}\n\n"
                    logger.info(f"[MCP] Sent via SSE: {msg.get('id', 'notification')}")
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sessions.pop(session_id, None)
            logger.info(f"[MCP] SSE closed: {session_id}")
    
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.post("/mcp/sse")
async def mcp_sse_post(request: Request):
    """Handle probes to /mcp/sse"""
    return Response(status_code=200, content="", media_type="text/plain")


@app.post("/mcp/messages")
async def mcp_messages(request: Request):
    """Receive JSON-RPC, send response via SSE stream"""
    session_id = request.query_params.get("sessionId")
    
    try:
        body = await request.body()
        body_text = body.decode('utf-8').strip()
        
        if not body_text or body_text == '{}':
            return Response(status_code=202)
        
        msg = json.loads(body_text)
    except Exception as e:
        logger.error(f"[MCP] Parse error: {e}")
        return Response(status_code=400, content=str(e))
    
    method = msg.get("method", "")
    params = msg.get("params", {})
    msg_id = msg.get("id")
    
    logger.info(f"[MCP] Received: {method} (id={msg_id}, session={session_id})")
    
    # Notifications don't need response
    if method in ("initialized", "notifications/initialized"):
        logger.info("[MCP] Client initialized")
        return Response(status_code=202)
    
    # Build response
    response = build_response(msg_id, method, params)
    
    # Send via SSE if we have the session
    if session_id and session_id in _sessions:
        await _sessions[session_id].put(response)
        logger.info(f"[MCP] Queued response for SSE: {method}")
        return Response(status_code=202)
    else:
        # Fallback: return directly (for testing)
        logger.warning(f"[MCP] No session, returning directly: {method}")
        return Response(
            status_code=200,
            content=json.dumps(response),
            media_type="application/json"
        )
