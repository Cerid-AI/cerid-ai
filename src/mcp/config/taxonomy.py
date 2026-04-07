# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Domain taxonomy, supported extensions, and cross-domain affinity."""
from __future__ import annotations

import json
import os

# ---------------------------------------------------------------------------
# Hierarchical Taxonomy (domains -> sub-categories -> tags)
# ---------------------------------------------------------------------------
TAXONOMY = {
    "coding": {
        "description": "Source code, scripts, technical documentation",
        "icon": "code",
        "sub_categories": ["python", "javascript", "devops", "architecture", "general"],
    },
    "finance": {
        "description": "Financial documents, tax records, budgets",
        "icon": "dollar-sign",
        "sub_categories": ["tax", "investments", "budgets", "receipts", "general"],
    },
    "projects": {
        "description": "Project plans, meeting notes, specifications",
        "icon": "folder",
        "sub_categories": ["active", "archived", "proposals", "general"],
    },
    "personal": {
        "description": "Personal notes, journal entries, health records",
        "icon": "user",
        "sub_categories": ["notes", "health", "travel", "general"],
    },
    "general": {
        "description": "Uncategorized or cross-domain content",
        "icon": "file",
        "sub_categories": ["general"],
    },
    "conversations": {
        "description": "Extracted memories from chat sessions",
        "icon": "message-circle",
        "sub_categories": ["facts", "decisions", "preferences", "action-items", "general"],
    },
    "trading": {
        "description": "Automated trading signals, market analysis, execution logs, and strategy research",
        "icon": "trending-up",
        "sub_categories": [
            "signals", "market-analysis", "execution", "post-analysis",
            "strategy-research", "risk-analysis", "general",
        ],
    },
}

# User-defined custom domains via env var (JSON object with same shape as TAXONOMY entries)
_custom_domains_raw = os.getenv("CERID_CUSTOM_DOMAINS", "")
if _custom_domains_raw:
    try:
        _custom = json.loads(_custom_domains_raw)
        if isinstance(_custom, dict):
            TAXONOMY.update(_custom)
    except (json.JSONDecodeError, TypeError):
        pass  # silently ignore malformed custom domains

DOMAINS = list(TAXONOMY.keys())
DEFAULT_DOMAIN = "general"
DEFAULT_SUB_CATEGORY = "general"
INBOX_DOMAIN = "inbox"  # files here trigger AI categorization


def collection_name(domain: str, *, namespace: str | None = None) -> str:
    """ChromaDB collection name for a given domain.

    When ``namespace`` is provided (or ``KB_NAMESPACE`` env var is set to
    something other than ``"default"``), collections are prefixed for
    multi-KB isolation: ``kb_{namespace}_{domain}``.

    Backward compatible: default namespace uses legacy ``domain_{slug}`` format.
    """
    import os

    ns = namespace or os.getenv("KB_NAMESPACE", "")
    slug = domain.replace(" ", "_").lower()
    if ns and ns != "default":
        return f"kb_{ns}_{slug}"
    return f"domain_{slug}"


# ---------------------------------------------------------------------------
# Supported file extensions (mapped to parser functions in utils/parsers.py)
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {
    # Documents
    ".pdf", ".docx", ".xlsx", ".csv", ".tsv",
    # E-books & rich text
    ".epub", ".rtf",
    # Email
    ".eml", ".mbox",
    # Text / markup
    ".txt", ".md", ".rst", ".log",
    ".html", ".htm", ".xml",
    # Code
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".rb", ".cpp", ".c", ".h", ".cs",
    ".sql", ".r", ".swift", ".kt",
    ".sh", ".bash",
    # Config / data
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
}

# ---------------------------------------------------------------------------
# Cross-Domain Connections
# ---------------------------------------------------------------------------
DOMAIN_AFFINITY = {
    "coding":        {"projects": 0.6},
    "projects":      {"coding": 0.6, "finance": 0.4},
    "finance":       {"projects": 0.4, "trading": 0.3},
    "trading":       {"finance": 0.3},
    "personal":      {"general": 0.5, "conversations": 0.3},
    "general":       {"personal": 0.5, "conversations": 0.3},
    "conversations": {"personal": 0.3, "general": 0.3},
}
CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2   # weight for domain pairs not in DOMAIN_AFFINITY


# ---------------------------------------------------------------------------
# Per-Domain Tag Vocabulary (controlled vocabulary for constrained tagging)
# ---------------------------------------------------------------------------
# Tags in the vocabulary are preferred during AI categorization and surfaced
# first in typeahead suggestions.  Free-form tags are still allowed but
# vocabulary tags are boosted in quality scoring.
TAG_VOCABULARY: dict[str, list[str]] = {
    "coding": [
        "python", "javascript", "typescript", "docker", "api", "cli",
        "testing", "debugging", "refactoring", "architecture", "database",
        "security", "performance", "ci-cd", "git", "frontend", "backend",
        "documentation", "config", "automation", "data-pipeline",
    ],
    "finance": [
        "tax-return", "invoice", "receipt", "budget", "investment",
        "expense", "income", "bank-statement", "tax-deduction", "payroll",
        "insurance", "retirement", "mortgage", "credit-card", "report",
    ],
    "projects": [
        "meeting-notes", "specification", "proposal", "roadmap", "design",
        "requirements", "milestone", "retrospective", "status-update",
        "architecture", "timeline", "stakeholder", "risk", "deliverable",
    ],
    "personal": [
        "journal", "health", "travel", "recipe", "workout", "meditation",
        "goal", "habit", "book-notes", "learning", "family", "gratitude",
        "planning", "reflection", "inspiration",
    ],
    "general": [
        "reference", "tutorial", "how-to", "research", "notes",
        "bookmark", "template", "cheatsheet", "summary", "faq",
    ],
    "conversations": [
        "fact", "decision", "preference", "action-item", "insight",
        "question", "recommendation", "follow-up", "context", "memory",
    ],
    "trading": [
        "trading-signal", "herd-detection", "kelly-sizing", "cascade-liquidation",
        "longshot-surface", "market-analysis", "risk-management", "position-sizing",
        "entry-trigger", "exit-strategy", "backtest", "performance-analysis",
        "volatility", "sentiment", "correlation", "arbitrage", "execution-log",
    ],
}
