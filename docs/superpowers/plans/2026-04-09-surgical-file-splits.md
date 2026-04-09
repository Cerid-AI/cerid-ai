# Surgical File Splits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Split 7 mixed files into public base + internal extension (*_internal.py) so repo sync requires zero manual cherry-picking.

**Architecture:** Each mixed file gets a companion *_internal.py that contains internal-only code (trading, boardroom, billing, enterprise). The internal file registers itself via hook functions called from a bootstrap block appended to the base file. The sync script truncates at the hook marker for public distribution.

**Tech Stack:** Python 3.11, FastAPI, TypeScript, React 19, ruff, mypy, pytest, vitest

---

## Phase 1: Config Layer Splits

### Task 1: Extract config/settings_internal.py

**Files:**
- Create: `src/mcp/config/settings_internal.py`
- Modify: `src/mcp/config/settings.py`

- [ ] **Step 1:** Create `src/mcp/config/settings_internal.py` with the following content:

```python
"""Internal-only settings -- trading, boardroom, billing config.

This file exists only in cerid-ai-internal. The public repo's settings.py
does not reference this file. The bootstrap in main_internal.py calls
extend_settings() at startup.
"""
from __future__ import annotations

import os


def extend_settings() -> None:
    """Add internal-only config to the settings module namespace."""
    import config.settings as _s

    # Trading Agent Integration
    _s.CERID_TRADING_ENABLED = os.getenv("CERID_TRADING_ENABLED", "false").lower() in ("true", "1")
    _s.TRADING_AGENT_URL = os.getenv("TRADING_AGENT_URL", "http://localhost:8090")

    # Boardroom Agent Integration
    _s.CERID_BOARDROOM_ENABLED = os.getenv("CERID_BOARDROOM_ENABLED", "false").lower() in ("true", "1")
    _s.CERID_BOARDROOM_TIER = os.getenv("CERID_BOARDROOM_TIER", "foundation")

    # Extend CONSUMER_REGISTRY with internal-only consumers
    _s.CONSUMER_REGISTRY["trading-agent"] = {
        "rate_limits": {
            "/sdk/": (80, 60),
            "/agent/": (80, 60),
        },
        "allowed_domains": ["trading"],
        "strict_domains": True,
    }
    _s.CONSUMER_REGISTRY["boardroom-agent"] = {
        "rate_limits": {
            "/sdk/": (40, 60),
            "/agent/": (40, 60),
        },
        "allowed_domains": ["strategy", "competitive_intel", "marketing", "advertising", "operations", "audit"],
        "strict_domains": True,
    }

    # Rebuild CLIENT_RATE_LIMITS after extending CONSUMER_REGISTRY
    _s.CLIENT_RATE_LIMITS.update({
        k: v["rate_limits"] for k, v in _s.CONSUMER_REGISTRY.items()
        if k in ("trading-agent", "boardroom-agent")
    })
```

- [ ] **Step 2:** Edit `src/mcp/config/settings.py` -- remove trading/boardroom env vars (lines 552-562). Replace this block:

```python
# ---------------------------------------------------------------------------
# Trading Agent Integration (internal only — not in public distro)
# ---------------------------------------------------------------------------
CERID_TRADING_ENABLED = os.getenv("CERID_TRADING_ENABLED", "false").lower() in ("true", "1")
TRADING_AGENT_URL = os.getenv("TRADING_AGENT_URL", "http://localhost:8090")

# ---------------------------------------------------------------------------
# Boardroom Agent Integration (internal only — not in public distro)
# ---------------------------------------------------------------------------
CERID_BOARDROOM_ENABLED = os.getenv("CERID_BOARDROOM_ENABLED", "false").lower() in ("true", "1")
CERID_BOARDROOM_TIER = os.getenv("CERID_BOARDROOM_TIER", "foundation")
```

with:

```python
# Trading/boardroom config injected at runtime by config.settings_internal
CERID_TRADING_ENABLED: bool = False
TRADING_AGENT_URL: str = ""
CERID_BOARDROOM_ENABLED: bool = False
CERID_BOARDROOM_TIER: str = "foundation"
```

- [ ] **Step 3:** Edit `src/mcp/config/settings.py` -- remove trading-agent and boardroom-agent from CONSUMER_REGISTRY (lines 704-719). Delete the `"trading-agent"` and `"boardroom-agent"` dict entries from the `CONSUMER_REGISTRY` dict so `"_default"` immediately follows `"webhook"`.

Remove:
```python
    "trading-agent": {
        "rate_limits": {
            "/sdk/": (80, 60),       # 80 req/min — 5-session burst of 67.5/min
            "/agent/": (80, 60),
        },
        "allowed_domains": ["trading"],
        "strict_domains": True,
    },
    "boardroom-agent": {
        "rate_limits": {
            "/sdk/": (40, 60),
            "/agent/": (40, 60),
        },
        "allowed_domains": ["strategy", "competitive_intel", "marketing", "advertising", "operations", "audit"],
        "strict_domains": True,
    },
```

- [ ] **Step 4:** Add hook marker at the very bottom of `src/mcp/config/settings.py` (after the final line):

```python

# -- Internal settings -------------------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
```

- [ ] **Step 5:** Verify removals:

```bash
grep -c "os.getenv.*CERID_TRADING_ENABLED" src/mcp/config/settings.py
# Expected: 0

grep -c "CERID_TRADING_ENABLED" src/mcp/config/settings_internal.py
# Expected: 1

grep -c '"trading-agent"' src/mcp/config/settings.py
# Expected: 0

grep -c '"trading-agent"' src/mcp/config/settings_internal.py
# Expected: 1
```

- [ ] **Step 6: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/config/settings.py src/mcp/config/settings_internal.py
git commit -m "refactor: extract internal-only settings to settings_internal.py"
```

---

### Task 2: Extract config/taxonomy_internal.py

**Files:**
- Create: `src/mcp/config/taxonomy_internal.py`
- Modify: `src/mcp/config/taxonomy.py`

- [ ] **Step 1:** Create `src/mcp/config/taxonomy_internal.py`:

```python
"""Internal-only taxonomy extensions -- trading + boardroom domains.

This file exists only in cerid-ai-internal. The bootstrap in
main_internal.py calls extend_taxonomy() at startup, before any
router reads TAXONOMY or DOMAINS.
"""
from __future__ import annotations


def extend_taxonomy() -> None:
    """Add trading + boardroom domains to the taxonomy module namespace."""
    import config.taxonomy as _t

    # ── Trading domain ──────────────────────────────────────────────────
    _t.TAXONOMY["trading"] = {
        "description": "Automated trading signals, market analysis, execution logs, and strategy research",
        "icon": "trending-up",
        "sub_categories": [
            "signals", "market-analysis", "execution", "post-analysis",
            "strategy-research", "risk-analysis", "general",
        ],
    }

    # ── Boardroom domains (gated by CERID_BOARDROOM_ENABLED) ────────────
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

    # ── Cross-domain affinity extensions ─────────────────────────────────
    _t.DOMAIN_AFFINITY["trading"] = {"finance": 0.3}
    _t.DOMAIN_AFFINITY["finance"]["trading"] = 0.3
    _t.DOMAIN_AFFINITY["strategy"] = {"competitive_intel": 0.6, "finance": 0.4, "marketing": 0.3, "operations": 0.3}
    _t.DOMAIN_AFFINITY["competitive_intel"] = {"strategy": 0.6, "marketing": 0.4}
    _t.DOMAIN_AFFINITY["marketing"] = {"advertising": 0.7, "strategy": 0.3, "competitive_intel": 0.4}
    _t.DOMAIN_AFFINITY["advertising"] = {"marketing": 0.7}
    _t.DOMAIN_AFFINITY["operations"] = {"strategy": 0.3, "finance": 0.3}
    _t.DOMAIN_AFFINITY["audit"] = {}

    # ── Tag vocabulary extensions ────────────────────────────────────────
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
```

- [ ] **Step 2:** Edit `src/mcp/config/taxonomy.py` -- remove the `"trading"` domain from the TAXONOMY dict. Delete lines 44-51 (the `"trading": { ... }` entry):

```python
    "trading": {
        "description": "Automated trading signals, market analysis, execution logs, and strategy research",
        "icon": "trending-up",
        "sub_categories": [
            "signals", "market-analysis", "execution", "post-analysis",
            "strategy-research", "risk-analysis", "general",
        ],
    },
```

- [ ] **Step 3:** Remove all 6 boardroom domains from TAXONOMY dict. Delete lines 52-82 (the `# --- Boardroom domains ...` comment through the closing `}` of `"audit"`):

```python
    # --- Boardroom domains (gated by CERID_BOARDROOM_ENABLED) ---
    "strategy": {
        ...
    },
    "competitive_intel": {
        ...
    },
    "marketing": {
        ...
    },
    "advertising": {
        ...
    },
    "operations": {
        ...
    },
    "audit": {
        ...
    },
```

