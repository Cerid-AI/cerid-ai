# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP tool registry — schemas and execute_tool() dispatcher.

Extracted from routers/mcp_sse.py so the SSE router is a thin
protocol layer and tools are testable independently.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import config
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j, get_redis
from app.routers.artifacts import recategorize
from app.routers.health import health_check, list_collections
from app.services.ingestion import ingest_content, ingest_file
from app.tool_registry import (
    TOOL_REGISTRY,
    InvalidToolError,
    ToolError,
    execute_registered_tool,
    get_registered_schemas,
)
from core.utils.swallowed import log_swallowed_error

# Extension hooks — populated by bootstrap (internal tools, plugins, etc.)
_tool_dispatchers: list = []

_audit_logger = logging.getLogger("ai-companion.mcp_tool_audit")

# ── MCP Tool Definitions ─────────────────────────────────────────────────────

MCP_TOOLS = [
    # v0.96.0: pkb_query alias removed. Deprecation maturity ended per
    # `tests/test_mcp_tool_schema_fidelity.py::test_tool_inventory_meets_minimum`
    # — the floor drops from 56 → 55 tools. Use `pkb_agent_query` for the
    # multi-domain reranked path (the v0.95 replacement) or
    # `pkb_search_filtered` when you specifically need a single-domain
    # top-k cheaper than the agent path.
    {
        "name": "pkb_ingest",
        "description": "Ingest a raw text blob into the KB (no file, no parsing pipeline). **Use when** the LLM is handing in text it already has — chat turn, web-scrape snippet, computed summary. For files on disk use `pkb_ingest_file`; for full triage routing use `pkb_triage`. **Returns** `{status, artifact_id, chunks, domain}`.",
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
        "description": "Ingest a file from the local archive — runs through parsers (PDF/HTML/code), chunks, and writes Neo4j + ChromaDB. **Use when** the source is already on disk under `/archive/` and you want straight ingestion without triage. For pipeline triage (auto-domain, dedup, route) use `pkb_triage`. **Returns** `{status, artifact_id, chunks, domain}`.",
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
        "description": "Probe upstream service connectivity (Neo4j, ChromaDB, Redis) + invariants snapshot. **Use when** diagnosing failed ingests or empty-result queries — confirms the KB stack is up before deeper investigation. **Returns** `{status, services, ...}` with per-service connection state.",
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
        "description": "List ChromaDB collections currently registered in the KB. **Use when** verifying the KB schema, debugging a missing domain, or sizing the embedding store. **Returns** `{total: int, collections: [str]}` — one name per registered collection.",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "total": {"type": "integer", "description": "Number of registered collections"},
                "collections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ChromaDB collection names (e.g. 'domain_coding')",
                },
            },
        },
    },
    {
        "name": "pkb_agent_query",
        "description": "Primary KB search — multi-domain hybrid retrieval with reranking and context assembly. **Use when** answering any factual / project question grounded in the KB. Default-on reranking improves precision; pass `use_reranking=false` for raw retrieval. **Phase K3.3** — optional `surfaces=['wiki','vector','graph','memory']` arg restricts retrieval to a subset of knowledge surfaces; default uses the surface router to pick. **Returns** `{results, context, confidence, domains_searched, total_results, surface_route?}`. Cost class: medium (one embed + one rerank call).",
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
                "surfaces": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["wiki", "vector", "graph", "memory"]},
                    "description": "Phase K3.3 — restrict retrieval to the named surfaces. Empty/omitted = router-chosen.",
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
                "surface_route": {
                    "type": "object",
                    "description": "Phase K3.3 — which surfaces were consulted, and why",
                },
            },
        },
    },
    {
        "name": "pkb_artifacts",
        "description": "List ingested artifacts in the KB. **Use when** inventorying recent ingests, looking up an artifact ID for `pkb_artifact_get` / `pkb_recategorize`, or auditing what landed in a domain. **Returns** `{artifacts: [{artifact_id, domain, source, chunks, created_at}]}`. Default limit 50.",
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
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of artifact records",
                },
            },
        },
    },
    {
        "name": "pkb_recategorize",
        "description": "Move one artifact (and its chunks) from one domain to another. **Use when** an ingest landed in the wrong domain (e.g. a project doc tagged 'general'). Atomic at the Neo4j level; ChromaDB chunks are migrated between collections. **Returns** `{status, artifact_id, old_domain, new_domain, sub_category, chunks_moved}`. For bulk moves use `pkb_recategorize_bulk`.",
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
            "properties": {
                "status": {"type": "string", "description": "'success' on completion"},
                "artifact_id": {"type": "string"},
                "old_domain": {"type": "string"},
                "new_domain": {"type": "string"},
                "sub_category": {"type": "string"},
                "chunks_moved": {"type": "integer", "description": "Number of ChromaDB chunks reassigned"},
            },
        },
    },
    {
        "name": "pkb_triage",
        "description": "Run a file through the full ingestion pipeline — LangGraph routes through parse → categorize → enrich → store. **Use when** processing newly-dropped content where domain isn't known or AI categorization should decide. Slower than `pkb_ingest_file` (involves LLM categorization). **Returns** `{status, artifact_id, chunks, domain, filename, categorize_mode, triage_status}`.",
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
        "description": "READ-then-OPTIONALLY-WRITE health checks. Finds duplicates, stale artifacts (default >90 days), orphaned chunks, and domain distribution skew. With `auto_fix=true` it resolves duplicates + cleans orphans (mutating). **Use when** doing periodic KB hygiene OR forced cleanup. **Returns** `{timestamp, findings: {duplicates, stale, orphans, distribution}}`. Read-only with default args; pass `auto_fix=true` only when you've inspected the findings.",
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
        "description": "READ-ONLY reports: activity summary, ingestion stats, cost estimates, query patterns over the last N hours. **Use when** producing a status digest, debugging usage spikes, or sizing future ops. Pure observability — no mutations. **Returns** `{timestamp, findings: {activity, ingestion, costs, queries}}`. For mutating maintenance use `pkb_maintain`; for hygiene checks use `pkb_rectify`.",
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
        "description": "MUTATING maintenance routines: health probe, stale artifact detection, collection analysis, orphan cleanup. With `auto_purge=true` permanently removes stale artifacts past `stale_days`. **Use when** running scheduled cleanup OR forced manual maintenance. **Returns** `{timestamp, findings}`. Distinct from `pkb_rectify` (which targets duplicate/orphan resolution) and `pkb_audit` (read-only reporting).",
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
        "description": "Score artifact quality across the KB and surface the worst offenders. **Use when** triaging which artifacts to clean up before running `pkb_maintain` with `auto_purge=true`, or building a curation queue. **Returns** `{timestamp, findings: {quality_distribution, low_quality: [{artifact_id, score, reason}]}}`. Cheap (no LLM call); safe to sample-call.",
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
        "description": "Generate a human-readable digest of recent KB activity: new artifacts, connections, top concepts, health bands. **Use when** producing a daily/weekly briefing or onboarding someone to recent changes. Lookback window in hours (default 24, max 168). **Returns** `{timestamp, findings}` with a narrative summary plus enumerated highlights.",
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
        "description": "Inspect the APScheduler job registry — which maintenance jobs are scheduled and when they next run. **Use when** diagnosing whether nightly digests / weekly synthesis / memory archival are actually wired. **Returns** `{status: 'running'|'not_running', jobs: [{id, name, next_run, trigger}]}`.",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "'running' or 'not_running'"},
                "jobs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "next_run": {"type": ["string", "null"]},
                            "trigger": {"type": "string"},
                        },
                    },
                    "description": "Currently-registered scheduled jobs",
                },
            },
        },
    },
    {
        "name": "pkb_check_hallucinations",
        "description": "Fact-check an LLM response by extracting claims and verifying each against the KB via similarity + NLI entailment. **Use when** an assistant just generated factual content and you want a verification gate before showing it. **Returns** `{claims: [{text, support, confidence}], summary, conversation_id, skipped}`. Cost class: high (LLM claim extraction + per-claim retrieval).",
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
        "description": "Extract structured memories (facts, decisions, preferences) from a conversation response and store them in the memory graph. **Use when** persisting context the user will want carried forward across sessions. Memories are scored, deduplicated, and linked to the source conversation. **Returns** `{memories_stored, results: [{memory_id, text, type}]}`.",
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
        "description": "Soft-archive conversation memories older than the retention window (sets `archived=true` on the Neo4j node; excludes from default recall). **Use when** running periodic retention cleanup. **Returns** `{archived_count, retention_days, cutoff_date, timestamp}` — non-mutating to chunks/embeddings, reversible by clearing the `archived` flag.",
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
                "timestamp": {"type": "string", "description": "ISO-8601 archival timestamp"},
                "retention_days": {"type": "integer"},
                "cutoff_date": {"type": "string", "description": "ISO-8601 cutoff (older = archived)"},
                "archived_count": {"type": "integer", "description": "Number of memories archived this run"},
                "error": {"type": "string", "description": "Present only if the archive operation failed"},
            },
        },
    },
    {
        "name": "pkb_memory_recall",
        "description": "Search the memory graph with decay-adjusted scoring. Memories are reinforced by each recall (access count increments). **Use when** building context for a new conversation turn that should remember prior facts/decisions. Distinct from `pkb_agent_query` which searches ingested artifacts; this searches the per-user memory layer. **Returns** `{memories: [{id, text, score, access_count, source, created_at}], total_recalled, timestamp}`.",
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
                            "source": {"type": "string"},
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
        "description": "Search the web (Brave/Bing/DuckDuckGo via configured provider) for information not yet in the KB. **Use when** the answer requires fresh data the KB doesn't have. Results are scored via Self-RAG; pass `auto_ingest=true` to write verified results into the KB (gated by `ENABLE_AUTO_LEARN=true`). **Returns** `{query, results: [{title, url, snippet, score}], provider, ingested_count, timestamp}`.",
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
        "description": "Ingest non-text files (images, audio) by routing through OCR / audio-transcription / vision-LLM plugins, then storing the extracted text as a normal KB artifact. **Use when** the source is an image or audio file rather than text. Requires `CERID_TIER=pro`. Pass `plugin` to force a specific extractor; otherwise auto-detected by file type. **Returns** `{status, artifact_id, chunks, domain, plugin_used, extracted_chars}`.",
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
    {
        "name": "pkb_knowledge_pack_list",
        "description": "List available + installed knowledge packs. Packs are curated baseline corpora (e.g. 'crypto-trading-101', 'rfc-collection') the user can opt into. **Use when** the user wants to know what canned KBs exist or which they've already installed. **Returns** `{available: [pack], installed: [pack]}` with pack metadata grouped by domain.",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "available": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Registry packs grouped by domain",
                },
                "installed": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Packs currently installed",
                },
            },
        },
    },
    {
        "name": "pkb_knowledge_pack_install",
        "description": "Download → verify (hash) → ingest a knowledge pack from the registry. Idempotent at the same version (re-running with the same `pack_id` is a no-op). **Use when** opting into a curated corpus surfaced by `pkb_knowledge_pack_list`. **Returns** `{pack_id, version, domain, artifact_count, installed_at}`. Long-running for big packs; not gated by tier.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_id": {"type": "string", "description": "Registry pack id"},
            },
            "required": ["pack_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "pack_id": {"type": "string"},
                "version": {"type": "string"},
                "domain": {"type": "string"},
                "artifact_count": {"type": "integer"},
                "installed_at": {"type": "string"},
            },
        },
    },
    {
        "name": "pkb_knowledge_pack_uninstall",
        "description": "Permanently remove an installed knowledge pack — drops all its ingested artifacts from Neo4j AND its chunks from ChromaDB. **Use when** retiring a pack the user no longer wants. **DESTRUCTIVE.** No soft-delete; for soft-delete of individual artifacts use `pkb_quarantine`. **Returns** `{pack_id, status, removed, missing}`.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack_id": {"type": "string", "description": "Installed pack id"},
            },
            "required": ["pack_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "pack_id": {"type": "string"},
                "status": {"type": "string"},
                "removed": {"type": "integer"},
                "missing": {"type": "integer"},
            },
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

