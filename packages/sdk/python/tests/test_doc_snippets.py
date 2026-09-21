# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute every Python snippet in the shipped docs against the real client.

``README.md`` is the PyPI long_description and ``docs/SDK_GUIDE.md`` is the
canonical integration guide — they are the first code a consumer runs, and
nothing was executing them. Every snippet below is run against the actual
``cerid`` resource methods with only the socket faked, so a renamed method, a
wrong keyword, or an attribute that doesn't exist fails here instead of in a
new user's terminal.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cerid import AsyncCeridClient, CeridClient

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[2]

DOCS = [PACKAGE_ROOT / "README.md", REPO_ROOT / "docs" / "SDK_GUIDE.md"]

# One canned 200 per endpoint, shaped like the server's response models.
RESPONSES: dict[str, dict[str, Any]] = {
    "/sdk/v1/query": {
        "context": "Chunking splits documents on semantic boundaries.",
        "sources": [{"content": "…", "relevance": 0.92}],
        "confidence": 0.92,
        "domains_searched": ["coding"],
        "total_results": 1,
        "token_budget_used": 120,
        "graph_results": 0,
        "results": [{"content": "Chunking splits documents.", "relevance": 0.92, "domain": "coding"}],
    },
    "/sdk/v1/search": {
        "results": [{"content": "auth.py", "relevance": 0.88}],
        "total_results": 1,
        "confidence": 0.88,
    },
    "/sdk/v1/ingest": {"status": "success", "artifact_id": "art-200", "chunks": 1, "domain": "databases"},
    "/sdk/v1/ingest/file": {"status": "success", "artifact_id": "art-201", "chunks": 3, "domain": "databases"},
    "/sdk/v1/ingest/external": {"accepted": 1, "skipped": 0, "errors": [], "source_type": "readwise"},
    "/sdk/v1/hallucination": {
        "conversation_id": "demo",
        "timestamp": "2026-09-03T00:00:00Z",
        "skipped": False,
        "reason": None,
        "claims": [{"claim": "Redis defaults to port 6380.", "status": "unverified", "confidence": 0.12}],
        "summary": {"total": 1, "verified": 0, "unverified": 1, "assessed": 1, "overall_confidence": 0.12},
        "mode": "thorough",
        "nli_skipped": False,
    },
    "/sdk/v1/memory/extract": {
        "conversation_id": "demo",
        "timestamp": "2026-09-03T00:00:00Z",
        "memories_extracted": 2,
        "memories_stored": 2,
        "skipped_duplicates": 0,
        "results": [],
    },
    "/sdk/v1/llm/complete": {
        "content": "Own your memory.",
        "model": "meta-llama/llama-3.3-70b-instruct",
        "provider": "openrouter_paid",
        "reason": "moderate query — cost-sensitive tier",
        "estimated_cost_per_1k": 0.00015,
        "tier_p95_ms": 2400,
    },
    "/sdk/v1/health": {
        "status": "healthy",
        "version": "1.1.0",
        "services": {"chromadb": "connected", "redis": "connected"},
        "features": {"hallucination_check": True},
    },
    "/sdk/v1/health/detailed": {
        "status": "healthy", "version": "1.1.0", "services": {}, "features": {},
        "circuit_breakers": {}, "degradation_tier": "FULL", "uptime_seconds": 1200.0,
    },
    "/sdk/v1/settings": {"version": "1.1.0", "tier": "community", "features": {"hallucination_check": True}},
    "/sdk/v1/collections": {"collections": ["general", "coding"], "total": 2},
    "/sdk/v1/taxonomy": {"domains": ["general", "coding"], "taxonomy": {}},
    "/sdk/v1/plugins": {"plugins": [], "total": 0},
}


def _canned(url: str) -> httpx.Response:
    path = "/sdk/v1" + str(url).split("/sdk/v1", 1)[1]
    if path.startswith("/sdk/v1/memory/extract/jobs/"):
        body: dict[str, Any] = {"job_id": path.rsplit("/", 1)[-1], "status": "finished"}
    else:
        body = RESPONSES[path]
    return httpx.Response(200, json=body, request=httpx.Request("GET", str(url)))


def _fake_sync(self: Any, url: str, **kwargs: Any) -> httpx.Response:
    return _canned(url)


async def _fake_async(self: Any, url: str, **kwargs: Any) -> httpx.Response:
    return _canned(url)


def _python_blocks(path: Path) -> list[tuple[str, int, str]]:
    """Every ```python fenced block, with the line it starts on."""
    blocks = []
    for match in re.finditer(r"```python\n(.*?)```", path.read_text(), re.DOTALL):
        line = path.read_text()[: match.start()].count("\n") + 1
        blocks.append((path.name, line, match.group(1)))
    return blocks


ALL_BLOCKS = [block for doc in DOCS for block in _python_blocks(doc)]


def test_docs_actually_contain_python_snippets() -> None:
    """Guard against the extractor silently matching nothing."""
    assert len(ALL_BLOCKS) >= 6


@pytest.mark.parametrize("name,line,source", ALL_BLOCKS, ids=[f"{n}:{ln}" for n, ln, _ in ALL_BLOCKS])
def test_doc_snippet_runs(name: str, line: int, source: str) -> None:
    with (
        patch.object(httpx.Client, "post", _fake_sync),
        patch.object(httpx.Client, "get", _fake_sync),
        patch.object(httpx.AsyncClient, "post", _fake_async),
        patch.object(httpx.AsyncClient, "get", _fake_async),
    ):
        client = CeridClient(base_url="http://localhost:8888", client_id="my-app")
        # Snippets that continue a previous example use a bare `client`.
        namespace: dict[str, Any] = {
            "CeridClient": CeridClient,
            "AsyncCeridClient": AsyncCeridClient,
            "client": client,
            "__name__": "__doc_snippet__",
        }
        try:
            exec(compile(source, f"{name}:{line}", "exec"), namespace)  # noqa: S102
        finally:
            client.close()
