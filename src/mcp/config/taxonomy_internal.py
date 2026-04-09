"""Internal-only taxonomy extensions -- trading + boardroom domains.

This file exists only in cerid-ai-internal. The bootstrap in
main_internal.py calls extend_taxonomy() at startup, before any
router reads TAXONOMY or DOMAINS.
"""
from __future__ import annotations


def extend_taxonomy() -> None:
    """Add trading + boardroom domains to the taxonomy module namespace."""
    import config.taxonomy as _t

    # -- Trading domain ------------------------------------------------------
    _t.TAXONOMY["trading"] = {
        "description": "Automated trading signals, market analysis, execution logs, and strategy research",
        "icon": "trending-up",
        "sub_categories": [
            "signals", "market-analysis", "execution", "post-analysis",
            "strategy-research", "risk-analysis", "general",
        ],
    }

    # -- Boardroom domains ---------------------------------------------------
    _t.TAXONOMY["strategy"] = {
        "description": "Corporate strategy, board decisions, OKRs, risk assessments, competitive positioning",
        "icon": "target",
        "sub_categories": ["decisions", "pivots", "okrs", "risk", "positioning", "general"],
    }
    _t.TAXONOMY["competitive_intel"] = {
        "description": "Competitor profiles, market research, trends, regulatory monitoring",
        "icon": "search",
        "sub_categories": ["competitors", "market", "trends", "regulatory", "general"],
    }
    _t.TAXONOMY["marketing"] = {
        "description": "Marketing campaigns, content strategy, audiences, channel performance",
        "icon": "megaphone",
        "sub_categories": ["campaigns", "content", "audiences", "channels", "general"],
    }
    _t.TAXONOMY["advertising"] = {
        "description": "Ad platform data -- Google Ads, Meta/Instagram, X campaigns and creative",
        "icon": "zap",
        "sub_categories": ["google_ads", "meta_ads", "x_ads", "performance", "creative", "general"],
    }
    _t.TAXONOMY["operations"] = {
        "description": "Business operations -- processes, SOPs, vendors, resources, sprint plans",
        "icon": "settings",
        "sub_categories": ["processes", "sops", "vendors", "resources", "sprints", "general"],
    }
    _t.TAXONOMY["audit"] = {
        "description": "Boardroom audit trail -- agent actions, approvals, budget usage",
        "icon": "shield",
        "sub_categories": ["actions", "approvals", "budget_usage", "agent_logs", "general"],
    }

    # Rebuild DOMAINS list after extending TAXONOMY
    _t.DOMAINS = list(_t.TAXONOMY.keys())

    # -- Cross-domain affinity extensions ------------------------------------
    _t.DOMAIN_AFFINITY["trading"] = {"finance": 0.3}
    _t.DOMAIN_AFFINITY["finance"]["trading"] = 0.3
    _t.DOMAIN_AFFINITY["strategy"] = {"competitive_intel": 0.6, "finance": 0.4, "marketing": 0.3, "operations": 0.3}
    _t.DOMAIN_AFFINITY["competitive_intel"] = {"strategy": 0.6, "marketing": 0.4}
    _t.DOMAIN_AFFINITY["marketing"] = {"advertising": 0.7, "strategy": 0.3, "competitive_intel": 0.4}
    _t.DOMAIN_AFFINITY["advertising"] = {"marketing": 0.7}
    _t.DOMAIN_AFFINITY["operations"] = {"strategy": 0.3, "finance": 0.3}
    _t.DOMAIN_AFFINITY["audit"] = {}

    # -- Tag vocabulary extensions -------------------------------------------
    _t.TAG_VOCABULARY["trading"] = [
        "trading-signal", "herd-detection", "kelly-sizing", "cascade-liquidation",
        "longshot-surface", "market-analysis", "risk-management", "position-sizing",
        "entry-trigger", "exit-strategy", "backtest", "performance-analysis",
        "volatility", "sentiment", "correlation", "arbitrage", "execution-log",
    ]
    _t.TAG_VOCABULARY["strategy"] = [
        "board-decision", "strategic-pivot", "okr", "key-result", "risk-assessment",
        "competitive-position", "swot", "pestel", "vision", "mission", "quarterly-review",
        "goal-cascade", "strategy-brief", "board-deck", "scenario-analysis",
    ]
    _t.TAG_VOCABULARY["competitive_intel"] = [
        "competitor-profile", "market-sizing", "trend-analysis", "regulatory-change",
        "pricing-intel", "product-launch", "market-share", "industry-news",
        "patent-filing", "acquisition", "partnership", "benchmark",
    ]
    _t.TAG_VOCABULARY["marketing"] = [
        "campaign-brief", "content-calendar", "audience-segment", "brand-voice",
        "channel-strategy", "cac-analysis", "ltv-model", "conversion-rate",
        "email-campaign", "social-post", "landing-page", "creative-asset",
    ]
    _t.TAG_VOCABULARY["advertising"] = [
        "google-ads", "meta-ads", "instagram-ads", "x-ads", "reels-campaign",
        "stories-campaign", "carousel-ad", "search-campaign", "display-campaign",
        "ad-creative", "bid-optimization", "a-b-test", "roas", "ctr", "cpc",
    ]
    _t.TAG_VOCABULARY["operations"] = [
        "sop", "process-map", "vendor-evaluation", "resource-allocation",
        "sprint-plan", "retrospective", "onboarding", "workflow", "bottleneck",
        "capacity-planning", "incident-report", "meeting-notes", "action-item",
    ]
    _t.TAG_VOCABULARY["audit"] = [
        "agent-action", "approval-request", "budget-spend", "governance-event",
        "content-review", "kill-switch", "anomaly-alert", "rollback",
        "compliance-check", "access-log",
    ]