def _summarize_args(arguments: dict) -> dict[str, Any]:
    """Drop fields larger than 256 chars + redact obvious credentials.

    The audit log records ``args_summary`` so post-incident grep is
    feasible without exposing PII or oversized payloads (e.g. an
    embedded image base64). Keys matching credential-like names are
    stubbed regardless of size.
    """
    _REDACT_KEYS = {"password", "token", "secret", "api_key", "authorization"}
    out: dict[str, Any] = {}
    for k, v in arguments.items():
        if k.lower() in _REDACT_KEYS:
            out[k] = "<redacted>"
        elif isinstance(v, str) and len(v) > 256:
            out[k] = f"<str[{len(v)}]>"
        elif isinstance(v, (list, dict)) and len(repr(v)) > 256:
            out[k] = f"<{type(v).__name__}[len={len(v)}]>"
        else:
            out[k] = v
    return out


async def _dispatch_raw(name: str, arguments: dict) -> Any:
    """Inner dispatcher — runs the resolution chain without instrumentation.

    ``execute_tool`` wraps this with timing + audit logging + metric
    recording so every tool call regardless of which sub-dispatcher
    actually handles it gets observability for free.
    """
    # 1. Decorator-registered tools (Phase 1.6+)
    if name in TOOL_REGISTRY:
        return await execute_registered_tool(name, arguments)

    # 2. Legacy if/elif (pre-Phase-1.6)
    # v0.96.0: pkb_query handler removed — the deprecation alias from
    # v0.95 reached maturity and is no longer dispatched. Callers
    # receive the "Unknown tool: pkb_query" branch below, which is the
    # correct end-state per the deprecation contract.
    if name == "pkb_ingest":
        return await asyncio.to_thread(ingest_content, arguments.get("content", ""), arguments.get("domain", "general"))
    elif name == "pkb_ingest_file":
        return await ingest_file(**arguments)
    elif name == "pkb_health":
        return await asyncio.to_thread(health_check)
    elif name == "pkb_collections":
        return await asyncio.to_thread(list_collections)
    elif name == "pkb_agent_query":
        from core.agents.query_agent import agent_query
        from core.retrieval.surface_router import route as _surface_route

        # Phase K3.3 — surface-aware query.
        # When `surfaces` is omitted, the router classifies the intent and
        # picks the surface set; when provided, we honour it verbatim.
        query_text = arguments.get("query", "")
        requested_surfaces = arguments.get("surfaces") or []
        surface_decision = _surface_route(query_text)
        active_surfaces = (
            requested_surfaces if requested_surfaces else surface_decision.surfaces
        )

        # When the W surface fires AND we matched an entity hint, fetch
        # the wiki page eagerly and pass it as a side-channel context
        # boost. The agent_query path stays untouched for the vector/
        # graph/memory surfaces; wiki context is composed on top.
        wiki_page = None
        if "wiki" in active_surfaces and surface_decision.matched_entity_hint:
            try:
                from app.services.wiki_pages import get_entity_page  # noqa: PLC0415

                neo4j_driver = get_neo4j()
                # Try canonical slug first, then fuzzy by hint.
                wiki_page = await get_entity_page(
                    neo4j_driver, surface_decision.matched_entity_hint,
                )
            except Exception:  # noqa: BLE001
                wiki_page = None

        result = await agent_query(
            query=query_text,
            domains=arguments.get("domains"),
            top_k=arguments.get("top_k", 10),
            use_reranking=arguments.get("use_reranking", True),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            neo4j_driver=get_neo4j(),
        )

        # Attach surface route metadata + wiki page when fetched.
        result["surface_route"] = {
            "primary": surface_decision.primary,
            "surfaces": active_surfaces,
            "intent": surface_decision.intent,
            "confidence": surface_decision.confidence,
            "rationale": surface_decision.rationale,
        }
        if wiki_page is not None:
            wp = wiki_page.model_dump() if hasattr(wiki_page, "model_dump") else dict(wiki_page)
            # Light projection — keep the agent context cheap; full page
            # is available via pkb_wiki_lookup if the caller wants it.
            result["wiki_page"] = {
                "slug": wp.get("slug"),
                "name": wp.get("name"),
                "summary": wp.get("summary"),
                "confidence_band": wp.get("confidence_band"),
                "last_updated_at": wp.get("last_updated_at"),
            }

        return result
    elif name == "pkb_artifacts":
        domain = arguments.get("domain", "") or None
        limit = arguments.get("limit", 50)
        driver = get_neo4j()
        return await asyncio.to_thread(graph.list_artifacts, driver, domain=domain, limit=limit)
    elif name == "pkb_recategorize":
        return await asyncio.to_thread(
            recategorize,
            artifact_id=arguments["artifact_id"],
            new_domain=arguments["new_domain"],
            tags=arguments.get("tags", ""),
        )
    elif name == "pkb_triage":
        from app.agents.triage import triage_file
        triage_result = await triage_file(
            file_path=arguments.get("file_path", ""),
            domain=arguments.get("domain", ""),
            categorize_mode=arguments.get("categorize_mode", ""),
        )
        if triage_result.get("status") == "error":
            return {"status": "error", "error": triage_result.get("error", "Unknown error")}
        result = await asyncio.to_thread(
            ingest_content,
            triage_result["parsed_text"],
            triage_result["domain"],
            metadata=triage_result["metadata"],
        )
        result["filename"] = triage_result["filename"]
        result["categorize_mode"] = triage_result.get("categorize_mode", "")
        result["triage_status"] = triage_result["status"]
        return result
    elif name == "pkb_rectify":
        from core.agents.rectify import rectify
        return await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=arguments.get("checks"),
            auto_fix=arguments.get("auto_fix", False),
            stale_days=arguments.get("stale_days", 90),
        )
    elif name == "pkb_audit":
        from core.agents.audit import audit
        return await audit(
            redis_client=get_redis(),
            reports=arguments.get("reports"),
            hours=arguments.get("hours", 24),
        )
    elif name == "pkb_maintain":
        from core.agents.maintenance import maintain
        return await maintain(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            actions=arguments.get("actions"),
            stale_days=arguments.get("stale_days", 90),
            auto_purge=arguments.get("auto_purge", False),
        )
    elif name == "pkb_curate":
        from app.agents.curator import curate
        return await curate(
            neo4j_driver=get_neo4j(),
            domains=arguments.get("domains"),
            max_artifacts=arguments.get("max_artifacts", 200),
        )
    elif name == "pkb_digest":
        from app.routers.digest import digest_endpoint
        return await digest_endpoint(hours=arguments.get("hours", 24))
    elif name == "pkb_scheduler_status":
        from app.scheduler import get_job_status
        return get_job_status()
    elif name == "pkb_check_hallucinations":
        from core.agents.hallucination import check_hallucinations
        return await check_hallucinations(
            response_text=arguments.get("response_text", ""),
            conversation_id=arguments.get("conversation_id", ""),
            chroma_client=get_chroma(),
            neo4j_driver=get_neo4j(),
            redis_client=get_redis(),
            threshold=arguments.get("threshold"),
        )
    elif name == "pkb_memory_extract":
        from app.agents.memory import extract_and_store_memories
        return await extract_and_store_memories(
            response_text=arguments.get("response_text", ""),
            conversation_id=arguments.get("conversation_id", ""),
            model=arguments.get("model", "unknown"),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            neo4j_driver=get_neo4j(),
        )
    elif name == "pkb_memory_archive":
        from app.agents.memory import archive_old_memories
        return await archive_old_memories(
            neo4j_driver=get_neo4j(),
            retention_days=arguments.get("retention_days", 180),
        )
    elif name == "pkb_memory_recall":
        from app.agents.memory import recall_memories
        from core.utils.time import utcnow_iso as _utcnow_iso
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
                    "source": m.get("memory_type", "fact"),
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
        from app.services.multimodal import ingest_multimodal
        return await ingest_multimodal(
            file_path=arguments.get("file_path", ""),
            domain=arguments.get("domain", "general"),
            tags=arguments.get("tags", ""),
            plugin_override=arguments.get("plugin", ""),
        )
    elif name == "pkb_knowledge_pack_list":
        from app.services.knowledge_packs import (
            default_registry_path,
            default_state_path,
        )
        from core.knowledge.packs import (
            load_install_state,
            load_registry,
        )
        registry = load_registry(default_registry_path())
        state = load_install_state(default_state_path())
        return {
            "available": [p.to_dict() for p in registry.values()],
            "installed": [p.to_dict() for p in state],
        }
    elif name == "pkb_knowledge_pack_install":
        from app.services.knowledge_packs import (
            default_registry_path,
            install_pack_default,
        )
        from core.knowledge.packs import load_registry
        pack_id = arguments.get("pack_id", "")
        registry = load_registry(default_registry_path())
        pack = registry.get(pack_id)
        if pack is None:
            raise ValueError(f"Pack {pack_id!r} not in registry")
        record = await install_pack_default(pack)
        return {
            "pack_id": record.pack_id,
            "version": record.version,
            "domain": record.domain,
            "artifact_count": len(record.artifact_ids),
            "installed_at": record.installed_at,
        }
    elif name == "pkb_knowledge_pack_uninstall":
        from app.services.knowledge_packs import uninstall_pack_default
        return await uninstall_pack_default(arguments.get("pack_id", ""))
    # Try extension tool dispatchers (registered by bootstrap)
    for _dispatcher in _tool_dispatchers:
        result = await _dispatcher(name, arguments)
        if result is not None:
            return result
    raise InvalidToolError(f"Unknown tool: {name}")


