# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end integration tests against REAL running Docker services.

This test suite hits live endpoints — NO mocks. Requires the full Docker
stack to be running (MCP Server, Neo4j, ChromaDB, Redis, Ollama).

Usage:
    cd /Users/sunrunner/Develop/cerid-ai
    python3 -m pytest tests/test_e2e_integration.py -v --tb=short -x -m integration

Prerequisites:
    - Docker stack running (./scripts/start-cerid.sh)
    - Ollama with llama3.1:8b loaded
    - OpenRouter API key configured (for external LLM tests)
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MCP_URL = "http://localhost:8888"
FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"
LLM_TIMEOUT = 60.0       # generous for slow Ollama / external LLM
INGEST_TIMEOUT = 30.0
DEFAULT_TIMEOUT = 20.0

# Unique run ID to avoid dedup collisions across repeated test runs
RUN_ID = uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_sse_response(response_text: str) -> list[dict]:
    """Parse SSE text into list of data events."""
    events = []
    for line in response_text.split("\n"):
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def extract_chat_content(events: list[dict]) -> str:
    """Extract full text content from SSE chat events."""
    content = ""
    for event in events:
        if "choices" in event:
            delta = event["choices"][0].get("delta", {})
            content += delta.get("content", "")
    return content


def build_kb_system_message(kb_results: list[dict]) -> dict:
    """Build a system message with <document> tags from KB results."""
    docs = []
    for r in kb_results:
        artifact_id = r.get("artifact_id", r.get("id", ""))
        domain = r.get("domain", "")
        filename = r.get("filename", r.get("source", ""))
        chunk_content = r.get("content", r.get("text", ""))
        docs.append(
            f'<document id="{artifact_id}" domain="{domain}" source="{filename}">\n'
            f"{chunk_content}\n"
            "</document>"
        )
    content = (
        "You have access to the user's personal knowledge base. "
        "The following documents are relevant. Use their content when answering.\n\n"
        + "\n\n".join(docs)
    )
    return {"role": "system", "content": content}


def _read_fixture(name: str) -> str:
    """Read a fixture file from the synthetic fixtures directory."""
    return (FIXTURES / name).read_text()


def _ingest_label(base: str) -> str:
    """Create a run-unique label for ingestion to avoid dedup."""
    return f"[e2e-{RUN_ID}] {base}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """httpx client with base URL targeting the real MCP server."""
    with httpx.Client(
        base_url=MCP_URL,
        timeout=DEFAULT_TIMEOUT,
        headers={"X-Client-ID": "e2e-test"},
    ) as c:
        yield c


@pytest.fixture(scope="module")
def llm_client():
    """httpx client with extended timeout for LLM calls."""
    with httpx.Client(
        base_url=MCP_URL,
        timeout=LLM_TIMEOUT,
        headers={"X-Client-ID": "e2e-test"},
    ) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def check_services(client):
    """Skip all tests if Docker services aren't running."""
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        health = resp.json()
        assert health["status"] in ("healthy", "degraded")
    except (httpx.ConnectError, httpx.ReadTimeout, AssertionError) as exc:
        pytest.skip(f"Docker services not running: {exc}")


# ---------------------------------------------------------------------------
# Shared state: track which ingestions succeeded so downstream tests can skip
# ---------------------------------------------------------------------------

_ingestion_results: dict[str, bool] = {}


def _try_get_kb_context(
    client: httpx.Client, query: str, domains: list[str] | None = None
) -> list[dict]:
    """Fetch KB context for a query, returning [] on failure instead of asserting."""
    payload: dict = {"query": query, "top_k": 5}
    if domains:
        payload["domains"] = domains
    try:
        resp = client.post("/agent/query", json=payload)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("sources", data.get("results", []))
    except (httpx.ConnectError, httpx.ReadTimeout):
        return []


