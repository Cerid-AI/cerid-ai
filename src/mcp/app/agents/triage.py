# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Triage Agent — LangGraph-orchestrated ingestion routing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

import config
from app.parsers import PARSER_REGISTRY, parse_file
from utils.chunker import chunk_text, make_context_header
from utils.metadata import ai_categorize, extract_metadata

logger = logging.getLogger("ai-companion.triage")


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

# State dict type for LangGraph (uses dict internally)
TriageStateDict = dict[str, Any]


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def validate_node(state: TriageStateDict) -> TriageStateDict:
    """Validate file exists, extension is supported, file is non-empty."""
    path = Path(state["file_path"])

    if not path.exists():
        return {**state, "status": "error", "error": f"File not found: {state['file_path']}"}

    if path.stat().st_size == 0:
        return {**state, "status": "error", "error": f"File is empty: {path.name}"}

    ext = path.suffix.lower()
    if ext not in PARSER_REGISTRY:
        return {**state, "status": "error", "error": f"Unsupported file type: {ext}"}

    return {**state, "filename": path.name, "file_type": ext.lstrip(".")}


def parse_node(state: TriageStateDict) -> TriageStateDict:
    """Parse file content using the appropriate parser."""
    try:
        parsed = parse_file(state["file_path"])
        is_structured = parsed.get("table_count", 0) > 0 or state.get("file_type") in ("xlsx", "csv")

        return {
            **state,
            "parsed_text": parsed["text"],
            "file_type": parsed.get("file_type", state.get("file_type", "")),
            "page_count": parsed.get("page_count"),
            "is_structured": is_structured,
            "status": "parsed",
        }
    except (FileNotFoundError, ValueError) as e:
        return {**state, "status": "error", "error": str(e)}
    except Exception as e:
        return {**state, "status": "error", "error": f"Parse failed: {e}"}


def route_categorization(state: TriageStateDict) -> TriageStateDict:
    """Decide whether AI categorization is needed."""
    domain = state.get("domain", "")
    mode = state.get("categorize_mode", "") or config.CATEGORIZE_MODE

    # If domain is explicitly set and valid, skip AI
    if domain and domain in config.DOMAINS:
        return {
            **state,
            "needs_ai_categorization": False,
            "categorize_mode": "manual",
        }

    # If mode is manual, use default domain
    if mode == "manual":
        return {
            **state,
            "domain": config.DEFAULT_DOMAIN,
            "needs_ai_categorization": False,
            "categorize_mode": "manual",
        }

    # AI categorization needed
    return {
        **state,
        "needs_ai_categorization": True,
        "categorize_mode": mode,
    }


async def categorize_node(state: TriageStateDict) -> TriageStateDict:
    """Run AI categorization via Bifrost if needed."""
    if not state.get("needs_ai_categorization"):
        return state

    mode = state.get("categorize_mode", "smart")
    text = state.get("parsed_text", "")
    filename = state.get("filename", "")

    ai_result = await ai_categorize(text, filename, mode)

    updates: dict[str, Any] = {}
    if ai_result.get("suggested_domain"):
        updates["domain"] = ai_result["suggested_domain"]
    if ai_result.get("keywords"):
        updates["metadata"] = {
            **state.get("metadata", {}),
            "keywords": json.dumps(ai_result["keywords"]),
        }
    if ai_result.get("summary"):
        meta = dict(updates.get("metadata") or state.get("metadata") or {})
        meta["summary"] = ai_result["summary"]
        updates["metadata"] = meta

    if ai_result.get("sub_category"):
        meta = dict(updates.get("metadata") or state.get("metadata") or {})
        meta["sub_category"] = ai_result["sub_category"]
        updates["metadata"] = meta
    if ai_result.get("tags"):
        meta = dict(updates.get("metadata") or state.get("metadata") or {})
        meta["tags_json"] = json.dumps(ai_result["tags"])
        updates["metadata"] = meta

    # Fallback if AI didn't produce a valid domain
    if not updates.get("domain") or updates["domain"] not in config.DOMAINS:
        updates["domain"] = config.DEFAULT_DOMAIN

    updates["status"] = "categorized"
    return {**state, **updates}


def extract_metadata_node(state: TriageStateDict) -> TriageStateDict:
    """Extract local metadata (no API calls)."""
    text = state.get("parsed_text", "")
    filename = state.get("filename", "")
    domain = state.get("domain", config.DEFAULT_DOMAIN)

    meta = extract_metadata(text, filename, domain)

    # Merge with any AI-produced metadata (AI overrides local for keywords/summary)
    existing_meta = state.get("metadata", {})
    merged = {**meta, **existing_meta}

    # Add file-type-specific fields
    merged["file_type"] = state.get("file_type", "")
    if state.get("page_count") is not None:
        merged["page_count"] = state["page_count"]
    if state.get("tags"):
        merged["tags"] = state["tags"]
    if state.get("categorize_mode"):
        merged["categorize_mode"] = state["categorize_mode"]
    if state.get("needs_ai_categorization"):
        merged["ai_categorized"] = "true"

    return {**state, "metadata": merged}


