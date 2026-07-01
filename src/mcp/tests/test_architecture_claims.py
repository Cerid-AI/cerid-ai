# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Architecture-claims doc-drift gate.

Graduates root-cause cluster "verification incoherence + doc/architecture
overclaim" from tasks/2026-06-29-rag-api-systemic-audit.md. The team kept
reading a green snapshot while the architecture diverged: CLAUDE.md advertises
capabilities that are dormant, post-hoc, or not-yet-built, so the roadmap
believed features were done. This module maps each load-bearing capability
CLAIM to an executable assertion.

- Claims TRUE today -> plain passing test (regression guard: if the capability
  is removed, the doc is now lying and CI fails).
- Claims NOT YET true (deliberate targets) -> ``@pytest.mark.xfail(strict=True)``.
  The xfail keeps the gap VISIBLE and, crucially, when the target phase lands and
  the assertion starts passing, the strict-xfail FAILS — forcing the developer
  to remove the xfail and consciously update the claim's status. A claim can
  never silently flip from "aspirational" to "done" without a human touching
  this file.

Decision 2026-06-29: the team will BUILD true inline NLI gating (not reword the
claim to post-hoc). Phase 3 (2026-06-30) landed it — ``call_internal_llm_stream``
+ ``core.agents.hallucination.inline_gate`` — so that claim graduated from a
strict-xfail target to a plain passing regression guard below. No xfail targets
remain today.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MCP = _REPO_ROOT / "src" / "mcp"


def _src(rel: str) -> str:
    return (_MCP / rel).read_text(encoding="utf-8")


# ── Claims TRUE today (regression guards) ──────────────────────────────────

def test_claim_core_never_imports_app():
    """CLAUDE.md: 'core/ never imports from app/' (Phase C layer contract).

    AST-based so a docstring/comment mentioning ``from app.`` is not a false
    positive. This is a stricter executable form of the import-linter contract.
    Sprint LC (2026-06-29) DI-threaded the last two offenders (inbox_triage,
    daily_digest) via set_inbox_registry / set_digest_graph and removed the
    `.importlinter` ignore-list, so this is now a clean regression guard: if a
    new core/ module imports app/, both this test and `lint-imports` fail.
    """
    offenders: list[str] = []
    for p in (_MCP / "core").rglob("*.py"):
        if "tests" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
                offenders.append(f"{p.relative_to(_MCP)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "app":
                        offenders.append(f"{p.relative_to(_MCP)}:{node.lineno}")
    assert not offenders, f"core/ imports app/ in: {offenders}"


def test_claim_exclude_packs_exposed_on_agent_query():
    """CLAUDE.md (Slice 7.3): personal-first 'exclude_packs' on the agent query."""
    assert "exclude_packs" in _src("app/routers/agents.py"), \
        "exclude_packs missing from AgentQueryRequest / /agent/query"


def test_claim_exclude_packs_forwarded_by_mcp_tool():
    """The pkb_agent_query MCP tool advertises + forwards exclude_packs."""
    tools = _src("app/tools.py")
    assert tools.count("exclude_packs") >= 2, \
        "pkb_agent_query must declare AND forward exclude_packs (schema + dispatch)"


def test_claim_cerid_error_handler_registered():
    """Phase 2 (audit CEG-1): a CeridError exception handler is registered so
    domain errors render as structured JSON, not a bare 500, and the previously
    dead ``error_response`` renderer is wired. Regression guard: removing the
    handler fails here.
    """
    main = _src("app/main.py")
    handlers = _src("app/error_handlers.py")
    assert "register_cerid_error_handler(app)" in main, \
        "main.py does not register the CeridError handler"
    assert "exception_handler(CeridError)" in handlers and "error_response" in handlers, \
        "app/error_handlers.py does not wire CeridError -> error_response"


def test_claim_require_feature_raises_canonical_error():
    """Phase 2 (audit CEG-2): the tier gate raises the canonical FeatureGateError
    (rendered to 403 by the handler), not a raw HTTPException — one error type,
    one JSON shape across require_feature / check_feature / check_tier.
    """
    from errors import FeatureGateError

    src = _src("config/features.py")
    assert "raise FeatureGateError" in src, "require_feature no longer raises FeatureGateError"
    assert FeatureGateError.http_status == 403


def test_claim_inline_nli_gating_during_streaming():
    """CLAUDE.md snapshot: 'streaming verification, NLI entailment gating'.

    Landed Phase 3 (2026-06-30): ``internal_llm`` exposes a streaming synthesis
    variant (no hardcoded ``stream:False``), and
    ``core.agents.hallucination.inline_gate`` suppresses evidence-contradicted
    sentences mid-stream. The claim is now true — regression guard below.
    """
    internal_llm = _src("core/utils/internal_llm.py")
    assert '"stream": False' not in internal_llm, \
        "internal_llm re-introduced a hardcoded stream:False — inline gating regressed"
    assert "async def call_internal_llm_stream" in internal_llm, \
        "streaming synthesis entrypoint removed — inline gating cannot stream"
    gate = _src("core/agents/hallucination/inline_gate.py")
    assert "async def inline_nli_gate" in gate, \
        "inline NLI gate removed — mid-stream suppression gone"


def test_claim_all_retrieval_through_canonical_path():
    """CLAUDE.md: retrieval is surface-routed through the agent pipeline.

    Phase 1 (2026-06-29) deleted the hand-rolled ``query_knowledge`` path; /query
    and /sdk/v1/search now route through ``agent_query_full``. Regression guard:
    if a new bypass re-introduces ``query_knowledge`` into sdk.py, this fails.
    """
    sdk = _src("app/routers/sdk.py")
    assert "query_knowledge" not in sdk, \
        "/sdk/v1/search re-introduced a query_knowledge bypass of the canonical path"


def test_claim_exclude_packs_honored_on_every_surface():
    """``exclude_packs`` / pack provenance holds on the /query path too (Phase 1)."""
    query = _src("app/routers/query.py")
    assert "exclude_packs" in query or "pack_id" in query, \
        "/query no longer honors exclude_packs / pack provenance"