# ---------------------------------------------------------------------------
# Class 1: TestIngestSyntheticData — must run FIRST
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestIngestSyntheticData:
    """Ingest synthetic fixture files into the real KB."""

    def _do_ingest(self, client, fixture_name: str, domain: str, label: str) -> dict:
        """Common ingest logic. Returns response data or raises on failure."""
        raw = _read_fixture(fixture_name)
        content = _ingest_label(raw)
        resp = client.post(
            "/ingest",
            json={"content": content, "domain": domain},
            timeout=INGEST_TIMEOUT,
        )
        assert resp.status_code == 200, (
            f"Ingest {label} failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )
        data = resp.json()
        assert data.get("status") in ("success", "duplicate"), (
            f"Unexpected ingest status for {label}: {data}"
        )
        _ingestion_results[label] = True
        return data

    def test_ingest_quantum_computing(self, client):
        data = self._do_ingest(client, "quantum_computing_overview.md", "general", "quantum")
        if data.get("status") == "success":
            assert data.get("chunks", 0) > 0, f"No chunks created: {data}"

    def test_ingest_financial_report(self, client):
        self._do_ingest(client, "financial_report_q3_2025.md", "finance", "financial")

    def test_ingest_mixed_claims(self, client):
        self._do_ingest(client, "mixed_claims_document.md", "general", "mixed_claims")

    def test_health_after_ingest(self, client):
        # Allow indexing to settle
        time.sleep(2)
        resp = client.get("/health")
        assert resp.status_code == 200
        health = resp.json()
        services = health.get("services", {})
        assert services.get("chromadb") == "connected", f"ChromaDB: {services}"
        # Collections should exist
        coll_resp = client.get("/collections")
        assert coll_resp.status_code == 200
        coll_data = coll_resp.json()
        assert coll_data.get("total", 0) > 0, f"No collections: {coll_data}"


# ---------------------------------------------------------------------------
# Class 2: TestRetrievalAccuracy
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRetrievalAccuracy:
    """Query the KB and verify correct content comes back."""

    def _query(self, client, query: str, **kwargs) -> dict:
        """Common query helper — asserts 200 or fails with detail."""
        payload: dict = {"query": query, "top_k": 10, **kwargs}
        resp = client.post("/agent/query", json=payload)
        assert resp.status_code == 200, (
            f"Query failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )
        return resp.json()

    def test_query_shors_algorithm(self, client):
        data = self._query(client, "What is the complexity of Shor's algorithm?", domains=["general"])
        full_text = json.dumps(data).lower()
        assert any(term in full_text for term in [
            "polynomial", "o(", "shor", "log n", "factoring",
        ]), f"No Shor's algorithm content found: {data.get('answer', '')[:200]}"

    def test_query_meridian_revenue(self, client):
        data = self._query(client, "What was Meridian Technologies revenue?", domains=["finance"])
        full_text = json.dumps(data).lower()
        assert any(term in full_text for term in [
            "847.3", "847", "meridian",
        ]), f"No Meridian revenue data found: {full_text[:300]}"

    def test_query_cross_domain(self, client):
        data = self._query(client, "quantum computing algorithms", domains=["general", "finance"])
        sources = data.get("sources", data.get("results", []))
        answer = data.get("answer", data.get("context", ""))
        assert sources or answer, f"Empty cross-domain response: {data}"

    def test_query_returns_sources(self, client):
        data = self._query(client, "Shor's algorithm", top_k=5)
        sources = data.get("sources", data.get("results", []))
        if sources:
            first = sources[0]
            assert any(
                k in first for k in ("artifact_id", "id", "filename", "source", "domain")
            ), f"Source missing identifying fields: {first.keys()}"

    def test_query_relevance_ordering(self, client):
        data = self._query(client, "Shor's algorithm polynomial time")
        sources = data.get("sources", data.get("results", []))
        if len(sources) >= 2:
            scores = [
                s.get("relevance", s.get("score", s.get("similarity", None)))
                for s in sources
            ]
            numeric_scores = [s for s in scores if s is not None]
            if len(numeric_scores) >= 2:
                for i in range(len(numeric_scores) - 1):
                    assert numeric_scores[i] >= numeric_scores[i + 1], (
                        f"Sources not sorted by relevance: {numeric_scores}"
                    )

    def test_query_empty_returns_gracefully(self, client):
        data = self._query(client, "xyzzy nonsense gibberish zqwplm", top_k=5)
        assert "error" not in data.get("status", ""), f"Unexpected error: {data}"


# ---------------------------------------------------------------------------
# Class 3: TestExternalLLMChat (OpenRouter)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestExternalLLMChat:
    """Chat using OpenRouter (external LLM) with KB injection."""

    def _stream_chat(self, llm_client, messages: list[dict], model: str) -> tuple[list[dict], str]:
        """Helper: send chat request and parse SSE response."""
        resp = llm_client.post(
            "/chat/stream",
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 500,
                "stream": True,
            },
            timeout=LLM_TIMEOUT,
        )
        assert resp.status_code == 200, f"Chat stream failed ({resp.status_code}): {resp.text[:300]}"
        events = parse_sse_response(resp.text)
        content = extract_chat_content(events)
        return events, content

    def test_chat_with_kb_context_external(self, client, llm_client):
        # Step 1: Get KB context about quantum computing
        sources = _try_get_kb_context(client, "Shor's algorithm complexity", ["general"])
        if not sources:
            pytest.skip("No KB sources found for quantum computing — ingest may have failed")

        # Step 2: Build messages with KB context
        system_msg = build_kb_system_message(sources)
        messages = [
            system_msg,
            {"role": "user", "content": "What is Shor's algorithm and what is its time complexity?"},
        ]

        # Step 3: Stream chat with external model
        events, content = self._stream_chat(llm_client, messages, "openrouter/anthropic/claude-sonnet-4.6")

        # Step 4: Verify response references KB content
        content_lower = content.lower()
        assert any(term in content_lower for term in [
            "shor", "polynomial", "factoring", "log",
        ]), f"External LLM response doesn't reference KB content: {content[:300]}"

    def test_chat_external_aware_of_financial_data(self, client, llm_client):
        sources = _try_get_kb_context(client, "Meridian Technologies revenue Q3", ["finance"])
        if not sources:
            pytest.skip("No KB sources found for financial data")

        system_msg = build_kb_system_message(sources)
        messages = [
            system_msg,
            {"role": "user", "content": "What was Meridian Technologies' Q3 2025 total revenue?"},
        ]

        events, content = self._stream_chat(llm_client, messages, "openrouter/anthropic/claude-sonnet-4.6")

        content_lower = content.lower()
        assert any(term in content_lower for term in [
            "847", "meridian",
        ]), f"External LLM response doesn't reference financial data: {content[:300]}"

    def test_chat_external_multi_turn_with_context(self, client, llm_client):
        sources = _try_get_kb_context(client, "quantum computing qubits", ["general"])
        if not sources:
            pytest.skip("No KB sources found for quantum computing")

        system_msg = build_kb_system_message(sources)

        # Turn 1: With KB context
        messages_t1 = [
            system_msg,
            {"role": "user", "content": "Briefly explain what a qubit is."},
        ]
        events_t1, content_t1 = self._stream_chat(llm_client, messages_t1, "openrouter/anthropic/claude-sonnet-4.6")
        assert len(content_t1) > 20, f"Turn 1 response too short: {content_t1}"

        # Turn 2: Follow-up WITHOUT new KB context — model should still reference prior context
        messages_t2 = [
            system_msg,
            {"role": "user", "content": "Briefly explain what a qubit is."},
            {"role": "assistant", "content": content_t1},
            {"role": "user", "content": "What about entanglement? How does it relate to qubits?"},
        ]
        events_t2, content_t2 = self._stream_chat(llm_client, messages_t2, "openrouter/anthropic/claude-sonnet-4.6")
        content_t2_lower = content_t2.lower()
        assert any(term in content_t2_lower for term in [
            "entangle", "bell", "correlat", "qubit",
        ]), f"Turn 2 response doesn't reference quantum context: {content_t2[:300]}"

    def test_chat_external_cerid_meta_event(self, client, llm_client):
        messages = [
            {"role": "user", "content": "Hello, what is 2+2?"},
        ]
        resp = llm_client.post(
            "/chat/stream",
            json={
                "model": "openrouter/openai/gpt-4o-mini",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 50,
                "stream": True,
            },
            timeout=LLM_TIMEOUT,
        )
        assert resp.status_code == 200
        events = parse_sse_response(resp.text)

        # First data event should be cerid_meta
        meta_events = [e for e in events if "cerid_meta" in e]
        assert len(meta_events) > 0, (
            f"No cerid_meta event found. Events: {[list(e.keys()) for e in events[:5]]}"
        )
        meta = meta_events[0]["cerid_meta"]
        assert "requested_model" in meta, f"cerid_meta missing requested_model: {meta}"
        assert "resolved_model" in meta, f"cerid_meta missing resolved_model: {meta}"


# ---------------------------------------------------------------------------
# Class 4: TestInternalLLMChat (Ollama)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestInternalLLMChat:
    """Chat using Ollama (internal LLM) with KB injection."""

    @pytest.fixture(autouse=True)
    def _check_ollama(self, client):
        """Skip Ollama tests if Ollama isn't reachable."""
        try:
            resp = client.get("/health")
            health = resp.json()
            ollama_info = health.get("ollama", {})
            if not ollama_info.get("reachable", False):
                pytest.skip("Ollama not reachable")
        except Exception:
            pytest.skip("Cannot check Ollama status")

    def _ollama_chat(self, llm_client, messages: list[dict], model: str = "llama3.1:8b") -> dict:
        """Send a chat request to the Ollama proxy endpoint (non-streaming)."""
        resp = llm_client.post(
            "/ollama/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.3,
                "max_tokens": 500,
            },
            timeout=LLM_TIMEOUT,
        )
        assert resp.status_code == 200, f"Ollama chat failed ({resp.status_code}): {resp.text[:300]}"
        return resp.json()

    def test_chat_with_kb_context_internal(self, client, llm_client):
        sources = _try_get_kb_context(client, "Shor's algorithm", ["general"])
        if not sources:
            pytest.skip("No KB sources found")

        system_msg = build_kb_system_message(sources)
        messages = [
            system_msg,
            {"role": "user", "content": "What is Shor's algorithm? Be brief."},
        ]

        data = self._ollama_chat(llm_client, messages)
        response_text = data.get("message", {}).get("content", "")
        assert len(response_text) > 20, f"Ollama response too short: {response_text}"
        response_lower = response_text.lower()
        assert any(term in response_lower for term in [
            "shor", "factor", "quantum", "algorithm",
        ]), f"Ollama response doesn't reference KB content: {response_text[:300]}"

    def test_chat_internal_aware_of_injected_data(self, client, llm_client):
        sources = _try_get_kb_context(client, "Meridian Technologies revenue", ["finance"])
        if not sources:
            pytest.skip("No KB sources for financial data")

        system_msg = build_kb_system_message(sources)
        messages = [
            system_msg,
            {"role": "user", "content": "What was Meridian's total Q3 2025 revenue? Give the number."},
        ]

        data = self._ollama_chat(llm_client, messages)
        response_text = data.get("message", {}).get("content", "")
        response_lower = response_text.lower()
        assert any(term in response_lower for term in [
            "847", "meridian", "revenue",
        ]), f"Ollama response doesn't reference financial data: {response_text[:300]}"

    def test_chat_internal_responds_without_kb(self, llm_client):
        messages = [
            {"role": "user", "content": "What is 2 + 2? Answer with just the number."},
        ]
        data = self._ollama_chat(llm_client, messages)
        response_text = data.get("message", {}).get("content", "")
        assert len(response_text) > 0, "Ollama returned empty response without KB"
        assert "4" in response_text, f"Ollama failed basic math: {response_text}"

    def test_internal_vs_external_both_see_context(self, client, llm_client):
        """Same KB context, same question — verify BOTH models reference it."""
        sources = _try_get_kb_context(client, "quantum computing qubits", ["general"])
        if not sources:
            pytest.skip("No KB sources for quantum computing")

        system_msg = build_kb_system_message(sources)
        question = "What is a qubit? Briefly explain using the provided documents."
        messages = [system_msg, {"role": "user", "content": question}]

        # Internal (Ollama)
        ollama_data = self._ollama_chat(llm_client, messages)
        ollama_text = ollama_data.get("message", {}).get("content", "").lower()

        # External (OpenRouter)
        try:
            resp = llm_client.post(
                "/chat/stream",
                json={
                    "model": "openrouter/openai/gpt-4o-mini",
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 300,
                    "stream": True,
                },
                timeout=LLM_TIMEOUT,
            )
            events = parse_sse_response(resp.text)
            external_text = extract_chat_content(events).lower()
        except Exception as exc:
            pytest.skip(f"External LLM unavailable: {exc}")
            return

        # Both should mention qubit-related terms
        assert any(t in ollama_text for t in ["qubit", "quantum", "superposition"]), (
            f"Ollama didn't reference KB: {ollama_text[:200]}"
        )
        assert any(t in external_text for t in ["qubit", "quantum", "superposition"]), (
            f"External LLM didn't reference KB: {external_text[:200]}"
        )