- [ ] **Step 4:** Edit `src/mcp/config/taxonomy.py` -- remove trading + boardroom entries from DOMAIN_AFFINITY. Replace the entire `DOMAIN_AFFINITY` dict with:

```python
DOMAIN_AFFINITY = {
    "coding":        {"projects": 0.6},
    "projects":      {"coding": 0.6, "finance": 0.4},
    "finance":       {"projects": 0.4},
    "personal":      {"general": 0.5, "conversations": 0.3},
    "general":       {"personal": 0.5, "conversations": 0.3},
    "conversations": {"personal": 0.3, "general": 0.3},
}
```

Note: The `"finance": {"projects": 0.4}` entry no longer includes `"trading": 0.3` -- that affinity is injected by `extend_taxonomy()`.

- [ ] **Step 5:** Edit `src/mcp/config/taxonomy.py` -- remove trading + boardroom entries from TAG_VOCABULARY. Remove the `"trading"` entry and all 6 boardroom entries (`"strategy"`, `"competitive_intel"`, `"marketing"`, `"advertising"`, `"operations"`, `"audit"`). The TAG_VOCABULARY should end after the `"conversations"` entry:

```python
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
}
```

- [ ] **Step 6:** Add hook marker at the bottom of `src/mcp/config/taxonomy.py`:

```python

# -- Internal taxonomy -------------------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
```

- [ ] **Step 7:** Verify:

```bash
grep -c "boardroom\|trading\|strategy\|competitive_intel\|advertising\|operations\|audit" src/mcp/config/taxonomy.py
# Expected: 0

grep -c "trading" src/mcp/config/taxonomy_internal.py
# Expected: multiple (>5)
```

- [ ] **Step 8: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/config/taxonomy.py src/mcp/config/taxonomy_internal.py
git commit -m "refactor: extract internal-only taxonomy to taxonomy_internal.py"
```

---

## Phase 2: Router Layer Splits

### Task 3: Extract app/routers/agents_internal.py

**Files:**
- Create: `src/mcp/app/routers/agents_internal.py`
- Modify: `src/mcp/app/routers/agents.py`

- [ ] **Step 1:** Create `src/mcp/app/routers/agents_internal.py`:

```python
"""Internal-only trading agent endpoints.

This file exists only in cerid-ai-internal. It registers trading
endpoints on the agents router when called from the bootstrap block
at the bottom of agents.py.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.deps import get_chroma, get_neo4j
from app.models.trading import (
    CascadeConfirmRequest,
    HerdDetectRequest,
    KellySizeRequest,
    LongshotSurfaceRequest,
    TradingSignalRequest,
)

logger = logging.getLogger("ai-companion")


def register_trading_endpoints(router: APIRouter) -> None:
    """Register 5 trading POST endpoints on the given router."""

    @router.post("/agent/trading/signal")
    async def trading_signal_endpoint(req: TradingSignalRequest):
        """Enrich a trading signal with KB context."""
        try:
            from agents.trading_agent import trading_signal_enrich
            return await trading_signal_enrich(
                query=req.query,
                signal_data=req.signal_data,
                domains=req.domains,
                chroma=get_chroma(),
                neo4j=get_neo4j(),
                top_k=req.top_k,
            )
        except Exception as e:
            logger.error(f"Trading signal enrich error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/herd-detect")
    async def trading_herd_detect_endpoint(req: HerdDetectRequest):
        """Detect herd behavior via correlation graph violations."""
        try:
            from agents.trading_agent import herd_detect
            return await herd_detect(
                asset=req.asset,
                sentiment_data=req.sentiment_data,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Herd detect error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/kelly-size")
    async def trading_kelly_size_endpoint(req: KellySizeRequest):
        """Query historical CV_edge for Kelly sizing."""
        try:
            from agents.trading_agent import kelly_size
            return await kelly_size(
                strategy=req.strategy,
                confidence=req.confidence,
                win_loss_ratio=req.win_loss_ratio,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Kelly size error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/cascade-confirm")
    async def trading_cascade_confirm_endpoint(req: CascadeConfirmRequest):
        """Confirm cascade pattern against historical data."""
        try:
            from agents.trading_agent import cascade_confirm
            return await cascade_confirm(
                asset=req.asset,
                liquidation_events=req.liquidation_events,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Cascade confirm error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/longshot-surface")
    async def trading_longshot_surface_endpoint(req: LongshotSurfaceRequest):
        """Query stored calibration surface from Neo4j."""
        try:
            from agents.trading_agent import longshot_surface_query
            return await longshot_surface_query(
                asset=req.asset,
                date_range=req.date_range,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Longshot surface error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2:** Edit `src/mcp/app/routers/agents.py` -- remove the trading import at line 17:

```python
from config.settings import CERID_TRADING_ENABLED as _TRADING_ENABLED
```

- [ ] **Step 3:** Remove the try/except trading model import block (lines 19-34):

```python
# isort: split
try:
    from app.models.trading import (
        CascadeConfirmRequest,
        HerdDetectRequest,
        KellySizeRequest,
        LongshotSurfaceRequest,
        TradingSignalRequest,
    )
except ImportError:
    # Trading models not available in public distro
    CascadeConfirmRequest = None  # type: ignore[assignment,misc]
    HerdDetectRequest = None  # type: ignore[assignment,misc]
    KellySizeRequest = None  # type: ignore[assignment,misc]
    LongshotSurfaceRequest = None  # type: ignore[assignment,misc]
    TradingSignalRequest = None  # type: ignore[assignment,misc]
```

- [ ] **Step 4:** Remove the entire trading endpoints block (lines 793-873) -- everything from the `# ---------------------------------------------------------------------------` comment header through the last `raise HTTPException(...)` in `trading_longshot_surface_endpoint`.

- [ ] **Step 5:** Add hook marker + bootstrap block at the bottom of `agents.py`:

```python

# -- Trading endpoints -------------------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
try:
    from app.routers.agents_internal import register_trading_endpoints
    register_trading_endpoints(router)
except ImportError:
    pass
```

- [ ] **Step 6:** Verify:

```bash
grep -c "_TRADING_ENABLED" src/mcp/app/routers/agents.py
# Expected: 0

grep -c "CascadeConfirmRequest" src/mcp/app/routers/agents.py
# Expected: 0

grep -c "register_trading_endpoints" src/mcp/app/routers/agents.py
# Expected: 1 (in the bootstrap block)
```

- [ ] **Step 7: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/app/routers/agents.py src/mcp/app/routers/agents_internal.py
git commit -m "refactor: extract trading endpoints to agents_internal.py"
```

---

### Task 4: Extract app/routers/sdk_internal.py

**Files:**
- Create: `src/mcp/app/routers/sdk_internal.py`
- Modify: `src/mcp/app/routers/sdk.py`

- [ ] **Step 1:** Create `src/mcp/app/routers/sdk_internal.py`:

```python
"""Internal-only SDK endpoints -- trading + boardroom.

This file exists only in cerid-ai-internal. It registers trading and
boardroom SDK endpoints on the sdk router when called from the bootstrap
block at the bottom of sdk.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.sdk import (
    SDKCascadeConfirmResponse,
    SDKHerdDetectResponse,
    SDKKellySizeResponse,
    SDKLongshotSurfaceResponse,
    SDKTradingSignalResponse,
)
from app.models.trading import (
    CascadeConfirmRequest,
    HerdDetectRequest,
    KellySizeRequest,
    LongshotSurfaceRequest,
    TradingSignalRequest,
)
from app.routers.agents import (
    AgentQueryRequest,
    agent_query_endpoint,
)

_503 = {"description": "One or more backend services unavailable"}
_422 = {"description": "Invalid request parameters"}


def _require_trading() -> None:
    """Raise 404 if trading integration is disabled."""
    from config.settings import CERID_TRADING_ENABLED
    if not CERID_TRADING_ENABLED:
        raise HTTPException(status_code=404, detail="Trading integration disabled")


def _require_boardroom() -> None:
    """Raise 404 if boardroom integration is disabled."""
    from config.settings import CERID_BOARDROOM_ENABLED
    if not CERID_BOARDROOM_ENABLED:
        raise HTTPException(status_code=404, detail="Boardroom integration disabled")