async def execute_tool(name: str, arguments: dict) -> Any:
    """Public dispatcher — wraps ``_dispatch_raw`` with observability.

    Every tool call regardless of which sub-dispatcher resolves it
    emits:

    * **Audit log** — ``ai-companion.mcp_tool_audit`` logger, one INFO
      line per call with ``tool_name, args_summary, duration_ms,
      outcome, error_class``. Greppable by name post-incident; PII /
      oversized fields redacted in ``args_summary`` by
      ``_summarize_args``.

    * **Metrics** — ``mcp_tool_call_duration_ms{tool, outcome}`` +
      ``mcp_tool_call{tool, outcome, error_class}`` recorded via the
      ``utils.metrics`` collector. Surfaces in
      ``/observability/metrics`` aggregates and ``/health.invariants``.

    * **Sentry tag** — ``mcp_tool`` is set on the current scope so
      Sentry errors bin per-tool in the dashboard.

    Resolution order: ``TOOL_REGISTRY`` first, then legacy if/elif,
    then ``_tool_dispatchers`` chain (trading + external MCPs). Typed
    errors propagate; the SSE transport maps them onto JSON-RPC codes.
    Legacy callers that catch ``ValueError`` keep working because
    ``InvalidToolError`` derives from ``ToolError`` (not Exception
    subclassing ``ValueError``) — the SSE layer translates correctly.
    """
    start = time.monotonic()
    outcome: str = "ok"
    error_class: str | None = None
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        sentry_sdk.set_tag("mcp_tool", name)
    except Exception as exc:  # noqa: BLE001 — sentry SDK is an optional dep
        log_swallowed_error(__name__, exc)

    try:
        result = await _dispatch_raw(name, arguments)
        return result
    except ToolError as exc:
        outcome = "error"
        error_class = type(exc).__name__
        raise
    except Exception as exc:
        outcome = "error"
        error_class = type(exc).__name__
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000.0
        # Audit log line — structured-by-keyword extras so structlog
        # or log-shipping pipelines can parse them downstream.
        _audit_logger.info(
            "mcp.tool_call",
            extra={
                "tool_name": name,
                "args_summary": _summarize_args(arguments),
                "duration_ms": round(duration_ms, 2),
                "outcome": outcome,
                "error_class": error_class,
            },
        )
        # Metrics (fire-and-forget; never block tool call on metric write)
        try:
            from utils.metrics import get_metrics_collector
            collector = get_metrics_collector()
            tags = {"tool": name, "outcome": outcome}
            if error_class:
                tags["error_class"] = error_class
            collector.record_metric("mcp_tool_call_duration_ms", duration_ms, tags)
            collector.record_metric("mcp_tool_call", 1.0, tags)
        except Exception as exc:  # noqa: BLE001 — metrics infra failure must not propagate
            log_swallowed_error(__name__, exc)


