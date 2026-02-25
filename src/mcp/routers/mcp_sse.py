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
_MAX_SESSIONS = 100


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
    {
        "name": "pkb_digest",
        "description": "Get a summary of recent knowledge base activity: new artifacts, connections, and health status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Lookback window in hours (default 24, max 168)",
                    "default": 24,
                },
            },
        },
    },
    {
        "name": "pkb_scheduler_status",
        "description": "Get the status of scheduled maintenance jobs",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pkb_check_hallucinations",
        "description": "Check an LLM response for hallucinations by verifying claims against the knowledge base",
        "inputSchema": {
            "type": "object",
            "properties": {
                "response_text": {"type": "string", "description": "The LLM response text to fact-check"},
                "conversation_id": {"type": "string", "description": "Conversation ID for report storage"},
                "threshold": {
                    "type": "number",
                    "description": "Similarity threshold for claim verification (0-1, default 0.75)",
                    "default": 0.75,
                },
            },
            "required": ["response_text", "conversation_id"],
        },
    },
    {
        "name": "pkb_memory_extract",
        "description": "Extract and store memories (facts, decisions, preferences) from a conversation response",
        "inputSchema": {
            "type": "object",
            "properties": {
                "response_text": {"type": "string", "description": "The LLM response to extract memories from"},
                "conversation_id": {"type": "string", "description": "Conversation ID for linking"},
                "model": {"type": "string", "description": "Model that generated the response", "default": "unknown"},
            },
            "required": ["response_text", "conversation_id"],
        },
    },
    {
        "name": "pkb_memory_archive",
        "description": "Archive old conversation memories past the retention period",
        "inputSchema": {
            "type": "object",
            "properties": {
                "retention_days": {
                    "type": "integer",
                    "description": "Number of days to retain memories (default 180)",
                    "default": 180,
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
            neo4j_driver=get_neo4j(),
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
    elif name == "pkb_digest":
        from routers.digest import digest_endpoint
        return await digest_endpoint(hours=arguments.get("hours", 24))
    elif name == "pkb_scheduler_status":
        from scheduler import get_job_status
        return get_job_status()
    elif name == "pkb_check_hallucinations":
        from agents.hallucination import check_hallucinations
        return await check_hallucinations(
            response_text=arguments.get("response_text", ""),
            conversation_id=arguments.get("conversation_id", ""),
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
            threshold=arguments.get("threshold"),
        )
    elif name == "pkb_memory_extract":
        from agents.memory import extract_and_store_memories
        return await extract_and_store_memories(
            response_text=arguments.get("response_text", ""),
            conversation_id=arguments.get("conversation_id", ""),
            model=arguments.get("model", "unknown"),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            neo4j_driver=get_neo4j(),
        )
    elif name == "pkb_memory_archive":
        from agents.memory import archive_old_memories
        return await archive_old_memories(
            neo4j_driver=get_neo4j(),
            retention_days=arguments.get("retention_days", 180),
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
    # Evict oldest session if at capacity
    if len(_sessions) >= _MAX_SESSIONS:
        oldest_key = next(iter(_sessions))
        _sessions.pop(oldest_key, None)
        logger.warning(f"[MCP] Evicted oldest session {oldest_key} (cap={_MAX_SESSIONS})")
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