def register_internal_sdk_endpoints(router: APIRouter) -> None:
    """Register trading + boardroom SDK endpoints on the given router."""

    # ── Trading endpoints ───────────────────────────────────────────────

    from config.settings import CERID_TRADING_ENABLED
    if CERID_TRADING_ENABLED:
        from app.routers.agents_internal import register_trading_endpoints as _  # noqa: F401 -- ensures endpoints exist

        @router.post(
            "/trading/signal",
            response_model=SDKTradingSignalResponse,
            summary="Trading Signal Enrichment",
            description="Enrich a trading signal with KB context -- historical trades, domain knowledge, and confidence scoring.",
            responses={422: _422, 503: _503},
        )
        async def sdk_trading_signal(req: TradingSignalRequest):
            from app.routers.agents import trading_signal_endpoint
            return await trading_signal_endpoint(req)

        @router.post(
            "/trading/herd-detect",
            response_model=SDKHerdDetectResponse,
            summary="Herd Behavior Detection",
            description="Detect herd behavior patterns by analyzing correlation graph violations and historical herd events.",
            responses={422: _422, 503: _503},
        )
        async def sdk_trading_herd_detect(req: HerdDetectRequest):
            from app.routers.agents import trading_herd_detect_endpoint
            return await trading_herd_detect_endpoint(req)

        @router.post(
            "/trading/kelly-size",
            response_model=SDKKellySizeResponse,
            summary="Kelly Criterion Sizing",
            description="Compute Kelly fraction for position sizing using historical win/loss data from KB.",
            responses={422: _422, 503: _503},
        )
        async def sdk_trading_kelly_size(req: KellySizeRequest):
            from app.routers.agents import trading_kelly_size_endpoint
            return await trading_kelly_size_endpoint(req)

        @router.post(
            "/trading/cascade-confirm",
            response_model=SDKCascadeConfirmResponse,
            summary="Cascade Pattern Confirmation",
            description="Confirm whether a liquidation cascade pattern matches historical cascade events in the KB.",
            responses={422: _422, 503: _503},
        )
        async def sdk_trading_cascade_confirm(req: CascadeConfirmRequest):
            from app.routers.agents import trading_cascade_confirm_endpoint
            return await trading_cascade_confirm_endpoint(req)

        @router.post(
            "/trading/longshot-surface",
            response_model=SDKLongshotSurfaceResponse,
            summary="Longshot Calibration Surface",
            description="Query the calibration surface for longshot probability estimates from historical prediction market data.",
            responses={422: _422, 503: _503},
        )
        async def sdk_trading_longshot_surface(req: LongshotSurfaceRequest):
            from app.routers.agents import trading_longshot_surface_endpoint
            return await trading_longshot_surface_endpoint(req)

    # ── Boardroom endpoints ─────────────────────────────────────────────

    from config.settings import CERID_BOARDROOM_ENABLED
    if CERID_BOARDROOM_ENABLED:

        @router.get(
            "/ops/health",
            summary="Boardroom Health Check",
            description="Check boardroom integration status and tier.",
        )
        async def sdk_ops_health():
            from config.settings import CERID_BOARDROOM_TIER

            _require_boardroom()
            return {
                "status": "ok",
                "boardroom_enabled": True,
                "tier": CERID_BOARDROOM_TIER,
                "domains": ["strategy", "competitive_intel", "marketing", "advertising",
                            "finance", "operations", "audit"],
            }

        @router.post(
            "/ops/competitive-scan",
            summary="Competitive Intelligence Scan",
            description="Run a structured competitive analysis using KB + web search.",
            responses={422: _422, 503: _503},
        )
        async def sdk_ops_competitive_scan(req: AgentQueryRequest, request: Request):
            _require_boardroom()
            req.domains = ["competitive_intel"]
            result = await agent_query_endpoint(req, request)
            return {"result": result, "domain": "competitive_intel"}

        @router.post(
            "/ops/strategy-brief",
            summary="Strategy Brief Generation",
            description="Generate a board-ready strategy brief from accumulated intel.",
            responses={422: _422, 503: _503},
        )
        async def sdk_ops_strategy_brief(req: AgentQueryRequest, request: Request):
            _require_boardroom()
            req.domains = ["strategy", "competitive_intel"]
            result = await agent_query_endpoint(req, request)
            return {"result": result, "domains": ["strategy", "competitive_intel"]}

        @router.get(
            "/ops/governance-log",
            summary="Governance Audit Log",
            description="Query the boardroom audit trail for agent actions and approvals.",
        )
        async def sdk_ops_governance_log():
            _require_boardroom()
            # Placeholder -- will query audit domain in KB
            return {"entries": [], "total": 0}
```

- [ ] **Step 2:** Edit `src/mcp/app/routers/sdk.py` -- remove trading/boardroom model imports. Replace lines 20-36 (the two import blocks for `SDKCascadeConfirmResponse...SDKTradingSignalResponse` and `CascadeConfirmRequest...TradingSignalRequest`) with only the public SDK models:

```python
from app.models.sdk import (
    SDKHallucinationResponse,
    SDKHealthResponse,
    SDKMemoryExtractResponse,
    SDKQueryResponse,
)
```

- [ ] **Step 3:** Remove the import of `CERID_BOARDROOM_ENABLED, CERID_TRADING_ENABLED` from line 51:

```python
from config.settings import CERID_BOARDROOM_ENABLED, CERID_TRADING_ENABLED
```

- [ ] **Step 4:** Remove the `_require_trading()` helper function (lines 206-209).

- [ ] **Step 5:** Remove the entire `if CERID_TRADING_ENABLED:` block (lines 212-269) -- all 5 trading SDK endpoint definitions.

- [ ] **Step 6:** Remove the `_require_boardroom()` helper function (lines 277-280).

- [ ] **Step 7:** Remove the entire `if CERID_BOARDROOM_ENABLED:` block (lines 283-334) -- all 4 boardroom SDK endpoint definitions.

- [ ] **Step 8:** Add hook marker + bootstrap block at the bottom of `sdk.py`:

```python

# -- Internal SDK endpoints --------------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
try:
    from app.routers.sdk_internal import register_internal_sdk_endpoints
    register_internal_sdk_endpoints(router)
except ImportError:
    pass
```

- [ ] **Step 9:** Verify:

```bash
grep -c "CERID_TRADING_ENABLED\|CERID_BOARDROOM_ENABLED" src/mcp/app/routers/sdk.py
# Expected: 0

grep -c "TradingSignalRequest\|CascadeConfirmRequest" src/mcp/app/routers/sdk.py
# Expected: 0
```

- [ ] **Step 10: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/app/routers/sdk.py src/mcp/app/routers/sdk_internal.py
git commit -m "refactor: extract trading/boardroom SDK endpoints to sdk_internal.py"
```

---

## Phase 3: App Layer Splits

### Task 5: Extract app/main_internal.py

**Files:**
- Create: `src/mcp/app/main_internal.py`
- Modify: `src/mcp/app/main.py`

- [ ] **Step 1:** Create `src/mcp/app/main_internal.py`:

```python
"""Internal-only router registration and lifecycle hooks.

This file exists only in cerid-ai-internal. The bootstrap block
at the bottom of main.py calls register_internal_routers() and
get_internal_shutdown_hooks().
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Coroutine

logger = logging.getLogger("ai-companion")


def bootstrap_internal_config() -> None:
    """Extend settings and taxonomy with internal-only config.

    Must be called BEFORE any router import that reads CERID_TRADING_ENABLED
    or taxonomy domains.
    """
    from config.settings_internal import extend_settings
    from config.taxonomy_internal import extend_taxonomy

    extend_settings()
    extend_taxonomy()
    logger.info("Internal config bootstrapped (settings + taxonomy extended)")


def register_internal_routers(app: Any) -> None:
    """Register internal-only routers on the FastAPI app."""
    from app.routers import alerts, migration, ws_sync

    # Alerting API
    app.include_router(alerts.router)
    app.include_router(alerts.router, prefix="/api/v1")

    # Migration API
    app.include_router(migration.router)
    app.include_router(migration.router, prefix="/api/v1")

    # WebSocket sync
    app.include_router(ws_sync.router)

    # Trading proxy (gated by CERID_TRADING_ENABLED)
    from config.settings import CERID_TRADING_ENABLED
    if CERID_TRADING_ENABLED:
        from app.routers import trading_proxy
        app.include_router(trading_proxy.router)

    # Eval harness API (opt-in)
    if os.getenv("CERID_EVAL_ENABLED", "").lower() in ("1", "true", "yes"):
        from app.routers import eval as eval_router
        app.include_router(eval_router.router)

    logger.info("Internal routers registered")


def get_internal_shutdown_hooks() -> list[Callable[[], Coroutine[Any, Any, None]]]:
    """Return a list of async shutdown callables for internal services."""
    hooks: list[Callable[[], Coroutine[Any, Any, None]]] = []

    from config.settings import CERID_TRADING_ENABLED
    if CERID_TRADING_ENABLED:
        async def _close_trading_proxy() -> None:
            try:
                from app.routers.trading_proxy import close_trading_proxy_client
                await close_trading_proxy_client()
            except Exception as exc:
                logger.warning("Trading proxy shutdown failed: %s", exc)

        hooks.append(_close_trading_proxy)

    return hooks
```

- [ ] **Step 2:** Edit `src/mcp/app/main.py` -- remove internal-only router imports from the `from app.routers import (...)` block (line 38-68). Remove `alerts`, `migration`, and `ws_sync` from the import list. The import becomes:

```python
from app.routers import (
    a2a,
    agents,
    artifacts,
    automations,
    chat,
    digest,
    health,
    ingestion,
    kb_admin,
    mcp_sse,
    memories,
    models,
    observability,
    ollama_proxy,
    plugins,
    providers,
    query,
    scanner,
    sdk,
    settings,
    setup,
    sync,
    taxonomy,
    upload,
    user_state,
    workflows,
)
```

- [ ] **Step 3:** Remove `from config.settings import CERID_TRADING_ENABLED` (line 71).

- [ ] **Step 4:** Remove the trading proxy shutdown block (lines 336-342) from the `lifespan` shutdown sequence:

```python
    # Close trading proxy
    if CERID_TRADING_ENABLED:
        try:
            from app.routers.trading_proxy import close_trading_proxy_client
            await close_trading_proxy_client()
        except Exception as exc:
            logger.warning("Trading proxy shutdown failed: %s", exc)
```

- [ ] **Step 5:** Remove the alerts/migration/ws_sync router registration blocks (lines 428-437):

```python
# Alerting API
app.include_router(alerts.router)
app.include_router(alerts.router, prefix="/api/v1")

# Migration API
app.include_router(migration.router)
app.include_router(migration.router, prefix="/api/v1")

# WebSocket sync
app.include_router(ws_sync.router)
```

- [ ] **Step 6:** Remove the trading proxy block (lines 458-461):

```python
# Trading proxy
if CERID_TRADING_ENABLED:
    from app.routers import trading_proxy
    app.include_router(trading_proxy.router)
```

- [ ] **Step 7:** Remove the eval harness block (lines 463-466):

```python
# Eval harness API
if os.getenv("CERID_EVAL_ENABLED", "").lower() in ("1", "true", "yes"):
    from app.routers import eval as eval_router
    app.include_router(eval_router.router)
```

- [ ] **Step 8:** Add hook marker + bootstrap block at the bottom of `main.py` (before any bridge-layer imports at the end, or at the very end of the file):

```python

# -- Internal routers --------------------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
try:
    from app.main_internal import bootstrap_internal_config, register_internal_routers, get_internal_shutdown_hooks
    bootstrap_internal_config()
    register_internal_routers(app)
    # Shutdown hooks are invoked in lifespan -- store for deferred call
    _internal_shutdown_hooks = get_internal_shutdown_hooks()
except ImportError:
    _internal_shutdown_hooks = []
```

- [ ] **Step 9:** Update the lifespan shutdown to call internal hooks. In the shutdown section of the lifespan context manager, add after the existing shutdown calls (e.g., after the Ollama client close):

```python
    # Internal shutdown hooks (trading proxy etc.)
    for hook in _internal_shutdown_hooks:
        try:
            await hook()
        except Exception as exc:
            logger.warning("Internal shutdown hook failed: %s", exc)
```

Note: `_internal_shutdown_hooks` is set by the bootstrap block. If `main_internal.py` is not present (public repo), it defaults to `[]`.

- [ ] **Step 10:** Verify:

```bash
grep -c "CERID_TRADING_ENABLED" src/mcp/app/main.py
# Expected: 0 (in the public portion above the marker)

grep -c "alerts\|migration\|ws_sync" src/mcp/app/main.py
# Expected: 0 in the router import block (only in bootstrap block for internal)
```

- [ ] **Step 11: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/app/main.py src/mcp/app/main_internal.py
git commit -m "refactor: extract internal routers and lifecycle hooks to main_internal.py"
```

---

### Task 6: Extract app/tools_internal.py

**Files:**
- Create: `src/mcp/app/tools_internal.py`
- Modify: `src/mcp/app/tools.py`

- [ ] **Step 1:** Create `src/mcp/app/tools_internal.py`:

```python
"""Internal-only MCP tool definitions and dispatch -- trading tools.

This file exists only in cerid-ai-internal. The bootstrap block at the
bottom of tools.py calls get_trading_tools() to extend MCP_TOOLS and
dispatch_trading_tool() as a fallback in execute_tool().
"""
from __future__ import annotations

from typing import Any

from app.deps import get_chroma, get_neo4j


def get_trading_tools() -> list[dict[str, Any]]:
    """Return the 5 trading MCP tool definition dicts."""
    return [
        {
            "name": "pkb_trading_signal",
            "description": "Enrich a trading signal with KB context from ChromaDB and historical trades from Neo4j. Example signal_data: {\"asset\": \"ETH\", \"direction\": \"long\", \"confidence\": 0.82}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query describing the trading signal"},
                    "signal_data": {
                        "type": "object",
                        "description": "Signal metadata (asset, direction, confidence, etc.)",
                        "default": {},
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Knowledge domains to search",
                        "default": ["finance", "trading"],
                    },
                    "top_k": {"type": "integer", "description": "Number of KB results", "default": 5},
                },
                "required": ["query"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "sources": {"type": "array"},
                    "historical_trades": {"type": "array"},
                },
            },
        },
        {
            "name": "pkb_herd_detect",
            "description": "Detect herd behavior by checking correlation graph violations and sentiment extremes. Example sentiment_data: {\"finbert_score\": 0.7, \"fear_greed\": 45}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. ETH, BTC)"},
                    "sentiment_data": {
                        "type": "object",
                        "description": "Sentiment indicators (fear_greed_index, etc.)",
                        "default": {},
                    },
                },
                "required": ["asset"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "violations": {"type": "array"},
                    "historical_matches": {"type": "array"},
                    "sentiment_extreme": {"type": "boolean"},
                },
            },
        },
        {
            "name": "pkb_kelly_size",
            "description": "Compute Kelly-criterion position size using historical CV_edge from calibration data. Example values: strategy: \"herd-fade\", confidence: 0.75, win_loss_ratio: 1.5",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string", "description": "Strategy/session name for calibration lookup"},
                    "confidence": {"type": "number", "description": "Win probability (0-1)"},
                    "win_loss_ratio": {"type": "number", "description": "Average win / average loss ratio"},
                },
                "required": ["strategy", "confidence", "win_loss_ratio"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "kelly_fraction": {"type": "number"},
                    "cv_edge": {"type": "number"},
                    "kelly_raw": {"type": "number"},
                    "strategy": {"type": "string"},
                },
            },
        },
        {
            "name": "pkb_cascade_confirm",
            "description": "Confirm a liquidation cascade pattern against historical cascade events. liquidation_events shape: [{\"venue\": \"binance\", \"size_usd\": 500000, \"timestamp\": \"...\"}]",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. ETH, BTC)"},
                    "liquidation_events": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of liquidation events with venue and size_usd",
                        "default": [],
                    },
                },
                "required": ["asset"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "confirmation_score": {"type": "number"},
                    "historical_cascades": {"type": "integer"},
                    "match_quality": {"type": "string"},
                },
            },
        },
        {
            "name": "pkb_longshot_surface",
            "description": "Query stored calibration surface (implied vs actual probabilities) from Neo4j. date_range format: \"2026-03-01/2026-03-15\" or shorthand \"7d\", \"14d\", \"30d\", \"90d\"",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. ETH, BTC)"},
                    "date_range": {
                        "type": "string",
                        "description": "Lookback period (7d, 14d, 30d, 90d)",
                        "default": "30d",
                    },
                },
                "required": ["asset"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "calibration_points": {"type": "array"},
                    "count": {"type": "integer"},
                    "asset": {"type": "string"},
                    "date_range": {"type": "string"},
                },
            },
        },
    ]


async def dispatch_trading_tool(name: str, arguments: dict[str, Any]) -> Any | None:
    """Dispatch to trading agent functions. Returns None if name doesn't match."""
    if name == "pkb_trading_signal":
        from agents.trading_agent import trading_signal_enrich
        return await trading_signal_enrich(
            query=arguments.get("query", ""),
            signal_data=arguments.get("signal_data", {}),
            domains=arguments.get("domains", ["finance", "trading"]),
            chroma=get_chroma(),
            neo4j=get_neo4j(),
            top_k=arguments.get("top_k", 5),
        )
    elif name == "pkb_herd_detect":
        from agents.trading_agent import herd_detect
        return await herd_detect(
            asset=arguments.get("asset", ""),
            sentiment_data=arguments.get("sentiment_data", {}),
            neo4j=get_neo4j(),
        )
    elif name == "pkb_kelly_size":
        from agents.trading_agent import kelly_size
        return await kelly_size(
            strategy=arguments.get("strategy", ""),
            confidence=arguments.get("confidence", 0.5),
            win_loss_ratio=arguments.get("win_loss_ratio", 1.0),
            neo4j=get_neo4j(),
        )
    elif name == "pkb_cascade_confirm":
        from agents.trading_agent import cascade_confirm
        return await cascade_confirm(
            asset=arguments.get("asset", ""),
            liquidation_events=arguments.get("liquidation_events", []),
            neo4j=get_neo4j(),
        )
    elif name == "pkb_longshot_surface":
        from agents.trading_agent import longshot_surface_query
        return await longshot_surface_query(
            asset=arguments.get("asset", ""),
            date_range=arguments.get("date_range", "30d"),
            neo4j=get_neo4j(),
        )
    return None  # Name didn't match -- fall through to base dispatcher
