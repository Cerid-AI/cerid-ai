# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Verification KB child quota: lightweight_kb_query must not stampede Chroma."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# test_hallucination.py may inject a stub core.agents.query_agent with
# lightweight_kb_query = None. Clear it so the real module loads.
_existing = sys.modules.get("core.agents.query_agent")
if _existing is not None and not hasattr(_existing, "_get_adjacent_domains"):
    del sys.modules["core.agents.query_agent"]


def _settings_text() -> str:
    return (Path(__file__).resolve().parents[1] / "config" / "settings.py").read_text()


def test_verify_claim_default_literal():
    """Hermetic: do not importlib.reload settings (mutates process config)."""
    text = _settings_text()
    assert (
        'VERIFY_CLAIM_MAX_CONCURRENT", "3"' in text
        or "VERIFY_CLAIM_MAX_CONCURRENT', '3'" in text
    )


def test_verify_kb_max_concurrent_default_literal():
    text = _settings_text()
    assert (
        'VERIFY_KB_MAX_CONCURRENT", "2"' in text
        or "VERIFY_KB_MAX_CONCURRENT', '2'" in text
    )


def test_verify_kb_sem_uses_config_not_second_getenv():
    """Semaphore bound must come from config, not a second import-time getenv."""
    text = (
        Path(__file__).resolve().parents[1] / "core" / "agents" / "query_agent.py"
    ).read_text()
    assert "config.VERIFY_KB_MAX_CONCURRENT" in text
    assert 'os.getenv("VERIFY_KB_MAX_CONCURRENT"' not in text
    assert "os.getenv('VERIFY_KB_MAX_CONCURRENT'" not in text


@pytest.mark.asyncio
async def test_lightweight_kb_query_does_not_run_unbounded_parallel(monkeypatch):
    from core.agents import query_agent as qa

    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_multi(*_a, **_k):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.05)
        async with lock:
            current -= 1
        return [{"content": "x", "relevance": 0.9}]

    monkeypatch.setattr(qa, "multi_domain_query", fake_multi)
    monkeypatch.setattr(qa.config, "VERIFICATION_MIN_RELEVANCE", 0.0, raising=False)

    await asyncio.gather(*[qa.lightweight_kb_query("q") for _ in range(8)])
    assert peak <= 2
