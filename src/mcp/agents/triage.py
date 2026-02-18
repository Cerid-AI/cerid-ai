"""
Triage Agent - Intelligent ingestion routing with LangGraph orchestration.

Wraps the existing ingestion pipeline with:
- Conditional routing based on file type and content characteristics
- Batch processing with per-file error recovery
- AI categorization gating (skip AI for known domains, use AI for inbox)
- Structured state tracking through the ingestion lifecycle
- Audit trail integration via Redis
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph, END

import config
from utils.parsers import parse_file, PARSER_REGISTRY
from utils.metadata import extract_metadata, ai_categorize
from utils.chunker import chunk_text

logger = logging.getLogger("ai-companion.triage")


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

@dataclass
class TriageState:
    """State carried through the triage graph for a single file."""
    file_path: str = ""
    filename: str = ""
    domain: str = ""
    categorize_mode: str = ""
    tags: str = ""

    # Populated during processing
    parsed_text: str = ""
    file_type: str = ""
    page_count: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunks: List[str] = field(default_factory=list)
    content_hash: str = ""
    artifact_id: str = ""

    # Routing decisions
    needs_ai_categorization: bool = False
    is_structured: bool = False  # PDF/XLSX/CSV with tables

    # Result
    status: str = "pending"  # pending | parsed | categorized | ingested | error | duplicate
    error: str = ""
    result: Dict[str, Any] = field(default_factory=dict)


# State dict type for LangGraph (uses dict internally)
TriageStateDict = Dict[str, Any]


def _state_from_dict(d: TriageStateDict) -> TriageState:
    """Convert LangGraph state dict to TriageState."""
    s = TriageState()
    for k, v in d.items():
        if hasattr(s, k):
            setattr(s, k, v)
    return s


def _state_to_dict(s: TriageState) -> TriageStateDict:
    """Convert TriageState to dict for LangGraph."""
    return {
        "file_path": s.file_path,
        "filename": s.filename,
        "domain": s.domain,
        "categorize_mode": s.categorize_mode,
        "tags": s.tags,
        "parsed_text": s.parsed_text,
        "file_type": s.file_type,
        "page_count": s.page_count,
        "metadata": s.metadata,
        "chunks": s.chunks,
        "content_hash": s.content_hash,
        "artifact_id": s.artifact_id,
        "needs_ai_categorization": s.needs_ai_categorization,
        "is_structured": s.is_structured,
        "status": s.status,
        "error": s.error,
        "result": s.result,
    }


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

    updates = {}
    if ai_result.get("suggested_domain"):
        updates["domain"] = ai_result["suggested_domain"]
    if ai_result.get("keywords"):
        updates["metadata"] = {
            **state.get("metadata", {}),
            "keywords": json.dumps(ai_result["keywords"]),
        }
    if ai_result.get("summary"):
        meta = updates.get("metadata", state.get("metadata", {}))
        meta["summary"] = ai_result["summary"]
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
    chunks = chunk_text(text, max_tokens=config.CHUNK_MAX_TOKENS, overlap=config.CHUNK_OVERLAP)
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


def should_continue_after_categorize(state: TriageStateDict) -> str:
    """Route after AI categorization."""
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
    graph = StateGraph(dict)

    # Add nodes
    graph.add_node("validate", validate_node)
    graph.add_node("parse", parse_node)
    graph.add_node("route_categorization", route_categorization)
    graph.add_node("categorize", categorize_node)
    graph.add_node("extract_metadata", extract_metadata_node)
    graph.add_node("chunk", chunk_node)
    graph.add_node("error_end", lambda state: state)  # passthrough for errors

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
) -> Dict[str, Any]:
    """
    Run a single file through the triage pipeline.

    Returns the final state dict with parsed_text, metadata, chunks, domain,
    and status. The caller (main.py) handles the actual DB writes (ChromaDB,
    Neo4j, Redis) using the prepared data.

    Args:
        file_path: Absolute path to the file
        domain: Target domain (empty = auto-detect)
        categorize_mode: manual/smart/pro (empty = env default)
        tags: Optional tags string

    Returns:
        Final triage state dict with all prepared data
    """
    initial_state = {
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
    files: List[Dict[str, str]],
    default_mode: str = "",
) -> List[Dict[str, Any]]:
    """
    Process a batch of files through triage, collecting results.

    Each file is processed independently — one failure doesn't stop the batch.

    Args:
        files: List of dicts with keys: file_path, domain (optional), tags (optional)
        default_mode: Default categorization mode for the batch

    Returns:
        List of triage results (one per file)
    """
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