```

- [ ] **Step 2:** Edit `src/mcp/app/tools.py` -- remove the 5 trading tool dicts from the `MCP_TOOLS` list (lines 466-593). These are the dicts with names: `pkb_trading_signal`, `pkb_herd_detect`, `pkb_kelly_size`, `pkb_cascade_confirm`, `pkb_longshot_surface`.

- [ ] **Step 3:** Remove the 5 trading elif branches from `execute_tool()` (lines 842-880):

```python
    elif name == "pkb_trading_signal":
        ...
    elif name == "pkb_herd_detect":
        ...
    elif name == "pkb_kelly_size":
        ...
    elif name == "pkb_cascade_confirm":
        ...
    elif name == "pkb_longshot_surface":
        ...
```

- [ ] **Step 4:** Add hook marker + integration at the bottom of `tools.py`, AFTER the `MCP_TOOLS` list definition but BEFORE `execute_tool()`:

```python

# -- Internal tools ----------------------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
try:
    from app.tools_internal import get_trading_tools
    MCP_TOOLS.extend(get_trading_tools())
except ImportError:
    pass
```

- [ ] **Step 5:** In `execute_tool()`, add a fallback dispatch check BEFORE the final `raise ValueError(...)` at the end of the function. Just before line 924 (`raise ValueError(f"Unknown tool: {name}")`), insert:

```python
    # Internal tool dispatch fallback
    try:
        from app.tools_internal import dispatch_trading_tool
        result = await dispatch_trading_tool(name, arguments)
        if result is not None:
            return result
    except ImportError:
        pass
    raise ValueError(f"Unknown tool: {name}")
```

And remove the original `raise ValueError(f"Unknown tool: {name}")` that was there before.

- [ ] **Step 6:** Verify:

```bash
grep -c "pkb_trading_signal\|pkb_herd_detect\|pkb_kelly_size\|pkb_cascade_confirm\|pkb_longshot_surface" src/mcp/app/tools.py
# Expected: 0 (in the public portion above the marker)

grep -c "pkb_trading_signal" src/mcp/app/tools_internal.py
# Expected: 2 (one in tool def, one in dispatch)
```

- [ ] **Step 7: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/app/tools.py src/mcp/app/tools_internal.py
git commit -m "refactor: extract trading MCP tools to tools_internal.py"
```

---

### Task 7: Extract app/scheduler_internal.py

**Files:**
- Create: `src/mcp/app/scheduler_internal.py`
- Modify: `src/mcp/app/scheduler.py`

- [ ] **Step 1:** Create `src/mcp/app/scheduler_internal.py`:

```python
"""Internal-only scheduler jobs -- trading agent periodic tasks.

This file exists only in cerid-ai-internal. The bootstrap block at
the bottom of scheduler.py calls register_trading_jobs() to add
trading-specific cron jobs.
"""
from __future__ import annotations

import logging
import time

import config
from app.deps import get_neo4j

logger = logging.getLogger("ai-companion")


def _log_execution(job_name: str, status: str, duration: float, detail: str = "") -> None:
    """Log job execution for observability (mirrors scheduler.py pattern)."""
    try:
        from app.scheduler import _log_execution as _base_log
        _base_log(job_name, status, duration, detail)
    except (ImportError, AttributeError):
        pass  # Fallback: just use logger


async def _run_trading_autoresearch() -> None:
    """Pull performance summary from trading agent and store in KB."""
    start = time.time()
    try:
        from agents.trading_scheduler_jobs import run_trading_autoresearch
        result = await run_trading_autoresearch(
            trading_agent_url=config.TRADING_AGENT_URL,
            neo4j=get_neo4j(),
        )
        status = result.get("status", "unknown")
        duration = time.time() - start
        _log_execution("trading_autoresearch", status, duration)
        logger.info(f"Scheduled trading autoresearch: {status} in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("trading_autoresearch", "error", duration, str(e))
        logger.error(f"Scheduled trading autoresearch failed: {e}")


async def _run_platt_scaling_mirror() -> None:
    """Mirror Platt calibration params from trading agent to Neo4j."""
    start = time.time()
    try:
        from agents.trading_scheduler_jobs import run_platt_scaling_mirror
        result = await run_platt_scaling_mirror(
            trading_agent_url=config.TRADING_AGENT_URL,
            neo4j=get_neo4j(),
        )
        mirrored = result.get("mirrored", 0)
        duration = time.time() - start
        _log_execution("platt_scaling_mirror", result.get("status", "unknown"), duration, f"{mirrored} mirrored")
        logger.info(f"Scheduled Platt mirror: {mirrored} mirrored in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("platt_scaling_mirror", "error", duration, str(e))
        logger.error(f"Scheduled Platt mirror failed: {e}")


async def _run_longshot_surface_rebuild() -> None:
    """Rebuild calibration surface from trading agent data."""
    start = time.time()
    try:
        from agents.trading_scheduler_jobs import run_longshot_surface_rebuild
        result = await run_longshot_surface_rebuild(
            trading_agent_url=config.TRADING_AGENT_URL,
            neo4j=get_neo4j(),
        )
        status = result.get("status", "unknown")
        points = result.get("points_stored", 0)
        duration = time.time() - start
        _log_execution("longshot_surface_rebuild", status, duration, f"{points} points")
        logger.info(f"Scheduled longshot surface rebuild: {status} ({points} points) in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("longshot_surface_rebuild", "error", duration, str(e))
        logger.error(f"Scheduled longshot surface rebuild failed: {e}")


def register_trading_jobs(scheduler: object, cfg: object) -> None:
    """Register trading cron jobs on the given APScheduler instance.

    Args:
        scheduler: AsyncIOScheduler instance
        cfg: config module (must have CERID_TRADING_ENABLED, SCHEDULE_TRADING_AUTORESEARCH, etc.)
    """
    from apscheduler.triggers.cron import CronTrigger

    if not getattr(cfg, "CERID_TRADING_ENABLED", False):
        return

    _trading_schedule = getattr(cfg, "SCHEDULE_TRADING_AUTORESEARCH", "")
    if _trading_schedule:
        scheduler.add_job(  # type: ignore[union-attr]
            _run_trading_autoresearch,
            CronTrigger.from_crontab(_trading_schedule),
            id="trading_autoresearch",
            name="Trading auto-research",
            replace_existing=True,
        )

    _platt_schedule = getattr(cfg, "SCHEDULE_PLATT_MIRROR", "")
    if _platt_schedule:
        scheduler.add_job(  # type: ignore[union-attr]
            _run_platt_scaling_mirror,
            CronTrigger.from_crontab(_platt_schedule),
            id="platt_scaling_mirror",
            name="Platt scaling mirror",
            replace_existing=True,
        )

    _longshot_schedule = getattr(cfg, "SCHEDULE_LONGSHOT_SURFACE", "")
    if _longshot_schedule:
        scheduler.add_job(  # type: ignore[union-attr]
            _run_longshot_surface_rebuild,
            CronTrigger.from_crontab(_longshot_schedule),
            id="longshot_surface_rebuild",
            name="Longshot surface rebuild",
            replace_existing=True,
        )

    logger.info("Trading scheduler jobs registered (CERID_TRADING_ENABLED=true)")
```

- [ ] **Step 2:** Edit `src/mcp/app/scheduler.py` -- remove the 3 trading job functions (lines 172-227). Remove `_run_trading_autoresearch`, `_run_platt_scaling_mirror`, and `_run_longshot_surface_rebuild`.

- [ ] **Step 3:** Remove the trading job registration block from `start_scheduler()` (lines 315-344). Remove everything from `# Trading jobs (gated by CERID_TRADING_ENABLED + per-job schedule config)` through `logger.info("Trading scheduler jobs registered (CERID_TRADING_ENABLED=true)")`.

- [ ] **Step 4:** Add hook marker + bootstrap block at the bottom of `scheduler.py` (or within `start_scheduler()` right before `_scheduler.start()`):

In `start_scheduler()`, just before the `_scheduler.start()` call (and after all other job registrations), add:

```python
    # -- Internal scheduler jobs -------------------------------------------------
    # Below this line: internal-only bootstrap (stripped for public distribution)
    try:
        from app.scheduler_internal import register_trading_jobs
        register_trading_jobs(_scheduler, config)
    except ImportError:
        pass
```

- [ ] **Step 5:** Verify:

```bash
grep -c "_run_trading_autoresearch\|_run_platt_scaling_mirror\|_run_longshot_surface_rebuild" src/mcp/app/scheduler.py
# Expected: 0

grep -c "register_trading_jobs" src/mcp/app/scheduler.py
# Expected: 1 (in the bootstrap block)
```

