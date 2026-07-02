# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures and dependency stubs for cerid-ai tests."""

import contextlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Add src/mcp to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Stub heavy native dependencies before any test module imports
# ---------------------------------------------------------------------------

def _ensure_stub(name, stub_module):
    """Register a stub module only if the real one isn't importable."""
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = stub_module


def pytest_configure(config):
    """Stub heavy dependencies not available on the test host."""

    # tiktoken
    _tiktoken = ModuleType("tiktoken")

    class _FakeEncoding:
        def encode(self, text):
            return text.split()

    _tiktoken.get_encoding = lambda name: _FakeEncoding()
    _ensure_stub("tiktoken", _tiktoken)

    # httpx
    _httpx = ModuleType("httpx")

    class _AsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return MagicMock()

    _httpx.AsyncClient = _AsyncClient
    _ensure_stub("httpx", _httpx)

    # spacy
    _spacy = ModuleType("spacy")
    _spacy.load = MagicMock(side_effect=OSError("stub"))
    _ensure_stub("spacy", _spacy)

    # chromadb (with submodules)
    _chromadb = ModuleType("chromadb")
    _chromadb.HttpClient = MagicMock
    _chromadb_config = ModuleType("chromadb.config")
    _chromadb_config.Settings = MagicMock
    _chromadb.config = _chromadb_config
    _ensure_stub("chromadb", _chromadb)
    _ensure_stub("chromadb.config", _chromadb_config)

    # neo4j
    _neo4j = ModuleType("neo4j")
    _neo4j.GraphDatabase = MagicMock()
    _ensure_stub("neo4j", _neo4j)

    # redis
    _redis_mod = ModuleType("redis")
    _redis_mod.Redis = MagicMock
    _ensure_stub("redis", _redis_mod)

    # pdfplumber
    _ensure_stub("pdfplumber", ModuleType("pdfplumber"))

    # openpyxl
    _ensure_stub("openpyxl", ModuleType("openpyxl"))

    # pandas
    _ensure_stub("pandas", ModuleType("pandas"))

    # docx
    _ensure_stub("docx", ModuleType("docx"))

    # apscheduler (for scheduler.py)
    _apscheduler = ModuleType("apscheduler")
    _apscheduler_schedulers = ModuleType("apscheduler.schedulers")
    _apscheduler_asyncio = ModuleType("apscheduler.schedulers.asyncio")
    _apscheduler_asyncio.AsyncIOScheduler = MagicMock
    _apscheduler_triggers = ModuleType("apscheduler.triggers")
    _apscheduler_cron = ModuleType("apscheduler.triggers.cron")
    _cron_trigger = MagicMock()
    _cron_trigger.from_crontab = MagicMock(return_value=MagicMock())
    _apscheduler_cron.CronTrigger = _cron_trigger
    _apscheduler.schedulers = _apscheduler_schedulers
    _apscheduler_schedulers.asyncio = _apscheduler_asyncio
    _apscheduler.triggers = _apscheduler_triggers
    _apscheduler_triggers.cron = _apscheduler_cron
    _ensure_stub("apscheduler", _apscheduler)
    _ensure_stub("apscheduler.schedulers", _apscheduler_schedulers)
    _ensure_stub("apscheduler.schedulers.asyncio", _apscheduler_asyncio)
    _ensure_stub("apscheduler.triggers", _apscheduler_triggers)
    _ensure_stub("apscheduler.triggers.cron", _apscheduler_cron)


# Pre-import the real ``core.agents.hallucination`` package at conftest load
# (before any test module is collected) so test_self_rag's "stub if not already
# imported" guard skips — otherwise it would install a bare, submodule-less stub
# into sys.modules that leaks into later tests patching
# ``core.agents.hallucination.verification`` / ``.streaming``. (Exposed when
# surface-biased retrieval became default-ON and shifted import timing.)
with contextlib.suppress(Exception):  # real hallucination package may be unavailable in some envs
    import core.agents.hallucination  # noqa: F401


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_llm_client():
    """Reset the shared LLM httpx.AsyncClient singleton between tests.

    Without this, _get_client() caches a client from the first test,
    preventing subsequent tests' patches from taking effect.  Also clears
    the claim_cache L1 in-memory cache to prevent cross-test leakage.
    Sets a dummy OPENROUTER_API_KEY so LLM calls reach the mocked
    httpx.AsyncClient rather than short-circuiting on the missing-key
    RuntimeError.
    """
    import os

    import core.utils.llm_client as _llm_mod
    from core.utils.circuit_breaker import get_breaker
    from core.utils.claim_cache import clear_l1_cache

    # Ensure LLM calls use the direct OpenRouter path (mockable via httpx.AsyncClient)
    old_key = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "test-dummy-key"  # pragma: allowlist secret

    # Also reset internal_llm client
    with contextlib.suppress(Exception):  # best-effort internal_llm client reset
        import core.utils.internal_llm as _internal_llm_mod
        _internal_llm_mod._ollama_client = None

    _llm_mod._client = None
    clear_l1_cache()
    # GA P0.5: surface-bias retrieval is now default-ON, so the C2 wiki-fetcher
    # registry must be reset between tests — a fetcher leaked from one test would
    # otherwise inject wiki results into later compiled_summary queries.
    with contextlib.suppress(Exception):  # best-effort wiki-fetcher registry reset
        from core.agents.query_agent import set_wiki_page_fetcher
        set_wiki_page_fetcher(None)
    # Reset all circuit breakers to prevent cross-test state leakage.
    # The "bifrost-*" breaker names survive as legacy identifiers for
    # historical call-site categories (rerank/claims/verify/...); Bifrost
    # itself was retired (audit C-4 + 2026-04-17 follow-up).
    for name in (
        "bifrost-rerank", "bifrost-claims", "bifrost-verify",
        "bifrost-synopsis", "bifrost-memory", "bifrost-compress",
        "bifrost-decompose", "web-search", "openrouter", "tavily",
        "searxng", "ragas_eval", "neo4j", "ollama",
    ):
        get_breaker(name).reset()
    yield
    _llm_mod._client = None
    clear_l1_cache()
    # Restore original API key state
    if old_key is None:
        os.environ.pop("OPENROUTER_API_KEY", None)
    else:
        os.environ["OPENROUTER_API_KEY"] = old_key


@pytest.fixture
def mock_neo4j():
    """Mock Neo4j driver with session context manager."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session


@pytest.fixture
def mock_chroma():
    """Mock ChromaDB client."""
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    client.get_collection.return_value = collection
    return client, collection


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return MagicMock()
