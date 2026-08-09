"""Shared pytest fixtures for Cerid AI beta functional tests."""

import os
import uuid

import httpx
import pytest

MCP_BASE_URL = os.getenv("BETA_MCP_BASE", "http://ai-companion-mcp:8888")


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """HTTP client pre-configured for the MCP service on the llm-network."""
    headers: dict = {
        "X-Client-ID": "beta-test",
        "Content-Type": "application/json",
    }
    api_key = os.getenv("CERID_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    with httpx.Client(
        base_url=MCP_BASE_URL,
        headers=headers,
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