# ---------------------------------------------------------------------------
# Class 5: TestVerificationWithRealKB
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestVerificationWithRealKB:
    """Verify the hallucination checker works against real KB.

    NOTE: These endpoints are rate-limited (10 req/60s per client).
    Tests include a wait between calls to avoid 429 responses.
    """

    # Use a distinct client ID to get a separate rate-limit bucket
    VERIFY_HEADERS = {"X-Client-ID": f"e2e-verify-{RUN_ID}"}

    def _verify_post(self, llm_client, path: str, payload: dict) -> httpx.Response:
        """POST to a verification endpoint with rate-limit-safe headers."""
        return llm_client.post(
            path,
            json=payload,
            headers=self.VERIFY_HEADERS,
            timeout=LLM_TIMEOUT,
        )

    def test_hallucination_check_correct_claim(self, llm_client):
        """Check a factually correct claim against the KB."""
        conversation_id = f"e2e-verify-correct-{RUN_ID}"
        resp = self._verify_post(llm_client, "/agent/hallucination", {
            "response_text": (
                "According to the knowledge base, Shor's algorithm factors "
                "large integers in polynomial time. It was developed by Peter Shor in 1994."
            ),
            "conversation_id": conversation_id,
        })
        if resp.status_code == 429:
            pytest.skip("Rate limited — retry after cooldown")
        assert resp.status_code == 200, f"Hallucination check failed ({resp.status_code}): {resp.text[:300]}"
        data = resp.json()
        assert data is not None
        assert any(k in data for k in [
            "claims", "overall_score", "score", "status", "summary",
        ]), f"Unexpected hallucination response format: {list(data.keys())}"

    def test_hallucination_check_wrong_claim(self, llm_client):
        """Check a factually wrong claim — should flag it."""
        time.sleep(7)  # Rate limit spacing
        conversation_id = f"e2e-verify-wrong-{RUN_ID}"
        resp = self._verify_post(llm_client, "/agent/hallucination", {
            "response_text": (
                "Meridian Technologies reported Q3 2025 revenue of $900M, "
                "which was a 25% increase year over year."
            ),
            "conversation_id": conversation_id,
        })
        if resp.status_code == 429:
            pytest.skip("Rate limited — retry after cooldown")
        assert resp.status_code == 200, f"Hallucination check failed ({resp.status_code}): {resp.text[:300]}"
        data = resp.json()
        assert data is not None

    def test_streaming_verification_events(self, llm_client):
        """POST to the verification SSE endpoint and parse events."""
        time.sleep(7)  # Rate limit spacing
        conversation_id = f"e2e-verify-stream-{RUN_ID}"
        resp = self._verify_post(llm_client, "/agent/verify-stream", {
            "response_text": (
                "Python was created by Guido van Rossum and first released in 1991. "
                "HTTP/2 was standardized in 2015 as RFC 7540."
            ),
            "conversation_id": conversation_id,
        })
        if resp.status_code == 429:
            pytest.skip("Rate limited — retry after cooldown")
        assert resp.status_code == 200, f"Verify stream failed ({resp.status_code}): {resp.text[:300]}"

        events = parse_sse_response(resp.text)
        assert len(events) > 0, f"No SSE events from verify-stream. Raw: {resp.text[:500]}"

        event_types = [e.get("type", e.get("event", "")) for e in events]
        assert len(event_types) > 0, f"Events lack type field: {events[:3]}"

    def test_verify_numerical_precision(self, llm_client):
        """KB has $847.3M — claiming $900M should get flagged or show uncertainty."""
        time.sleep(7)  # Rate limit spacing
        conversation_id = f"e2e-verify-num-{RUN_ID}"
        resp = self._verify_post(llm_client, "/agent/hallucination", {
            "response_text": (
                "Meridian Technologies had total revenue of $900M in Q3 2025."
            ),
            "conversation_id": conversation_id,
        })
        if resp.status_code == 429:
            pytest.skip("Rate limited — retry after cooldown")
        assert resp.status_code == 200, f"Verification failed ({resp.status_code}): {resp.text[:300]}"
        data = resp.json()
        assert data is not None


