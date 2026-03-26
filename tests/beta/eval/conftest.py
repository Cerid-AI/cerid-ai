# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for evaluation suite — async httpx, seed/cleanup, SSE parsing."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

MCP_BASE = "http://ai-companion-mcp:8888"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
async def aclient():
    """Async HTTP client for the MCP service on llm-network."""
    async with httpx.AsyncClient(
        base_url=MCP_BASE,
        headers={"X-Client-ID": "gui", "Content-Type": "application/json"},
        timeout=60.0,
    ) as c:
        yield c


@pytest.fixture()
def unique_marker() -> str:
    """Unique marker string for test isolation."""
    return f"EVAL_{uuid.uuid4().hex[:12]}"


async def seed_content(client: httpx.AsyncClient, content: str, domain: str = "general") -> str:
    """Ingest content via POST /ingest, return artifact_id. Retries on rate limit."""
    for attempt in range(5):
        resp = await client.post("/ingest", json={"content": content, "domain": domain})
        if resp.status_code == 429:
            await asyncio.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()["artifact_id"]
    resp.raise_for_status()  # Final failure
    return ""  # unreachable


async def cleanup_artifact(client: httpx.AsyncClient, artifact_id: str) -> None:
    """Delete artifact via DELETE /admin/artifacts/{id}."""
    try:
        await client.delete(f"/admin/artifacts/{artifact_id}")
    except httpx.HTTPError:
        pass  # Best-effort cleanup


async def wait_for_indexed(client: httpx.AsyncClient, artifact_id: str, timeout: float = 10) -> None:
    """Poll until artifact is retrievable via GET /artifacts/{id}."""
    deadline = time.time() + timeout
    delay = 0.5
    while time.time() < deadline:
        resp = await client.get(f"/artifacts/{artifact_id}")
        if resp.status_code == 200:
            return
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 3.0)
    raise TimeoutError(f"Artifact {artifact_id} not indexed within {timeout}s")


async def stream_verify(
    client: httpx.AsyncClient,
    response_text: str,
    user_query: str,
    expert_mode: bool = False,
    source_artifact_ids: list[str] | None = None,
    generating_model: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Call POST /agent/verify-stream and parse SSE events into structured result.

    Returns dict with keys: claims (list), summary (dict), errors (list).

    Args:
        generating_model: Model that produced response_text. Enables cross-model
            diversity selection — the verifier picks a different model family.
        conversation_history: Prior conversation turns for consistency checking.
    """
    body: dict[str, Any] = {
        "response_text": response_text,
        "conversation_id": f"eval-{uuid.uuid4().hex[:8]}",
        "user_query": user_query,
    }
    if expert_mode:
        body["expert_mode"] = True
    if source_artifact_ids:
        body["source_artifact_ids"] = source_artifact_ids
    if generating_model:
        body["model"] = generating_model
    if conversation_history:
        body["conversation_history"] = conversation_history

    claims: list[dict] = []
    summary: dict = {}
    errors: list[str] = []

    return await _stream_verify_with_retry(client, body, claims, summary, errors)


async def _stream_verify_with_retry(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    claims: list[dict],
    summary: dict,
    errors: list[str],
    max_retries: int = 5,
) -> dict[str, Any]:
    """Execute verify-stream with retry on 429 rate limit."""
    for attempt in range(max_retries):
        async with client.stream("POST", "/agent/verify-stream", json=body, timeout=180.0) as resp:
            if resp.status_code == 429 and attempt < max_retries - 1:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("event", event.get("type", ""))
                        if etype == "claim_extracted":
                            claims.append({
                                "index": event.get("index"),
                                "claim": event.get("claim", ""),
                                "claim_type": event.get("claim_type", ""),
                                "status": "pending",
                            })
                        elif etype == "claim_verified":
                            idx = event.get("index")
                            for c in claims:
                                if c["index"] == idx:
                                    c["status"] = event.get("status", "")
                                    c["confidence"] = event.get("confidence", 0)
                                    c["source"] = event.get("source", "")
                                    c["verification_method"] = event.get("verification_method", "")
                                    c["reason"] = event.get("reason", "")
                                    break
                        elif etype == "summary":
                            summary.update(event)
                        elif etype == "error":
                            errors.append(event.get("detail", str(event)))
            # Success — don't retry
            return {"claims": claims, "summary": summary, "errors": errors}

    # Should not reach here, but just in case
    return {"claims": claims, "summary": summary, "errors": errors}


async def generate_chat_answer(client: httpx.AsyncClient, query: str, model: str = "auto") -> str:
    """Call POST /chat/stream with SSE streaming, return full response text."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }
    text_parts: list[str] = []
    async with client.stream("POST", "/chat/stream", json=body, timeout=60.0) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    text_parts.append(content)
            except (json.JSONDecodeError, IndexError, KeyError):
                continue
    return "".join(text_parts)


def load_jsonl(filename: str) -> list[dict]:
    """Load a JSONL fixture file."""
    path = FIXTURES_DIR / filename
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
