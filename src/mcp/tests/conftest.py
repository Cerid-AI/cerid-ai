"""Shared test fixtures for cerid-ai tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add src/mcp to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))


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
