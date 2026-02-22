"""
AI Companion MCP Server - MCP SSE Transport + Ingestion Pipeline
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deps import close_neo4j, get_neo4j
from routers import agents, artifacts, digest, health, ingestion, mcp_sse, query
from scheduler import start_scheduler, stop_scheduler
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

    # Auto-import from sync directory if DB is empty (Phase 5B)
    try:
        from sync_check import auto_import_if_empty
        auto_import_if_empty()
    except Exception as e:
        logger.warning(f"Sync auto-import check failed: {e}")

    # Start scheduled maintenance engine (Phase 4C.1)
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start failed (server runs without it): {e}")

    yield

    # Shutdown: stop scheduler, close DB driver, clear MCP sessions
    stop_scheduler()
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

# Register routers at root (backward compatibility) and /api/v1/ prefix
_api_routers = [
    health.router,
    query.router,
    ingestion.router,
    artifacts.router,
    agents.router,
    digest.router,
]
for r in _api_routers:
    app.include_router(r)
    app.include_router(r, prefix="/api/v1")

# MCP transport stays at root only (not versioned)
app.include_router(mcp_sse.router)


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}