# -- External MCP tools (Sprint 1A.1) ----------------------------------------
# Register the external-MCP dispatcher so ``ext_*`` names served by
# user-configured external servers are routable through ``execute_tool``
# alongside built-in ``pkb_*`` tools. Schemas are merged at request time
# via ``get_all_tools()`` because external tools are discovered on
# server connect, not at module import.
#
# Lives ABOVE the trading-tools hook marker so it survives the to-public
# sync truncation (everything below ``# -- Trading tools`` is stripped
# for the public distribution).
from app.services.external_mcp_dispatch import (  # noqa: E402
    dispatch_external_mcp_tool,
    get_external_tool_schemas,
)

_tool_dispatchers.append(dispatch_external_mcp_tool)


# -- Phase 1.6+ decorator-registered tools -----------------------------------
# Importing the mcp_tools package triggers each module's @register_tool
# decorators. ``TOOL_REGISTRY`` is populated as a side-effect. Order
# matters only insofar as legacy-registry collisions must fail loudly
# (handled in tool_registry.register_tool).
import app.mcp_tools  # noqa: E402, F401


def get_all_tools() -> list[dict]:
    """Return the full tool palette: registered + legacy + external.

    Composition order:
      1. ``TOOL_REGISTRY`` entries (Phase 1.6+ decorator-registered).
      2. Legacy ``MCP_TOOLS`` entries (pre-Phase-1.6 list-of-dicts).
      3. External MCP server schemas (discovered at runtime).

    Each section is internally sorted for stability across requests
    so the LLM's tool-list cache stays warm.
    """
    return [
        *get_registered_schemas(),
        *MCP_TOOLS,
        *get_external_tool_schemas(),
    ]
