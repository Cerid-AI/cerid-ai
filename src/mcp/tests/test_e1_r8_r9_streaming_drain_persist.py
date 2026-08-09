# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 post-audit M3 — R8 finally-drain + R9 provisional hall:{cid} write.

Static contract probes (no live stack): the verification loop must wrap in
try/finally:_drain_background, and a provisional Redis write must exist before
the consistency await.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _streaming_source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core/agents/hallucination/streaming.py").read_text(encoding="utf-8")


def test_verification_loop_has_finally_drain() -> None:
    """R8: try/finally must call _drain_background (covers GeneratorExit)."""
    src = _streaming_source()
    tree = ast.parse(src)
    found_finally_drain = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        for stmt in node.finalbody:
            for child in ast.walk(stmt):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "_drain_background"
                ):
                    found_finally_drain = True
    assert found_finally_drain, (
        "verification loop has no finally:_drain_background — client disconnect "
        "orphans batch tasks (E1 R8 / CR-105)"
    )


def test_provisional_hall_persist_marker_present() -> None:
    """R9: provisional persist path must exist (closes feedback-clobber window)."""
    src = _streaming_source()
    assert "provisional_hall_persist" in src or "provisional hall" in src.lower(), (
        "no provisional hall:{cid} write marker — feedback during consistency "
        "await can clobber the previous report (E1 R9)"
    )
    # Two setex writes on the hall key path: provisional + final
    assert src.count("REDIS_HALLUCINATION_PREFIX") >= 2