def chunk_node(state: TriageStateDict) -> TriageStateDict:
    """Chunk the parsed text for vector storage."""
    text = state.get("parsed_text", "")
    ctx_header = make_context_header(
        filename=state.get("filename", ""),
        domain=state.get("domain", ""),
        sub_category=state.get("metadata", {}).get("sub_category", ""),
    )
    chunks = chunk_text(
        text, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP,
        context_header=ctx_header,
    )
    return {**state, "chunks": chunks}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def should_continue_after_validate(state: TriageStateDict) -> str:
    """Route after validation: error or parse."""
    if state.get("status") == "error":
        return "error_end"
    return "parse"


def should_continue_after_parse(state: TriageStateDict) -> str:
    """Route after parsing: error or categorization routing."""
    if state.get("status") == "error":
        return "error_end"
    return "route_categorization"


def should_categorize(state: TriageStateDict) -> str:
    """Route after categorization decision."""
    if state.get("needs_ai_categorization"):
        return "categorize"
    return "extract_metadata"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_triage_graph() -> StateGraph:
    """
    Build the LangGraph triage workflow.

    Flow:
        validate -> parse -> route_categorization -> [categorize?] -> extract_metadata -> chunk -> END

    Error handling: any node can set status="error", routing to error_end.
    """
    graph = StateGraph(dict)  # type: ignore[type-var]

    # Add nodes
    graph.add_node("validate", validate_node)  # type: ignore[type-var]
    graph.add_node("parse", parse_node)  # type: ignore[type-var]
    graph.add_node("route_categorization", route_categorization)  # type: ignore[type-var]
    graph.add_node("categorize", categorize_node)  # type: ignore[type-var]
    graph.add_node("extract_metadata", extract_metadata_node)  # type: ignore[type-var]
    graph.add_node("chunk", chunk_node)  # type: ignore[type-var]
    graph.add_node("error_end", lambda state: state)  # type: ignore[type-var]

    # Set entry point
    graph.set_entry_point("validate")

    # Add edges with conditional routing
    graph.add_conditional_edges("validate", should_continue_after_validate, {
        "error_end": "error_end",
        "parse": "parse",
    })
    graph.add_conditional_edges("parse", should_continue_after_parse, {
        "error_end": "error_end",
        "route_categorization": "route_categorization",
    })
    graph.add_conditional_edges("route_categorization", should_categorize, {
        "categorize": "categorize",
        "extract_metadata": "extract_metadata",
    })
    graph.add_edge("categorize", "extract_metadata")
    graph.add_edge("extract_metadata", "chunk")
    graph.add_edge("chunk", END)
    graph.add_edge("error_end", END)

    return graph


# Compile the graph once at module level
_triage_graph = None


def get_triage_graph():
    """Get or build the compiled triage graph."""
    global _triage_graph
    if _triage_graph is None:
        _triage_graph = build_triage_graph().compile()
    return _triage_graph


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def triage_file(
    file_path: str,
    domain: str = "",
    categorize_mode: str = "",
    tags: str = "",
) -> dict[str, Any]:
    """Run a single file through the triage pipeline, returning prepared state."""
    initial_state: dict[str, Any] = {
        "file_path": file_path,
        "filename": Path(file_path).name,
        "domain": domain,
        "categorize_mode": categorize_mode,
        "tags": tags,
        "parsed_text": "",
        "file_type": "",
        "page_count": None,
        "metadata": {},
        "chunks": [],
        "content_hash": "",
        "artifact_id": "",
        "needs_ai_categorization": False,
        "is_structured": False,
        "status": "pending",
        "error": "",
        "result": {},
    }

    graph = get_triage_graph()
    final_state = await graph.ainvoke(initial_state)

    return final_state


async def triage_batch(
    files: list[dict[str, str]],
    default_mode: str = "",
) -> list[dict[str, Any]]:
    """Process a batch of files independently — one failure doesn't stop the batch."""
    results = []
    for file_spec in files:
        try:
            result = await triage_file(
                file_path=file_spec["file_path"],
                domain=file_spec.get("domain", ""),
                categorize_mode=file_spec.get("categorize_mode", default_mode),
                tags=file_spec.get("tags", ""),
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Triage failed for {file_spec.get('file_path', '?')}: {e}")
            results.append({
                "file_path": file_spec.get("file_path", ""),
                "filename": Path(file_spec.get("file_path", "")).name,
                "status": "error",
                "error": str(e),
            })

    return results
