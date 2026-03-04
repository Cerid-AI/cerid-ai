# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
AI Companion MCP Server - MCP SSE Transport + Ingestion Pipeline
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deps import close_chroma, close_neo4j, close_redis, get_neo4j
from middleware.auth import APIKeyMiddleware
from middleware.rate_limit import RateLimitMiddleware
from middleware.request_id import RequestIDMiddleware
from routers import (
    agents,
    artifacts,
    digest,
    health,
    ingestion,
    mcp_sse,
    memories,
    query,
    settings,
    sync,
    taxonomy,
    upload,
)
from scheduler import start_scheduler, stop_scheduler
from utils import graph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai-companion")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation: warn on missing critical env vars
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning(
            "OPENROUTER_API_KEY not set — LLM features (categorization, reranking, "
            "verification, memory extraction) will fail"
        )

    # Startup: initialize Neo4j schema + run migrations
    try:
        driver = get_neo4j()
        graph.init_schema(driver)
        from db.neo4j.migrations import backfill_updated_at
        backfill_updated_at(driver)
    except Exception as e:
        logger.warning(f"Neo4j schema init failed (will retry on first use): {e}")

    # Auto-import from sync directory if DB is empty
    try:
        from sync_check import auto_import_if_empty
        auto_import_if_empty()
    except Exception as e:
        logger.warning(f"Sync auto-import check failed: {e}")

    # Load plugins
    try:
        from plugins import load_plugins
        loaded = load_plugins()
        if loaded:
            logger.info(f"Plugins loaded: {', '.join(loaded)}")
    except Exception as e:
        logger.warning(f"Plugin loading failed (server runs without plugins): {e}")

    # Start scheduled maintenance engine
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start failed (server runs without it): {e}")

    yield

    # Shutdown: stop scheduler, close DB connections, clear MCP sessions
    stop_scheduler()
    close_neo4j()
    close_chroma()
    close_redis()
    mcp_sse.clear_sessions()


app = FastAPI(
    title="AI Companion MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware stack (LIFO in Starlette — last added runs first)
# 1. CORS (added first, runs last — wraps response headers)
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
_wildcard = _cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 2. Rate limiting (added second)
app.add_middleware(RateLimitMiddleware)
# 3. API key auth (added third, runs first among auth/rate — rejects unauthenticated before rate check)
app.add_middleware(APIKeyMiddleware)
# 4. Request ID (added last, runs first — sets X-Request-ID for all subsequent middleware)
app.add_middleware(RequestIDMiddleware)

# Register routers at root (backward compatibility) and /api/v1/ prefix
_api_routers = [
    health.router,
    query.router,
    ingestion.router,
    artifacts.router,
    agents.router,
    digest.router,
    taxonomy.router,
    settings.router,
    upload.router,
    memories.router,
    sync.router,
]
for r in _api_routers:
    app.include_router(r)
    app.include_router(r, prefix="/api/v1")

# MCP transport stays at root only (not versioned)
app.include_router(mcp_sse.router)


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}
