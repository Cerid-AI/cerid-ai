# Copyright (c) 2026 Justin Michaels. All rights reserved.
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
    # --- Boardroom domains (gated by CERID_BOARDROOM_ENABLED) ---
    "strategy": {
        "description": "Corporate strategy, board decisions, OKRs, risk assessments, competitive positioning",
        "icon": "target",
        "sub_categories": ["decisions", "pivots", "okrs", "risk", "positioning", "general"],
    },
    "competitive_intel": {
        "description": "Competitor profiles, market research, trends, regulatory monitoring",
        "icon": "search",
        "sub_categories": ["competitors", "market", "trends", "regulatory", "general"],
    },
    "marketing": {
        "description": "Marketing campaigns, content strategy, audiences, channel performance",
        "icon": "megaphone",
        "sub_categories": ["campaigns", "content", "audiences", "channels", "general"],
    },
    "advertising": {
        "description": "Ad platform data — Google Ads, Meta/Instagram, X campaigns and creative",
        "icon": "zap",
        "sub_categories": ["google_ads", "meta_ads", "x_ads", "performance", "creative", "general"],
    },
    "operations": {
        "description": "Business operations — processes, SOPs, vendors, resources, sprint plans",
        "icon": "settings",
        "sub_categories": ["processes", "sops", "vendors", "resources", "sprints", "general"],
    },
    "audit": {
        "description": "Boardroom audit trail — agent actions, approvals, budget usage",
        "icon": "shield",
        "sub_categories": ["actions", "approvals", "budget_usage", "agent_logs", "general"],
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


def collection_name(domain: str) -> str:
    """ChromaDB collection name for a given domain."""
    return f"domain_{domain.replace(' ', '_').lower()}"


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
    "strategy":      {"competitive_intel": 0.6, "finance": 0.4, "marketing": 0.3, "operations": 0.3},
    "competitive_intel": {"strategy": 0.6, "marketing": 0.4},
    "marketing":     {"advertising": 0.7, "strategy": 0.3, "competitive_intel": 0.4},
    "advertising":   {"marketing": 0.7},
    "operations":    {"strategy": 0.3, "finance": 0.3},
    "audit":         {},
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
    # --- Boardroom tag vocabularies ---
    "strategy": [
        "board-decision", "strategic-pivot", "okr", "key-result", "risk-assessment",
        "competitive-position", "swot", "pestel", "vision", "mission", "quarterly-review",
        "goal-cascade", "strategy-brief", "board-deck", "scenario-analysis",
    ],
    "competitive_intel": [
        "competitor-profile", "market-sizing", "trend-analysis", "regulatory-change",
        "pricing-intel", "product-launch", "market-share", "industry-news",
        "patent-filing", "acquisition", "partnership", "benchmark",
    ],
    "marketing": [
        "campaign-brief", "content-calendar", "audience-segment", "brand-voice",
        "channel-strategy", "cac-analysis", "ltv-model", "conversion-rate",
        "email-campaign", "social-post", "landing-page", "creative-asset",
    ],
    "advertising": [
        "google-ads", "meta-ads", "instagram-ads", "x-ads", "reels-campaign",
        "stories-campaign", "carousel-ad", "search-campaign", "display-campaign",
        "ad-creative", "bid-optimization", "a-b-test", "roas", "ctr", "cpc",
    ],
    "operations": [
        "sop", "process-map", "vendor-evaluation", "resource-allocation",
        "sprint-plan", "retrospective", "onboarding", "workflow", "bottleneck",
        "capacity-planning", "incident-report", "meeting-notes", "action-item",
    ],
    "audit": [
        "agent-action", "approval-request", "budget-spend", "governance-event",
        "content-review", "kill-switch", "anomaly-alert", "rollback",
    ],
}
