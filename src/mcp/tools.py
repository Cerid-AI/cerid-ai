# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP tool registry — schemas and execute_tool() dispatcher.

Extracted from routers/mcp_sse.py so the SSE router is a thin
protocol layer and tools are testable independently.
"""
from __future__ import annotations

from typing import Any

import config
from db import neo4j as graph
from deps import get_chroma, get_neo4j, get_redis
from routers.artifacts import recategorize
from routers.health import health_check, list_collections
from routers.query import query_knowledge
from services.ingestion import ingest_content, ingest_file

# ── MCP Tool Definitions ─────────────────────────────────────────────────────

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
        "outputSchema": {
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": {"type": "object"}, "description": "Matching chunks with relevance scores"},
                "context": {"type": "string", "description": "Assembled context string"},
                "confidence": {"type": "number", "description": "Average relevance 0.0-1.0"},
                "domains_searched": {"type": "array", "items": {"type": "string"}},
                "total_results": {"type": "integer"},
            },
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "artifact_id": {"type": "string"},
                "chunks": {"type": "integer"},
                "domain": {"type": "string"},
            },
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "artifact_id": {"type": "string"},
                "chunks": {"type": "integer"},
                "domain": {"type": "string"},
            },
        },
    },
    {
        "name": "pkb_health",
        "description": "Check knowledge base service health",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "services": {"type": "object"},
            },
        },
    },
    {
        "name": "pkb_collections",
        "description": "List available knowledge base collections",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "services": {"type": "object"},
            },
        },
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
                "rag_mode": {
                    "type": "string",
                    "enum": ["manual", "smart", "custom_smart"],
                    "description": "RAG mode: manual (KB only), smart (KB + memory + external), custom_smart (Pro, configurable weights)",
                    "default": "manual",
                },
                "source_config": {
                    "type": "object",
                    "description": "Custom Smart source weights/toggles (Pro tier only). Keys: kb_enabled, memory_enabled, external_enabled, kb_weight, memory_weight, external_weight, memory_types",
                },
            },
            "required": ["query"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": {"type": "object"}, "description": "Matching chunks with relevance scores"},
                "context": {"type": "string", "description": "Assembled context string"},
                "confidence": {"type": "number", "description": "Average relevance 0.0-1.0"},
                "domains_searched": {"type": "array", "items": {"type": "string"}},
                "total_results": {"type": "integer"},
                "source_breakdown": {
                    "type": "object",
                    "description": "Per-source results (smart/custom_smart modes only)",
                    "properties": {
                        "kb": {"type": "array", "items": {"type": "object"}},
                        "memory": {"type": "array", "items": {"type": "object"}},
                        "external": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "rag_mode": {"type": "string", "description": "Active RAG mode used for this query"},
            },
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
        "outputSchema": {
            "type": "array",
            "items": {"type": "object"},
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
        "outputSchema": {
            "type": "object",
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "artifact_id": {"type": "string"},
                "chunks": {"type": "integer"},
                "domain": {"type": "string"},
            },
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string"},
                "findings": {"type": "object"},
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string"},
                "findings": {"type": "object"},
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string"},
                "findings": {"type": "object"},
            },
        },
    },
    {
        "name": "pkb_curate",
        "description": "Score artifact quality across the knowledge base. Returns quality distribution and low-quality artifacts needing attention.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Domains to score ({', '.join(config.DOMAINS)}). Empty = all.",
                },
                "max_artifacts": {
                    "type": "integer",
                    "description": "Max artifacts to score per domain (default 200)",
                    "default": 200,
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string"},
                "findings": {"type": "object"},
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string"},
                "findings": {"type": "object"},
            },
        },
    },
    {
        "name": "pkb_scheduler_status",
        "description": "Get the status of scheduled maintenance jobs",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "services": {"type": "object"},
            },
        },
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "claims": {"type": "array", "items": {"type": "object"}},
                "summary": {"type": "object"},
                "conversation_id": {"type": "string"},
                "skipped": {"type": "boolean"},
            },
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "memories_stored": {"type": "integer"},
                "results": {"type": "array", "items": {"type": "object"}},
            },
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
        "outputSchema": {
            "type": "object",
            "properties": {
                "archived_count": {"type": "integer"},
                "retention_days": {"type": "integer"},
                "cutoff_date": {"type": "string"},
                "timestamp": {"type": "string"},
            },
        },
    },
    {
        "name": "pkb_memory_recall",
        "description": "Recall relevant memories with salience-aware decay scoring. Six memory types (empirical, decision, preference, project_context, temporal, conversational) use per-type decay: power-law for long-lived facts, exponential for transient context, step-function for temporal events. Memories are reinforced by recency-weighted access. Returns results sorted by adjusted relevance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall from memory"},
                "top_k": {"type": "integer", "description": "Max memories to return (default: 10)", "default": 10},
                "min_score": {"type": "number", "description": "Min adjusted score threshold (default: 0.3)", "default": 0.3},
            },
            "required": ["query"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "score": {"type": "number"},
                            "access_count": {"type": "integer"},
                            "memory_type": {"type": "string"},
                            "age_days": {"type": "number"},
                            "source_authority": {"type": "number"},
                            "summary": {"type": "string"},
                            "base_similarity": {"type": "number"},
                            "created_at": {"type": "string"},
                        },
                    },
                },
                "total_recalled": {"type": "integer"},
                "timestamp": {"type": "string"},
            },
        },
    },
    {
        "name": "pkb_web_search",
        "description": "Search the web for information not in the knowledge base. Results are optionally verified through Self-RAG and can be auto-ingested into the KB.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — be specific for best results",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5, max: 10)",
                    "default": 5,
                },
                "auto_ingest": {
                    "type": "boolean",
                    "description": "Auto-ingest verified results into the KB (requires ENABLE_AUTO_LEARN=true)",
                    "default": False,
                },
            },
            "required": ["query"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                            "snippet": {"type": "string"},
                            "score": {"type": "number"},
                        },
                    },
                },
                "provider": {"type": "string"},
                "ingested_count": {"type": "integer"},
                "timestamp": {"type": "string"},
            },
        },
    },
    {
        "name": "pkb_ingest_multimodal",
        "description": (
            "Ingest images or audio files into the KB by routing through "
            "the appropriate plugin (OCR, audio transcription, or vision LLM). "
            "Requires CERID_TIER=pro."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to image or audio file",
                },
                "domain": {
                    "type": "string",
                    "description": f"Knowledge domain ({', '.join(config.DOMAINS)})",
                    "default": "general",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags for the artifact",
                    "default": "",
                },
                "plugin": {
                    "type": "string",
                    "description": "Force a specific plugin: 'ocr', 'audio', or 'vision'. Empty for auto-detect.",
                    "default": "",
                },
            },
            "required": ["file_path"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "artifact_id": {"type": "string"},
                "chunks": {"type": "integer"},
                "domain": {"type": "string"},
                "plugin_used": {"type": "string"},
                "extracted_chars": {"type": "integer"},
            },
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

async def execute_tool(name: str, arguments: dict) -> Any:
    """Dispatch a tool call by name. Raises ValueError for unknown tools."""
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
        rag_mode = arguments.get("rag_mode", "manual")
        if rag_mode in ("smart", "custom_smart"):
            from agents.retrieval_orchestrator import orchestrated_query
            return await orchestrated_query(
                query=arguments.get("query", ""),
                rag_mode=rag_mode,
                domains=arguments.get("domains"),
                top_k=arguments.get("top_k", 10),
                use_reranking=arguments.get("use_reranking", True),
                chroma_client=get_chroma(),
                redis_client=get_redis(),
                neo4j_driver=get_neo4j(),
                source_config=arguments.get("source_config"),
            )
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
    elif name == "pkb_curate":
        from agents.curator import curate
        return await curate(
            neo4j_driver=get_neo4j(),
            domains=arguments.get("domains"),
            max_artifacts=arguments.get("max_artifacts", 200),
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
    elif name == "pkb_memory_recall":
        from agents.memory import recall_memories
        from utils.time import utcnow_iso as _utcnow_iso
        results = await recall_memories(
            query=arguments.get("query", ""),
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            top_k=arguments.get("top_k", 10),
            min_score=arguments.get("min_score"),
        )
        return {
            "memories": [
                {
                    "id": m.get("memory_id", ""),
                    "text": m.get("text", ""),
                    "score": m.get("adjusted_score", 0.0),
                    "access_count": m.get("access_count", 0),
                    "memory_type": m.get("memory_type", "empirical"),
                    "age_days": m.get("age_days", 0.0),
                    "source_authority": m.get("source_authority", 0.7),
                    "summary": m.get("summary", ""),
                    "base_similarity": m.get("base_similarity", 0.0),
                    "created_at": "",  # not available from vector search metadata
                }
                for m in results
            ],
            "total_recalled": len(results),
            "timestamp": _utcnow_iso(),
        }
    elif name == "pkb_web_search":
        from utils.web_search import search_and_verify
        return await search_and_verify(
            query=arguments.get("query", ""),
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
            max_results=arguments.get("max_results", 5),
            auto_ingest=arguments.get("auto_ingest", False),
        )
    elif name == "pkb_ingest_multimodal":
        from services.multimodal import ingest_multimodal
        return await ingest_multimodal(
            file_path=arguments.get("file_path", ""),
            domain=arguments.get("domain", "general"),
            tags=arguments.get("tags", ""),
            plugin_override=arguments.get("plugin", ""),
        )
    raise ValueError(f"Unknown tool: {name}")
