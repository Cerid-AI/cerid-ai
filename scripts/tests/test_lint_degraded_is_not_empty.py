# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plant-fault tests for scripts/lint-degraded-is-not-empty.py (gate G-E).

The must-not-fire cases carry the weight here. Restricting the gate to route
and tool handlers is what took the hit count from 291 to 8; if that scope
ever loosens, `test_internal_helper_returning_none_not_flagged` is what says
so before the allowlist balloons.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_degraded_is_not_empty", _ROOT / "scripts" / "lint-degraded-is-not-empty.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_degraded_is_not_empty"] = lint
_SPEC.loader.exec_module(lint)


def _check(source: str, rel: str = "src/mcp/app/routers/fake.py") -> list["lint.Violation"]:
    return lint.check_source(source, rel)


class TestRouteHandlers:
    def test_empty_list_return_flagged(self) -> None:
        src = (
            "@router.get('/briefs')\n"
            "async def list_briefs_endpoint():\n"
            "    try:\n"
            "        return await load()\n"
            "    except Exception:\n"
            "        return []\n"
        )
        v = _check(src)
        assert len(v) == 1
        assert v[0].lineno == 6
        assert v[0].kind == "route"

    def test_none_return_flagged(self) -> None:
        src = (
            "@router.get('/digests/latest')\n"
            "async def get_latest_digest():\n"
            "    try:\n"
            "        return await load()\n"
            "    except Exception:\n"
            "        return None\n"
        )
        assert len(_check(src)) == 1

    def test_all_empty_response_model_flagged(self) -> None:
        """StructuralGapsResponse(gaps=[]) — a 200 that reads as 'no gaps'."""
        src = (
            "@router.get('/graph/gaps')\n"
            "async def get_structural_gaps():\n"
            "    try:\n"
            "        return await compute()\n"
            "    except Exception:\n"
            "        return StructuralGapsResponse(gaps=[], total=0)\n"
        )
        assert len(_check(src)) == 1

    def test_zero_count_dict_flagged(self) -> None:
        src = (
            "@router.get('/plugins/community')\n"
            "async def list_community_plugins():\n"
            "    try:\n"
            "        return await fetch()\n"
            "    except Exception:\n"
            "        return {'plugins': [], 'total': 0}\n"
        )
        assert len(_check(src)) == 1


class TestDegradedSiblingAccepted:
    def test_degraded_reason_sibling_not_flagged(self) -> None:
        """The fix shape: still empty, but the emptiness is labelled."""
        src = (
            "@router.get('/briefs')\n"
            "async def list_briefs_endpoint():\n"
            "    try:\n"
            "        return await load()\n"
            "    except Exception as exc:\n"
            "        return {'briefs': [], 'degraded': True, 'degraded_reason': str(exc)}\n"
        )
        assert _check(src) == []

    def test_status_keyword_on_response_model_not_flagged(self) -> None:
        src = (
            "@router.get('/graph/gaps')\n"
            "async def get_structural_gaps():\n"
            "    try:\n"
            "        return await compute()\n"
            "    except Exception:\n"
            "        return StructuralGapsResponse(gaps=[], status='unavailable')\n"
        )
        assert _check(src) == []

    def test_reraise_not_flagged(self) -> None:
        src = (
            "@router.get('/briefs')\n"
            "async def list_briefs_endpoint():\n"
            "    try:\n"
            "        return await load()\n"
            "    except Exception:\n"
            "        logger.exception('load failed')\n"
            "        raise\n"
        )
        assert _check(src) == []

    def test_non_empty_payload_not_flagged(self) -> None:
        src = (
            "@router.get('/briefs')\n"
            "async def list_briefs_endpoint():\n"
            "    try:\n"
            "        return await load()\n"
            "    except Exception:\n"
            "        return await load_from_cache()\n"
        )
        assert _check(src) == []


class TestScope:
    def test_internal_helper_returning_none_not_flagged(self) -> None:
        """The scope decision, pinned. An undecorated helper's None is not a
        wire response; including these took the gate from 8 hits to 291."""
        src = (
            "async def _load_cached(key):\n"
            "    try:\n"
            "        return await redis.get(key)\n"
            "    except Exception:\n"
            "        return None\n"
        )
        assert _check(src) == []

    def test_mcp_tool_handler_in_scope(self) -> None:
        src = (
            "@register_tool(name='pkb_timeline')\n"
            "async def pkb_timeline(params):\n"
            "    try:\n"
            "        return await query()\n"
            "    except Exception:\n"
            "        return {'events': []}\n"
        )
        v = _check(src, "src/mcp/app/mcp_tools/temporal.py")
        assert len(v) == 1
        assert v[0].kind == "tool"

    def test_success_path_empty_return_not_flagged(self) -> None:
        """Only the except branch is in scope — a genuinely empty result is
        allowed to be empty."""
        src = (
            "@router.get('/briefs')\n"
            "async def list_briefs_endpoint():\n"
            "    rows = await load()\n"
            "    if not rows:\n"
            "        return []\n"
            "    return rows\n"
        )
        assert _check(src) == []


class TestEmptyShape:
    def test_bytes_literal_is_not_empty_shaped(self) -> None:
        """b'' is a valid body, not an absent one."""
        import ast

        assert not lint.is_empty_shaped(ast.parse("b''", mode="eval").body)

    def test_dict_with_a_nonempty_value_is_not_empty_shaped(self) -> None:
        import ast

        node = ast.parse("{'items': [], 'source': 'cache'}", mode="eval").body
        assert not lint.is_empty_shaped(node)


class TestAllowlist:
    def test_every_repo_entry_has_a_reason(self) -> None:
        for key, reason in lint._load_allowlist().items():
            assert reason and not reason.startswith("TODO"), f"{key} has no reason"

    def test_live_hits_match_the_allowlist(self) -> None:
        live = {v.key() for v in lint.collect_all()}
        allow = set(lint._load_allowlist())
        assert live == allow, (
            f"unallowlisted: {sorted(live - allow)}; stale: {sorted(allow - live)}"
        )
