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
