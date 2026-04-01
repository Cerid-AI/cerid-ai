# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Triage Agent — LangGraph-orchestrated ingestion routing."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

import config
from errors import RoutingError
from parsers import PARSER_REGISTRY, parse_file
from utils.chunker import chunk_text, make_context_header
from utils.metadata import ai_categorize, extract_metadata

logger = logging.getLogger("ai-companion.triage")


# ---------------------------------------------------------------------------
# Structured triage result (bridge to ingestion service)
# ---------------------------------------------------------------------------

@dataclass
class TriageResult:
    """Structured output from triage pipeline, consumed by ingestion service."""

    quality_score: float = 0.6  # 0.0-1.0, mapped from 1-5 triage_score
    recommended_domain: str = ""
    should_ingest: bool = True
    skip_reason: str | None = None
    suggested_tags: list[str] = field(default_factory=list)


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
    from utils.agent_events import emit_agent_event

    filename = state.get("filename", Path(state["file_path"]).name)
    file_type = state.get("file_type", Path(state["file_path"]).suffix.lstrip(".")).upper()
    emit_agent_event("triage", f"{file_type} detected \u2014 {filename}, running extraction.")

    try:
        parsed = parse_file(state["file_path"])
        is_structured = parsed.get("table_count", 0) > 0 or state.get("file_type") in ("xlsx", "csv")
        page_count = parsed.get("page_count")

        if page_count:
            emit_agent_event("triage", f"{filename}: {page_count} pages extracted.")

        return {
            **state,
            "parsed_text": parsed["text"],
            "file_type": parsed.get("file_type", state.get("file_type", "")),
            "page_count": page_count,
            "is_structured": is_structured,
            "status": "parsed",
        }
    except (FileNotFoundError, ValueError) as e:
        return {**state, "status": "error", "error": str(e)}
    except (RoutingError, ValueError, OSError, RuntimeError) as e:
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
# AI Triage Scoring (Ollama, optional)
# ---------------------------------------------------------------------------

_TRIAGE_SCORE_PROMPT = (
    "Rate the following text content on a scale of 1-5 for knowledge value.\n"
    "1 = junk/spam/empty, 2 = low value (boilerplate, logs), "
    "3 = moderate (general info), 4 = good (useful reference), "
    "5 = excellent (high-quality knowledge).\n\n"
    "Respond with ONLY a single digit (1-5).\n\n"
    "Filename: {filename}\n"
    "Content (first 500 chars):\n{content}"
)


async def score_content_node(state: TriageStateDict) -> TriageStateDict:
    """Score content value using Ollama (1-5). Skips if disabled or unavailable."""
    if not getattr(config, "ENABLE_AI_TRIAGE", False):
        return {**state, "triage_score": 3}  # neutral default

    text = state.get("parsed_text", "")
    filename = state.get("filename", "")

    # Only score if we have meaningful text
    if len(text.strip()) < 50:
        return {**state, "triage_score": 2}

    try:
        from utils.llm_client import llm_call

        prompt = _TRIAGE_SCORE_PROMPT.format(
            filename=filename,
            content=text[:500],
        )
        response = await llm_call(
            prompt,
            provider="ollama",
            max_tokens=5,
            temperature=0.0,
        )
        # Parse the score from response
        score_text = response.strip()
        score = int(score_text[0]) if score_text and score_text[0].isdigit() else 3
        score = max(1, min(5, score))

        if score < 2:
            logger.info("AI triage: '%s' scored %d — skipping ingestion", filename, score)
            return {**state, "triage_score": score, "status": "error", "error": f"AI triage score {score}/5 — content too low value"}

        return {**state, "triage_score": score}
    except (RoutingError, ValueError, OSError, RuntimeError) as e:
        logger.debug("AI triage scoring failed (falling back to default): %s", e)
        return {**state, "triage_score": 3}  # fallback: ingest everything


def should_continue_after_triage(state: TriageStateDict) -> str:
    """Route after triage scoring: skip if score too low, else continue."""
    if state.get("status") == "error":
        return "error_end"
    return "route_categorization"


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def should_continue_after_validate(state: TriageStateDict) -> str:
    """Route after validation: error or parse."""
    if state.get("status") == "error":
        return "error_end"
    return "parse"


def should_continue_after_parse(state: TriageStateDict) -> str:
    """Route after parsing: error or AI triage scoring."""
    if state.get("status") == "error":
        return "error_end"
    return "score_content"


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
    graph.add_node("score_content", score_content_node)  # type: ignore[type-var]
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
        "score_content": "score_content",
    })
    graph.add_conditional_edges("score_content", should_continue_after_triage, {
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

def _state_to_triage_result(state: dict[str, Any]) -> TriageResult:
    """Convert final graph state dict into a structured TriageResult."""
    # Map 1-5 triage_score to 0.0-1.0 quality_score
    raw_score = state.get("triage_score", 3)
    quality_score = round(max(0.0, min(1.0, (raw_score - 1) / 4)), 2)

    is_error = state.get("status") == "error"
    skip_reason = state.get("error") if is_error else None

    # Extract tags from metadata
    tags_list: list[str] = []
    meta = state.get("metadata", {})
    tags_json = meta.get("tags_json", "")
    if tags_json:
        try:
            parsed_tags = json.loads(tags_json)
            if isinstance(parsed_tags, list):
                tags_list = [str(t) for t in parsed_tags]
        except (json.JSONDecodeError, TypeError):
            pass
    # Also pick up keywords as supplementary tags
    kw_json = meta.get("keywords_json", "")
    if kw_json:
        try:
            kws = json.loads(kw_json)
            if isinstance(kws, list):
                tags_list.extend(str(k) for k in kws if str(k) not in tags_list)
        except (json.JSONDecodeError, TypeError):
            pass

    return TriageResult(
        quality_score=quality_score,
        recommended_domain=state.get("domain", ""),
        should_ingest=not is_error,
        skip_reason=skip_reason,
        suggested_tags=tags_list,
    )


async def triage_file(
    file_path: str,
    domain: str = "",
    categorize_mode: str = "",
    tags: str = "",
) -> dict[str, Any]:
    """Run a single file through the triage pipeline, returning prepared state.

    The returned dict includes a ``triage_result`` key containing a
    :class:`TriageResult` dataclass for consumption by the ingestion service.
    """
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

    # Attach structured triage result for downstream consumers
    final_state["triage_result"] = _state_to_triage_result(final_state)

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
        except (RoutingError, ValueError, OSError, RuntimeError) as e:
            logger.error(f"Triage failed for {file_spec.get('file_path', '?')}: {e}")
            results.append({
                "file_path": file_spec.get("file_path", ""),
                "filename": Path(file_spec.get("file_path", "")).name,
                "status": "error",
                "error": str(e),
            })

    return results