- [ ] **Step 6: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/app/scheduler.py src/mcp/app/scheduler_internal.py
git commit -m "refactor: extract trading scheduler jobs to scheduler_internal.py"
```

---

## Phase 4: Frontend Split

### Task 8: Update frontend types

**Files:**
- Create: `src/web/src/lib/types_internal.ts`
- Modify: `src/web/src/lib/types.ts`

- [ ] **Step 1:** Edit `src/web/src/lib/types.ts` -- remove the `trading_enabled` field from `CeridSettings` interface (line 731):

```typescript
  // Trading agent integration
  trading_enabled?: boolean
```

Remove both lines (the comment and the field).

- [ ] **Step 2:** Edit `src/web/src/lib/types.ts` -- simplify `PluginStatus` type (line 1132). Replace:

```typescript
export type PluginStatus = "installed" | "active" | "error" | "disabled" | "requires_pro" | "requires_enterprise"
```

with:

```typescript
export type PluginStatus = "installed" | "active" | "error" | "disabled"
```

- [ ] **Step 3:** Edit `src/web/src/lib/types.ts` -- remove `FeatureTier` type (line 1134). Delete:

```typescript
export type FeatureTier = "community" | "pro" | "enterprise"
```

- [ ] **Step 4:** Create `src/web/src/lib/types_internal.ts`:

```typescript
/**
 * Internal-only type extensions for trading, boardroom, and enterprise features.
 *
 * This file exists only in cerid-ai-internal. The public repo uses
 * types.ts directly without these extensions.
 */

// Re-export everything from the base types
export * from './types'

// Override types with internal variants
import type { CeridSettings as BaseCeridSettings } from './types'

// Internal settings extend base with trading config
export interface InternalSettings extends BaseCeridSettings {
  trading_enabled?: boolean
}

// Internal PluginStatus includes enterprise tier variants
export type InternalPluginStatus =
  | "installed"
  | "active"
  | "error"
  | "disabled"
  | "requires_pro"
  | "requires_enterprise"

// Enterprise feature tier concept
export type FeatureTier = "community" | "pro" | "enterprise"
```

- [ ] **Step 5:** Verify:

```bash
grep -c "trading_enabled" src/web/src/lib/types.ts
# Expected: 0

grep -c "requires_pro" src/web/src/lib/types.ts
# Expected: 0

grep -c "FeatureTier" src/web/src/lib/types.ts
# Expected: 0

grep -c "trading_enabled" src/web/src/lib/types_internal.ts
# Expected: 1
```

- [ ] **Step 6:** Check for any imports of `FeatureTier` across the frontend:

```bash
grep -rn "FeatureTier" src/web/src/ --include='*.ts' --include='*.tsx' | grep -v types_internal
```

If any files import `FeatureTier`, update them to import from `types_internal` instead.

- [ ] **Step 7: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add src/web/src/lib/types.ts src/web/src/lib/types_internal.ts
git commit -m "refactor: extract internal-only frontend types to types_internal.ts"
```

---

## Phase 5: Cross-Cutting Wiring

### Task 9: Update imports across codebase

**Files:**
- Modify: (varies -- discovered by grep)

- [ ] **Step 1:** Run comprehensive grep for all references to moved symbols in non-internal files:

```bash
cd ~/Develop/cerid-ai-internal
grep -rn "CERID_TRADING_ENABLED\|CERID_BOARDROOM_ENABLED\|TRADING_AGENT_URL\|CERID_BOARDROOM_TIER" src/mcp/ --include='*.py' | grep -v '_internal.py' | grep -v __pycache__ | grep -v 'config/settings.py'
```

- [ ] **Step 2:** For each match found in step 1, determine the correct action:
  - If the file is a `_internal.py` companion: no action needed (already has correct context)
  - If the reference is in a base file that was already split: the reference should have been removed in the corresponding task -- verify and fix
  - If the reference is in an unrelated file (e.g., test files): the reference still works because `extend_settings()` injects the attributes onto `config.settings` at startup. No change needed IF the code imports via `config.settings.CERID_TRADING_ENABLED` or `import config; config.CERID_TRADING_ENABLED`
  - If the code does `from config.settings import CERID_TRADING_ENABLED` at module level in a file that loads BEFORE `main_internal.py` bootstraps: it will get the default `False` value. This is correct behavior for the public repo but may need attention in internal

- [ ] **Step 3:** Verify that `config/__init__.py` re-exports settings attributes (if it does -- check with `grep -n "CERID_TRADING" src/mcp/config/__init__.py`). The pattern `import config; config.CERID_TRADING_ENABLED` relies on `config/__init__.py` doing `from config.settings import *` or explicit re-exports.

- [ ] **Step 4:** Check trading model imports in the SDK internal version work:

```bash
python3 -c "import ast; ast.parse(open('src/mcp/app/routers/sdk_internal.py').read())"
```

- [ ] **Step 5:** Check that test mock paths are correct. Run:

```bash
grep -rn "mock.*CERID_TRADING_ENABLED\|patch.*CERID_TRADING_ENABLED" src/mcp/tests/ --include='*.py' | head -20
```

Any test that patches `config.settings.CERID_TRADING_ENABLED` will still work because `extend_settings()` writes to that exact module attribute. Tests that patch `config.CERID_TRADING_ENABLED` may need updating.

- [ ] **Step 6: Commit** (if any changes were needed)

```bash
cd ~/Develop/cerid-ai-internal
git add -u
git commit -m "fix: update imports for surgical file split refactoring"
```

---

### Task 10: Create bootstrap wiring in main_internal.py

**Files:**
- Modify: `src/mcp/app/main_internal.py` (already created in Task 5)

This task is already implemented in Task 5 -- `bootstrap_internal_config()` calls `extend_settings()` then `extend_taxonomy()` before routers are registered.

- [ ] **Step 1:** Verify the bootstrap order is correct. The `main.py` bootstrap block must call `bootstrap_internal_config()` BEFORE `register_internal_routers(app)`:

```python
try:
    from app.main_internal import bootstrap_internal_config, register_internal_routers, get_internal_shutdown_hooks
    bootstrap_internal_config()          # <-- settings + taxonomy extended first
    register_internal_routers(app)       # <-- routers can now read CERID_TRADING_ENABLED
    _internal_shutdown_hooks = get_internal_shutdown_hooks()
except ImportError:
    _internal_shutdown_hooks = []
```

- [ ] **Step 2:** Verify that the bootstrap block in `main.py` is placed BEFORE any router that reads `CERID_TRADING_ENABLED`. Check the placement relative to `app.include_router(sdk.router)` and `app.include_router(agents.router)`. The bootstrap block should come AFTER the main `_api_routers` loop (which includes agents and SDK routers that have bootstrap blocks of their own at the bottom of their files).

The ordering is:
1. `_api_routers` loop registers agents.router and sdk.router (their bootstrap blocks run at import time, reading `CERID_TRADING_ENABLED`)
2. The `main.py` bootstrap calls `bootstrap_internal_config()` which writes `CERID_TRADING_ENABLED = True` to the settings module

**Problem:** The agents.py and sdk.py bootstrap blocks execute at import time (when the module is first loaded), but `extend_settings()` hasn't run yet at that point.

**Fix:** Move the `bootstrap_internal_config()` call to BEFORE the router imports. In `main.py`, add the config bootstrap BEFORE the `from app.routers import ...` block:

```python
# Bootstrap internal config before any router import reads settings
try:
    from app.main_internal import bootstrap_internal_config
    bootstrap_internal_config()
except ImportError:
    pass
```

Then the hook marker + `register_internal_routers(app)` block stays at the bottom.

- [ ] **Step 3:** Update `main.py` accordingly: the config bootstrap goes early (before router imports), and the router registration goes at the bottom.

- [ ] **Step 4: Commit** (amend to Task 5 commit or create new)

```bash
cd ~/Develop/cerid-ai-internal
git add src/mcp/app/main.py
git commit -m "fix: bootstrap internal config before router imports in main.py"
```

---

## Phase 6: Sync Tooling

### Task 11: Create .sync-manifest.yaml

**Files:**
- Create: `.sync-manifest.yaml`

- [ ] **Step 1:** Create `.sync-manifest.yaml` at repo root:

