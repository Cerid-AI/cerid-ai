"""Shared pytest fixtures for Cerid AI beta functional tests."""

import uuid

import httpx
import pytest

MCP_BASE_URL = "http://ai-companion-mcp:8888"


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """HTTP client pre-configured for the MCP service on the llm-network."""
    with httpx.Client(
        base_url=MCP_BASE_URL,
        headers={
            "X-Client-ID": "beta-test",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    ) as c:
        yield c


@pytest.fixture()
def unique_id() -> str:
    """Return a unique string for test isolation."""
    return uuid.uuid4().hex[:12]


@pytest.fixture()
def test_artifact_id(client: httpx.Client, unique_id: str) -> str:
    """Ingest a throwaway text document and yield its artifact_id."""
    resp = client.post(
        "/ingest_content",
        json={
            "content": f"Beta fixture content {unique_id}",
            "title": f"Beta fixture {unique_id}",
            "domain": "general",
            "sub_category": "general",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["artifact_id"]
