# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Shared test fixtures and dependency stubs for cerid-ai public tests."""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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

    # apscheduler
    _apscheduler = ModuleType("apscheduler")
    _apscheduler_schedulers = ModuleType("apscheduler.schedulers")
    _apscheduler_asyncio = ModuleType("apscheduler.schedulers.asyncio")
    _apscheduler_asyncio.AsyncIOScheduler = MagicMock
    _apscheduler.schedulers = _apscheduler_schedulers
    _apscheduler_schedulers.asyncio = _apscheduler_asyncio
    _ensure_stub("apscheduler", _apscheduler)
    _ensure_stub("apscheduler.schedulers", _apscheduler_schedulers)
    _ensure_stub("apscheduler.schedulers.asyncio", _apscheduler_asyncio)

    # sentry_sdk
    _sentry = ModuleType("sentry_sdk")
    _sentry.init = MagicMock()
    _ensure_stub("sentry_sdk", _sentry)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_chromadb():
    """Mocked ChromaDB client with a default collection."""
    client = MagicMock()
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [["doc-1"]],
        "documents": [["Sample document text"]],
        "metadatas": [[{"domain": "coding", "filename": "test.py"}]],
        "distances": [[0.15]],
    }
    collection.count.return_value = 10
    client.get_or_create_collection.return_value = collection
    client.get_collection.return_value = collection
    return client


@pytest.fixture
def mock_neo4j():
    """Mocked Neo4j driver + session."""
    driver = MagicMock()
    session = MagicMock()
    result = MagicMock()
    result.data.return_value = []
    result.single.return_value = None
    result.consume.return_value = MagicMock()
    session.run.return_value = result
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = session
    return driver, session


@pytest.fixture
def mock_redis():
    """Mocked Redis client."""
    client = MagicMock()
    client.get.return_value = None
    client.set.return_value = True
    client.delete.return_value = 1
    client.exists.return_value = 0
    client.pipeline.return_value = MagicMock(
        __enter__=MagicMock(return_value=MagicMock()),
        __exit__=MagicMock(return_value=False),
    )
    return client


@pytest.fixture
def mock_llm():
    """Mocked LLM call function returning canned responses."""
    async_mock = AsyncMock()
    async_mock.return_value = "This is a canned LLM response."
    return async_mock