```yaml
# Sync Manifest -- defines what's internal-only vs public
# Used by scripts/sync-repos.py to automate repo sync

# Files that exist ONLY in the internal repo (never copied to public)
internal_only:
  # Internal extension files
  - "src/mcp/config/settings_internal.py"
  - "src/mcp/config/taxonomy_internal.py"
  - "src/mcp/app/routers/agents_internal.py"
  - "src/mcp/app/routers/sdk_internal.py"
  - "src/mcp/app/main_internal.py"
  - "src/mcp/app/tools_internal.py"
  - "src/mcp/app/scheduler_internal.py"
  - "src/web/src/lib/types_internal.ts"
  # Enterprise overlay
  - "src/mcp/enterprise/**"
  # Trading models and agents
  - "src/mcp/app/models/trading.py"
  - "src/mcp/agents/trading_agent.py"
  - "src/mcp/agents/trading_scheduler_jobs.py"
  - "src/mcp/app/routers/trading_proxy.py"
  # Desktop app
  - "packages/desktop/**"
  # Internal tests
  - "src/mcp/tests/test_trading_*.py"
  - "src/mcp/tests/test_boardroom_*.py"
  - "src/mcp/tests/test_enterprise_*.py"
  # Sync tooling itself
  - ".sync-manifest.yaml"
  - "scripts/sync-repos.py"

# Files where internal version = public version + appended hook block
# The sync script truncates at hook_marker (to-public) or appends (from-public)
mixed_files:
  - path: "src/mcp/config/settings.py"
    hook_marker: "# -- Internal settings -----"
  - path: "src/mcp/config/taxonomy.py"
    hook_marker: "# -- Internal taxonomy -----"
  - path: "src/mcp/app/routers/agents.py"
    hook_marker: "# -- Trading endpoints -----"
  - path: "src/mcp/app/routers/sdk.py"
    hook_marker: "# -- Internal SDK endpoints -----"
  - path: "src/mcp/app/main.py"
    hook_marker: "# -- Internal routers -----"
  - path: "src/mcp/app/tools.py"
    hook_marker: "# -- Internal tools -----"
  - path: "src/mcp/app/scheduler.py"
    hook_marker: "# -- Internal scheduler jobs -----"
  - path: "src/web/src/lib/types.ts"
    hook_marker: null  # No hook block -- just a clean file

# Strings that must NEVER appear in the public repo (leak detection)
forbidden_in_public:
  - "CERID_TRADING_ENABLED"
  - "CERID_BOARDROOM_ENABLED"
  - "CERID_BOARDROOM_TIER"
  - "TRADING_AGENT_URL"
  - "trading-agent"
  - "boardroom-agent"
  - "pkb_trading_signal"
  - "pkb_herd_detect"
  - "pkb_kelly_size"
  - "pkb_cascade_confirm"
  - "pkb_longshot_surface"
  - "trading_proxy"
  - "_internal.py"
  - "settings_internal"
  - "taxonomy_internal"
  - "agents_internal"
  - "sdk_internal"
  - "main_internal"
  - "tools_internal"
  - "scheduler_internal"
  - "types_internal"
  - "enterprise/"
  - "structlog"
```

- [ ] **Step 2: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add .sync-manifest.yaml
git commit -m "build: add sync manifest for surgical file splits"
```

---

### Task 12: Create scripts/sync-repos.py

**Files:**
- Create: `scripts/sync-repos.py`

- [ ] **Step 1:** Create `scripts/sync-repos.py`:

```python
#!/usr/bin/env python3
"""Sync script for cerid-ai-internal <-> cerid-ai (public) repos.

Usage:
    scripts/sync-repos.py to-public [--dry-run]
    scripts/sync-repos.py from-public [--dry-run]
    scripts/sync-repos.py validate

Reads .sync-manifest.yaml from the internal repo root.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml  # PyYAML -- already in requirements.txt


def _load_manifest(repo_root: Path) -> dict:
    manifest_path = repo_root / ".sync-manifest.yaml"
    if not manifest_path.exists():
        print(f"ERROR: {manifest_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(manifest_path) as f:
        return yaml.safe_load(f)


def _is_internal_only(rel_path: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any internal_only glob pattern."""
    from fnmatch import fnmatch
    for pattern in patterns:
        if fnmatch(rel_path, pattern):
            return True
    return False


def _truncate_at_marker(content: str, marker: str) -> str:
    """Truncate file content at the hook marker line."""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if marker in line:
            # Keep everything before the marker line, strip trailing blank lines
            result = "\n".join(lines[:i]).rstrip() + "\n"
            return result
    return content  # No marker found -- return as-is


def _get_hook_block(content: str, marker: str) -> str:
    """Extract everything from the hook marker line onward."""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if marker in line:
            return "\n".join(lines[i:])
    return ""


def _scan_for_leaks(directory: Path, forbidden: list[str], exclude_patterns: list[str]) -> list[tuple[str, str, int]]:
    """Scan files for forbidden strings. Returns list of (filepath, string, line_number)."""
    hits: list[tuple[str, str, int]] = []
    for root, dirs, files in os.walk(directory):
        # Skip hidden dirs, node_modules, __pycache__, .git
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "dist", ".git")]
        for fname in files:
            if not (fname.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml", ".json", ".md"))):
                continue
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(directory))
            if _is_internal_only(rel, exclude_patterns):
                continue
            try:
                text = fpath.read_text(errors="replace")
            except Exception:
                continue
            for line_num, line in enumerate(text.split("\n"), 1):
                for term in forbidden:
                    if term in line:
                        hits.append((rel, term, line_num))
    return hits


