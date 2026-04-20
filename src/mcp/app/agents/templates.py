# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Built-in agent templates for quick custom-agent creation (Phase 3 — extensibility).

Each template provides sensible defaults so users can spin up a new agent
with a single ``POST /custom-agents/from-template/{template_id}`` call and
optionally override individual fields afterward.
"""
from __future__ import annotations

from typing import Any

AGENT_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "research-assistant",
        "name": "Research Assistant",
        "description": (
            "A general-purpose research agent that searches the entire knowledge base, "
            "synthesises findings into concise answers, and cites its sources."
        ),
        "system_prompt": (
            "You are a meticulous research assistant. When answering a question, search the "
            "knowledge base thoroughly, cross-reference multiple sources, and provide a clear, "
            "well-structured answer with inline citations. If the evidence is inconclusive, "
            "say so explicitly rather than speculating."
        ),
        "tools": ["pkb_query", "pkb_list_artifacts", "web_search"],
        "domains": [],
        "rag_mode": "smart",
        "temperature": 0.4,
    },
    {
        "template_id": "code-reviewer",
        "name": "Code Reviewer",
        "description": (
            "Reviews code snippets for correctness, style, security issues, and "
            "performance pitfalls, drawing on the code-related knowledge base."
        ),
        "system_prompt": (
            "You are a senior code reviewer. Analyse the provided code for bugs, security "
            "vulnerabilities, style violations, and performance issues. Reference relevant "
            "documentation from the knowledge base when suggesting improvements. Be direct "
            "and actionable — prioritise the most impactful findings first."
        ),
        "tools": ["pkb_query", "pkb_list_artifacts"],
        "domains": ["code"],
        "rag_mode": "smart",
        "temperature": 0.3,
    },
    {
        "template_id": "fact-checker",
        "name": "Fact Checker",
        "description": (
            "Verifies claims against the knowledge base and external sources, returning "
            "a verdict with supporting evidence for each claim."
        ),
        "system_prompt": (
            "You are a rigorous fact-checker. Break the user's statement into individual "
            "claims, then verify each claim against the knowledge base and available sources. "
            "For each claim, provide a verdict (Verified, Unverified, or Uncertain) with the "
            "evidence that supports your assessment. Never assume — if evidence is lacking, "
            "mark the claim as Uncertain."
        ),
        "tools": ["pkb_query", "pkb_verify", "web_search"],
        "domains": [],
        "rag_mode": "smart",
        "temperature": 0.2,
    },
    {
        "template_id": "knowledge-curator",
        "name": "Knowledge Curator",
        "description": (
            "Helps organise, tag, and improve knowledge base content by suggesting "
            "re-categorisations, missing metadata, and quality improvements."
        ),
        "system_prompt": (
            "You are a knowledge curator responsible for maintaining a high-quality knowledge "
            "base. When asked, review artifacts for accurate categorisation, suggest missing "
            "tags or metadata, identify duplicate or outdated content, and recommend quality "
            "improvements. Prioritise actionable suggestions that increase discoverability "
            "and reduce noise."
        ),
        "tools": [
            "pkb_query",
            "pkb_list_artifacts",
            "pkb_recategorize",
            "pkb_delete_artifact",
        ],
        "domains": [],
        "rag_mode": "simple",
        "temperature": 0.5,
    },
]


def get_template(template_id: str) -> dict[str, Any] | None:
    """Look up a template by its ID. Returns *None* if not found."""
    for tpl in AGENT_TEMPLATES:
        if tpl["template_id"] == template_id:
            return tpl
    return None


def list_templates() -> list[dict[str, Any]]:
    """Return all available templates (safe to serialise directly)."""
    return AGENT_TEMPLATES