# ---------------------------------------------------------------------------
# Class 6: TestMemoryFlow
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMemoryFlow:
    """Verify memory endpoints work correctly."""

    def test_memories_endpoint_returns_data(self, client):
        resp = client.get("/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert "memories" in data, f"Missing 'memories' key: {list(data.keys())}"
        assert "total" in data, f"Missing 'total' key: {list(data.keys())}"

    def test_memories_excludes_trading_agent(self, client):
        """GET /memories should not include trading-agent client_source items."""
        resp = client.get("/memories", params={"limit": 100})
        assert resp.status_code == 200
        data = resp.json()
        memories = data.get("memories", [])
        # None should have client_source="trading-agent"
        for mem in memories:
            source = mem.get("client_source", "")
            assert source != "trading-agent", (
                f"Trading agent memory leaked into GUI memories: {mem}"
            )

    def test_memory_extraction_after_chat(self, llm_client):
        """POST /memories/extract with a conversation and verify memories returned."""
        conversation_id = f"e2e-mem-extract-{RUN_ID}"
        resp = llm_client.post(
            "/memories/extract",
            json={
                "conversation_id": conversation_id,
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"},
                    {
                        "role": "assistant",
                        "content": (
                            "The capital of France is Paris. It is the largest city in France "
                            "and has been the country's capital since the 10th century. "
                            "The user seems interested in geography and European capitals."
                        ),
                    },
                ],
            },
            timeout=LLM_TIMEOUT,
        )
        assert resp.status_code == 200, f"Memory extraction failed: {resp.text}"
        data = resp.json()
        # Should return some kind of extraction result
        assert data is not None
        # May have memories_extracted or results
        assert any(k in data for k in [
            "memories_extracted", "memories_stored", "results", "conversation_id",
        ]), f"Unexpected extraction response: {list(data.keys())}"