def cmd_to_public(internal_root: Path, public_root: Path, dry_run: bool) -> int:
    """Sync internal -> public. Skip internal_only, truncate mixed files at marker."""
    manifest = _load_manifest(internal_root)
    internal_only = manifest.get("internal_only", [])
    mixed_files = {m["path"]: m.get("hook_marker") for m in manifest.get("mixed_files", [])}
    forbidden = manifest.get("forbidden_in_public", [])

    copied = 0
    truncated = 0
    skipped = 0
    errors = 0

    for root, dirs, files in os.walk(internal_root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", "dist", ".venv", "stacks")]
        for fname in files:
            src = Path(root) / fname
            rel = str(src.relative_to(internal_root))

            # Skip internal-only files
            if _is_internal_only(rel, internal_only):
                skipped += 1
                if dry_run:
                    print(f"  SKIP (internal-only): {rel}")
                continue

            dst = public_root / rel

            # Handle mixed files -- truncate at marker
            if rel in mixed_files:
                marker = mixed_files[rel]
                if marker:
                    content = src.read_text()
                    truncated_content = _truncate_at_marker(content, marker)
                    if dry_run:
                        print(f"  TRUNCATE: {rel} (marker: {marker!r})")
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        dst.write_text(truncated_content)
                    truncated += 1
                    continue
                else:
                    # No marker (e.g., types.ts) -- copy as-is
                    pass

            # Regular file copy
            if dry_run:
                print(f"  COPY: {rel}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied += 1

    print(f"\n{'DRY RUN: ' if dry_run else ''}Copied: {copied}, Truncated: {truncated}, Skipped: {skipped}")

    # Leak scan on destination
    if not dry_run:
        hits = _scan_for_leaks(public_root, forbidden, [])
    else:
        # Scan internal repo for what WOULD leak
        hits = []
        for mf in manifest.get("mixed_files", []):
            path = internal_root / mf["path"]
            marker = mf.get("hook_marker")
            if not path.exists():
                continue
            content = path.read_text()
            if marker:
                content = _truncate_at_marker(content, marker)
            for line_num, line in enumerate(content.split("\n"), 1):
                for term in forbidden:
                    if term in line:
                        hits.append((mf["path"], term, line_num))

    if hits:
        print(f"\nLEAK DETECTION: {len(hits)} forbidden string(s) found!")
        for filepath, term, line_num in hits[:20]:
            print(f"  {filepath}:{line_num} -- {term!r}")
        return 1

    print("\nLeak scan: CLEAN")
    return 0


def cmd_from_public(internal_root: Path, public_root: Path, dry_run: bool) -> int:
    """Sync public -> internal. Re-append hook blocks from current internal."""
    manifest = _load_manifest(internal_root)
    internal_only = manifest.get("internal_only", [])
    mixed_files = {m["path"]: m.get("hook_marker") for m in manifest.get("mixed_files", [])}

    copied = 0
    merged = 0

    for root, dirs, files in os.walk(public_root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", "dist")]
        for fname in files:
            src = Path(root) / fname
            rel = str(src.relative_to(public_root))

            # Skip files that are internal-only (shouldn't exist in public, but just in case)
            if _is_internal_only(rel, internal_only):
                continue

            dst = internal_root / rel

            if rel in mixed_files:
                marker = mixed_files[rel]
                if marker and dst.exists():
                    # Get the hook block from the current internal version
                    internal_content = dst.read_text()
                    hook_block = _get_hook_block(internal_content, marker)

                    # Read public content and append hook block
                    public_content = src.read_text().rstrip()
                    merged_content = public_content + "\n\n" + hook_block + "\n" if hook_block else public_content + "\n"

                    if dry_run:
                        print(f"  MERGE: {rel} (re-append hook block)")
                    else:
                        dst.write_text(merged_content)
                    merged += 1
                    continue

            if dry_run:
                print(f"  COPY: {rel}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied += 1

    print(f"\n{'DRY RUN: ' if dry_run else ''}Copied: {copied}, Merged: {merged}")
    return 0


def cmd_validate(internal_root: Path, public_root: Path | None) -> int:
    """Validate both repos for leaks and missing config."""
    manifest = _load_manifest(internal_root)
    forbidden = manifest.get("forbidden_in_public", [])
    mixed_files = manifest.get("mixed_files", [])
    internal_only = manifest.get("internal_only", [])
    errors = 0

    # Check all internal extension files exist
    print("Checking internal extension files exist...")
    for pattern in internal_only:
        if "**" in pattern or "*" in pattern:
            continue  # Skip glob patterns
        fpath = internal_root / pattern
        if not fpath.exists():
            print(f"  MISSING: {pattern}")
            errors += 1

    # Check all mixed files have markers
    print("Checking hook markers in mixed files...")
    for mf in mixed_files:
        fpath = internal_root / mf["path"]
        marker = mf.get("hook_marker")
        if not fpath.exists():
            print(f"  MISSING: {mf['path']}")
            errors += 1
            continue
        if marker:
            content = fpath.read_text()
            if marker not in content:
                print(f"  NO MARKER: {mf['path']} (expected {marker!r})")
                errors += 1

    # Scan public repo for leaks (if provided)
    if public_root and public_root.exists():
        print(f"Scanning public repo for leaks: {public_root}")
        hits = _scan_for_leaks(public_root, forbidden, [])
        if hits:
            print(f"  LEAKS FOUND: {len(hits)}")
            for filepath, term, line_num in hits[:20]:
                print(f"    {filepath}:{line_num} -- {term!r}")
            errors += len(hits)
        else:
            print("  Public repo: CLEAN")

    if errors:
        print(f"\nVALIDATION FAILED: {errors} error(s)")
        return 1
    print("\nVALIDATION PASSED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync cerid-ai-internal <-> cerid-ai repos")
    parser.add_argument("command", choices=["to-public", "from-public", "validate"])
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without writing")
    parser.add_argument("--internal", default=None, help="Path to internal repo (default: script's parent's parent)")
    parser.add_argument("--public", default=None, help="Path to public repo")
    args = parser.parse_args()

    internal_root = Path(args.internal) if args.internal else Path(__file__).resolve().parent.parent
    public_root = Path(args.public) if args.public else Path.home() / "Develop" / "cerid-ai"

    if not internal_root.exists():
        print(f"ERROR: Internal repo not found: {internal_root}", file=sys.stderr)
        return 1

    if args.command == "validate":
        return cmd_validate(internal_root, public_root if public_root.exists() else None)

    if not public_root.exists():
        print(f"ERROR: Public repo not found: {public_root}", file=sys.stderr)
        return 1

    if args.command == "to-public":
        return cmd_to_public(internal_root, public_root, args.dry_run)
    elif args.command == "from-public":
        return cmd_from_public(internal_root, public_root, args.dry_run)

    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2:** Make the script executable:

```bash
chmod +x scripts/sync-repos.py
```

- [ ] **Step 3: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add scripts/sync-repos.py
git commit -m "build: add sync-repos.py for automated repo sync"
```

---

### Task 13: Create CI validation

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.sync-forbidden.txt` (for public repo reference)

- [ ] **Step 1:** Add `sync-validate` job to `.github/workflows/ci.yml`. Add after the existing `lint` job:

```yaml
  sync-validate:
    runs-on: ubuntu-latest
    timeout-minutes: 3
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
      - run: pip install pyyaml
      - name: Validate sync manifest
        run: python scripts/sync-repos.py validate --internal .
      - name: Dry-run to-public
        run: |
          mkdir -p /tmp/cerid-public
          python scripts/sync-repos.py to-public --dry-run --internal . --public /tmp/cerid-public
```

- [ ] **Step 2:** Create `.sync-forbidden.txt` (to be placed in the public repo during first sync):

```text
# Strings that must never appear in the public cerid-ai repo.
# CI scans all files for these patterns. One per line.
CERID_TRADING_ENABLED
CERID_BOARDROOM_ENABLED
CERID_BOARDROOM_TIER
TRADING_AGENT_URL
pkb_trading_signal
pkb_herd_detect
pkb_kelly_size
pkb_cascade_confirm
pkb_longshot_surface
trading_proxy
_internal.py
settings_internal
taxonomy_internal
agents_internal
sdk_internal
main_internal
tools_internal
scheduler_internal
types_internal
structlog
```

- [ ] **Step 3: Commit**

```bash
cd ~/Develop/cerid-ai-internal
git add .github/workflows/ci.yml .sync-forbidden.txt
git commit -m "ci: add sync-validate job and forbidden strings list"
```

---

## Phase 7: Verification and Execution

### Task 14: Verify end-to-end

**Files:**
- (no file changes -- verification only)

- [ ] **Step 1:** Run ruff on the backend:

```bash
cd ~/Develop/cerid-ai-internal
ruff check src/mcp/
```

Fix any lint errors.

- [ ] **Step 2:** Run TypeScript type check on the frontend:

```bash
cd ~/Develop/cerid-ai-internal/src/web
npx tsc --noEmit
```

Fix any type errors.

- [ ] **Step 3:** Run the sync validation:

```bash
cd ~/Develop/cerid-ai-internal
python scripts/sync-repos.py validate --internal .
```

- [ ] **Step 4:** Run to-public dry run:

```bash
cd ~/Develop/cerid-ai-internal
mkdir -p /tmp/cerid-public-test
python scripts/sync-repos.py to-public --dry-run --internal . --public /tmp/cerid-public-test
```

- [ ] **Step 5:** Grep the dry-run output for forbidden strings. Verify zero hits from the leak scan.

- [ ] **Step 6:** Run the Python test suite:

```bash
cd ~/Develop/cerid-ai-internal
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v --timeout=60"
```

- [ ] **Step 7:** Run the frontend test suite:

```bash
cd ~/Develop/cerid-ai-internal/src/web
npx vitest run
```

- [ ] **Step 8: Commit** any fixes from verification:

```bash
cd ~/Develop/cerid-ai-internal
git add -u
git commit -m "fix: address lint/type/test issues from surgical file splits"
```

---

### Task 15: Execute first sync

**Files:**
- (changes in the public repo ~/Develop/cerid-ai)

- [ ] **Step 1:** Run the actual to-public sync:

```bash
cd ~/Develop/cerid-ai-internal
python scripts/sync-repos.py to-public --internal . --public ~/Develop/cerid-ai
```

- [ ] **Step 2:** Verify the public repo is clean:

```bash
cd ~/Develop/cerid-ai
grep -rn "CERID_TRADING_ENABLED\|CERID_BOARDROOM_ENABLED\|_internal\.py" src/ --include='*.py' --include='*.ts'
# Expected: 0 results
```

- [ ] **Step 3:** Commit in the public repo:

```bash
cd ~/Develop/cerid-ai
git add -A
git commit -m "refactor: clean public distribution from surgical file splits"
```

- [ ] **Step 4:** Validate both directions:

```bash
cd ~/Develop/cerid-ai-internal
python scripts/sync-repos.py validate --internal . --public ~/Develop/cerid-ai
```

- [ ] **Step 5:** Test from-public round-trip:

```bash
cd ~/Develop/cerid-ai-internal
python scripts/sync-repos.py from-public --dry-run --internal . --public ~/Develop/cerid-ai
```

Verify the dry run shows MERGE for mixed files and COPY for regular files.

- [ ] **Step 6: Final commit** in internal repo:

```bash
cd ~/Develop/cerid-ai-internal
git add -u
git commit -m "chore: complete surgical file splits -- sync tooling verified"
```

---

## Summary

| Task | Component | Files Created | Files Modified |
|------|-----------|---------------|----------------|
| 1 | config/settings | settings_internal.py | settings.py |
| 2 | config/taxonomy | taxonomy_internal.py | taxonomy.py |
| 3 | app/routers/agents | agents_internal.py | agents.py |
| 4 | app/routers/sdk | sdk_internal.py | sdk.py |
| 5 | app/main | main_internal.py | main.py |
| 6 | app/tools | tools_internal.py | tools.py |
| 7 | app/scheduler | scheduler_internal.py | scheduler.py |
| 8 | frontend types | types_internal.ts | types.ts |
| 9 | imports | -- | varies |
| 10 | bootstrap wiring | -- | main.py |
| 11 | sync manifest | .sync-manifest.yaml | -- |
| 12 | sync script | scripts/sync-repos.py | -- |
| 13 | CI validation | .sync-forbidden.txt | ci.yml |
| 14 | verification | -- | -- |
| 15 | first sync | -- | (public repo) |

**Total: 10 new files, ~8 modified files, ~15 commits**

**Critical ordering:** Tasks 1-2 (config layer) must complete before Task 10 (bootstrap wiring). Tasks 3-7 can be parallelized. Task 8 is independent. Tasks 9-10 depend on 1-8. Tasks 11-13 can be done in parallel with 9-10. Task 14 depends on all prior. Task 15 depends on 14.

**Recommended parallelization for subagent-driven-development:**
- Agent 1: Tasks 1, 2 (config layer)
- Agent 2: Tasks 3, 4 (router layer)
- Agent 3: Tasks 5, 6, 7 (app layer)
- Agent 4: Task 8 (frontend)
- Agent 5: Tasks 11, 12, 13 (sync tooling)
- Sequential: Tasks 9, 10, 14, 15 (after all agents complete)