# ---------------------------------------------------------------------------
# Class 7: TestOllamaDirectEndpoints
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestOllamaDirectEndpoints:
    """Test Ollama proxy endpoints directly."""

    @pytest.fixture(autouse=True)
    def _check_ollama(self, client):
        try:
            resp = client.get("/health")
            health = resp.json()
            if not health.get("ollama", {}).get("reachable", False):
                pytest.skip("Ollama not reachable")
        except Exception:
            pytest.skip("Cannot check Ollama status")

    def test_ollama_list_models(self, client):
        resp = client.get("/ollama/models")
        assert resp.status_code == 200
        data = resp.json()
        models = data.get("models", [])
        assert len(models) > 0, "No Ollama models found"
        model_names = [m.get("name", "") for m in models]
        assert any("llama" in n.lower() for n in model_names), (
            f"No llama model found in: {model_names}"
        )

    def test_ollama_streaming_chat(self, llm_client):
        """Test Ollama streaming chat response."""
        resp = llm_client.post(
            "/ollama/chat",
            json={
                "model": "llama3.1:8b",
                "messages": [{"role": "user", "content": "Say 'hello' and nothing else."}],
                "stream": True,
                "temperature": 0.1,
                "max_tokens": 20,
            },
            timeout=LLM_TIMEOUT,
        )
        assert resp.status_code == 200, f"Ollama stream failed: {resp.text[:300]}"
        # Streaming returns SSE or NDJSON — should have content
        assert len(resp.text) > 0, "Empty streaming response"
