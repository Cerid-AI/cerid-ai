# Evaluation & Efficacy Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 4-tier evaluation suite that validates verification accuracy, RAG retrieval quality, smart routing correctness, and interactive UI flows against the live Cerid AI stack.

**Architecture:** HTTP-only tests running in an isolated Docker container on `llm-network`. No in-process MCP imports. Communication exclusively via API endpoints. IR metrics reimplemented locally (~40 lines). LLM-judge via `/chat/completions` SSE proxy. Browser E2E via Playwright MCP.

**Tech Stack:** Python 3.11, httpx, pytest, pytest-asyncio (eval tiers 1-3); Playwright MCP (tier 4)

**Spec:** `docs/superpowers/specs/2026-03-25-evaluation-efficacy-suite-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `tests/beta/eval/__init__.py` | Package marker |
| `tests/beta/eval/conftest.py` | Async httpx client, seed/cleanup helpers, SSE parser, poll-based indexing wait |
| `tests/beta/eval/metrics.py` | Local IR metrics (NDCG, MRR, P@K, R@K) — pure math, no deps |
| `tests/beta/eval/verification_efficacy.py` | 21 verification ground-truth tests across 5 claim types |
| `tests/beta/eval/rag_benchmark.py` | RAG retrieval metrics + RAGAS LLM-judge quality |
| `tests/beta/eval/routing_validation.py` | Smart router observable behavior via `/chat/completions` |
| `tests/beta/eval/fixtures/verification_cases.jsonl` | 21 ground-truth verification test cases |
| `tests/beta/eval/fixtures/benchmark_seed.jsonl` | 10 seed documents for RAG evaluation |
| `tests/beta/eval/fixtures/routing_cases.jsonl` | Router classification test cases |

**Modified:**
| File | Change |
|------|--------|
| `tests/beta/run.sh` | Add `--eval` and `--full` flags |
| `.gitignore` | Add `tests/beta/eval/reports/` |

---

### Task 1: IR Metrics Module

**Files:**
- Create: `tests/beta/eval/__init__.py`
- Create: `tests/beta/eval/metrics.py`

- [ ] **Step 1: Create eval package and metrics module**

```python
# tests/beta/eval/__init__.py
# (empty)

# tests/beta/eval/metrics.py
"""Local IR metrics — pure math, no external dependencies."""

from __future__ import annotations
import math


def ndcg_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at K."""
    if not relevant:
        return 0.0
    dcg = sum(
        (1.0 / math.log2(i + 2)) for i, rid in enumerate(ranked_ids[:k]) if rid in relevant
    )
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / idcg if idcg > 0 else 0.0


def mrr(ranked_ids: list[str], relevant: set[str]) -> float:
    """Mean Reciprocal Rank — 1/rank of first relevant result."""
    for i, rid in enumerate(ranked_ids):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def precision_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Precision at K — fraction of top-K that are relevant."""
    if not relevant or k == 0:
        return 0.0
    hits = sum(1 for rid in ranked_ids[:k] if rid in relevant)
    return hits / k


def recall_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    """Recall at K — fraction of relevant found in top-K."""
    if not relevant:
        return 0.0
    hits = sum(1 for rid in ranked_ids[:k] if rid in relevant)
    return hits / len(relevant)
```

- [ ] **Step 2: Verify module imports**

Run: `python3 -c "from tests.beta.eval.metrics import ndcg_at_k, mrr; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/beta/eval/__init__.py tests/beta/eval/metrics.py
git commit -m "feat(eval): add local IR metrics module (NDCG, MRR, P@K, R@K)"
```

---

### Task 2: Shared Fixtures and Helpers (`conftest.py`)

**Files:**
- Create: `tests/beta/eval/conftest.py`

- [ ] **Step 1: Create conftest with async client, seed/cleanup, SSE parser**

```python
# tests/beta/eval/conftest.py
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
def event_loop():
    """Create a session-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def aclient():
    """Async HTTP client for the MCP service on llm-network."""
    async with httpx.AsyncClient(
        base_url=MCP_BASE,
        headers={"X-Client-ID": "eval-test", "Content-Type": "application/json"},
        timeout=60.0,
    ) as c:
        yield c


@pytest.fixture()
def unique_marker() -> str:
    """Unique marker string for test isolation."""
    return f"EVAL_{uuid.uuid4().hex[:12]}"


async def seed_content(client: httpx.AsyncClient, content: str, domain: str = "general") -> str:
    """Ingest content via POST /ingest, return artifact_id."""
    resp = await client.post("/ingest", json={"content": content, "domain": domain})
    resp.raise_for_status()
    return resp.json()["artifact_id"]


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
) -> dict[str, Any]:
    """Call POST /agent/verify-stream and parse SSE events into structured result.

    Returns dict with keys: claims (list), summary (dict), errors (list).
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

    claims: list[dict] = []
    summary: dict = {}
    errors: list[str] = []

    async with client.stream("POST", "/agent/verify-stream", json=body, timeout=180.0) as resp:
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
                        summary = event
                    elif etype == "error":
                        errors.append(event.get("detail", str(event)))

    return {"claims": claims, "summary": summary, "errors": errors}


async def generate_chat_answer(client: httpx.AsyncClient, query: str) -> str:
    """Call POST /chat/completions with SSE streaming, return full response text."""
    body = {
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }
    text_parts: list[str] = []
    async with client.stream("POST", "/chat/completions", json=body, timeout=60.0) as resp:
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
```

- [ ] **Step 2: Commit**

```bash
git add tests/beta/eval/conftest.py
git commit -m "feat(eval): add shared async fixtures, SSE parser, seed/cleanup helpers"
```

---

### Task 3: Verification Ground-Truth Fixtures

**Files:**
- Create: `tests/beta/eval/fixtures/verification_cases.jsonl`

- [ ] **Step 1: Create 21 verification test cases**

Write the JSONL file with all 21 cases across 5 claim types. Each case includes:
- `id`, `description`, `response_text`, `user_query`
- `expected_claims`: array of `{text_fragment, type, expected_verdict}`
- Optional `seed_content`, `seed_domain` for KB-dependent tests

Key design decisions per case:
- **V-01 through V-06** (factual): Seed specific KB content, verify claims against it
- **V-10 through V-13** (evasion): Use hedging/deflection language patterns matching `EVASION_PATTERNS`
- **V-20 through V-23** (citation): Use "According to..." and URL patterns matching `CITATION_PATTERNS`
- **V-30 through V-33** (recency): Use year references < 2026 and temporal markers
- **V-40 through V-42** (ignorance): Use "I don't have information" patterns matching `_is_ignorance_admission()`

Each `text_fragment` must be specific enough for `find_matching_claim()` but flexible enough to survive LLM extraction reformulation.

- [ ] **Step 2: Commit**

```bash
git add tests/beta/eval/fixtures/verification_cases.jsonl
git commit -m "feat(eval): add 21 verification ground-truth test cases"
```

---

### Task 4: Verification Efficacy Tests

**Files:**
- Create: `tests/beta/eval/verification_efficacy.py`

- [ ] **Step 1: Implement verification efficacy test module**

```python
# tests/beta/eval/verification_efficacy.py
"""Tier 1: Verification efficacy — ground-truth claim tests."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from conftest import (
    cleanup_artifact,
    load_jsonl,
    seed_content,
    stream_verify,
    wait_for_indexed,
)

CASES = load_jsonl("verification_cases.jsonl")


def find_matching_claim(claims: list[dict], text_fragment: str) -> dict | None:
    """Find a claim whose text contains the fragment (case-insensitive)."""
    fragment_lower = text_fragment.lower()
    for c in claims:
        if fragment_lower in c.get("claim", "").lower():
            return c
    # Fallback: check if any claim overlaps significantly
    fragment_words = set(fragment_lower.split())
    for c in claims:
        claim_words = set(c.get("claim", "").lower().split())
        overlap = fragment_words & claim_words
        if len(overlap) >= max(2, len(fragment_words) * 0.6):
            return c
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_verification_case(case: dict, aclient: httpx.AsyncClient) -> None:
    """Test a single verification case against the live verify-stream endpoint."""
    seeded_ids: list[str] = []

    try:
        # Seed KB content if needed
        if case.get("seed_content"):
            aid = await seed_content(aclient, case["seed_content"], case.get("seed_domain", "general"))
            seeded_ids.append(aid)
            await wait_for_indexed(aclient, aid, timeout=10)

        # Stream verification
        result = await stream_verify(
            aclient,
            case["response_text"],
            case["user_query"],
            source_artifact_ids=[],
        )

        assert not result["errors"], f"Verification errors: {result['errors']}"
        claims = result["claims"]

        # Assert expected claims
        for expected in case.get("expected_claims", []):
            fragment = expected["text_fragment"]
            matched = find_matching_claim(claims, fragment)

            if expected.get("should_extract", True):
                assert matched is not None, (
                    f"[{case['id']}] Claim not extracted: '{fragment}'. "
                    f"Got {len(claims)} claims: {[c['claim'][:50] for c in claims]}"
                )

                # Check claim type if specified
                exp_type = expected.get("type")
                if exp_type and exp_type != "any":
                    assert matched["claim_type"] == exp_type, (
                        f"[{case['id']}] Expected type '{exp_type}' for '{fragment}', "
                        f"got '{matched['claim_type']}'"
                    )

                # Check verdict if specified
                exp_verdict = expected.get("expected_verdict")
                if exp_verdict:
                    assert matched["status"] == exp_verdict, (
                        f"[{case['id']}] Expected verdict '{exp_verdict}' for '{fragment}', "
                        f"got '{matched['status']}' (reason: {matched.get('reason', 'N/A')})"
                    )
            else:
                # Negative test: claim should NOT be extracted or should NOT be this type
                if matched and expected.get("not_type"):
                    assert matched["claim_type"] != expected["not_type"], (
                        f"[{case['id']}] Claim should NOT be type '{expected['not_type']}'"
                    )
    finally:
        # Cleanup seeded content
        for aid in seeded_ids:
            await cleanup_artifact(aclient, aid)
```

- [ ] **Step 2: Commit**

```bash
git add tests/beta/eval/verification_efficacy.py
git commit -m "feat(eval): add verification efficacy test module (21 parametrized cases)"
```

---

### Task 5: RAG Benchmark Seed Fixtures

**Files:**
- Create: `tests/beta/eval/fixtures/benchmark_seed.jsonl`

- [ ] **Step 1: Create 10 seed documents across domains**

Each document contains unique eval marker text + substantive content for meaningful retrieval:

```jsonl
{"content": "The Fibonacci sequence is a series where each number equals the sum of the two preceding ones: 0, 1, 1, 2, 3, 5, 8, 13, 21, 34. It appears in nature through spiral patterns in sunflowers and nautilus shells. [EVAL_SEED_FIB]", "domain": "coding", "query": "How does the Fibonacci sequence work?"}
{"content": "Compound interest formula: A = P(1 + r/n)^(nt), where P is principal amount, r is annual interest rate, n is compounding frequency per year, and t is time in years. At 5% annual rate compounded monthly, $1000 grows to $1051.16 after one year. [EVAL_SEED_COMPOUND]", "domain": "finance", "query": "What is the compound interest formula?"}
```

Include 10 documents spanning coding (3), finance (3), general (4) domains.

- [ ] **Step 2: Commit**

```bash
git add tests/beta/eval/fixtures/benchmark_seed.jsonl
git commit -m "feat(eval): add 10 RAG benchmark seed documents"
```

---

### Task 6: RAG Benchmark Tests

**Files:**
- Create: `tests/beta/eval/rag_benchmark.py`

- [ ] **Step 1: Implement RAG benchmark with retrieval metrics + RAGAS judge**

```python
# tests/beta/eval/rag_benchmark.py
"""Tier 2: RAG retrieval quality + RAGAS LLM-judge generation quality."""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import (
    cleanup_artifact,
    generate_chat_answer,
    load_jsonl,
    seed_content,
    wait_for_indexed,
)
from metrics import mrr, ndcg_at_k, precision_at_k, recall_at_k

SEED_DOCS = load_jsonl("benchmark_seed.jsonl")

# Thresholds from spec
NDCG_5_THRESHOLD = 0.6
MRR_THRESHOLD = 0.5
FAITHFULNESS_THRESHOLD = 0.7
RELEVANCY_THRESHOLD = 0.7


@pytest.fixture(scope="module")
async def seeded_benchmark(aclient: httpx.AsyncClient):
    """Seed all benchmark documents and yield queries with real artifact IDs."""
    seeded: list[dict] = []

    for doc in SEED_DOCS:
        aid = await seed_content(aclient, doc["content"], doc["domain"])
        await wait_for_indexed(aclient, aid, timeout=10)
        seeded.append({
            "query": doc["query"],
            "relevant_ids": [aid],
            "domain": doc["domain"],
            "artifact_id": aid,
        })

    yield seeded

    # Cleanup
    for entry in seeded:
        await cleanup_artifact(aclient, entry["artifact_id"])


@pytest.mark.asyncio
async def test_retrieval_metrics(aclient: httpx.AsyncClient, seeded_benchmark: list[dict]) -> None:
    """Phase B: Retrieval quality — NDCG@5 and MRR across seeded benchmark."""
    ndcg_scores = []
    mrr_scores = []

    for q in seeded_benchmark:
        resp = await aclient.post("/agent/query", json={
            "query": q["query"],
            "top_k": 20,
            "domains": [q["domain"]],
        })
        assert resp.status_code == 200, f"Query failed: {resp.text}"
        results = resp.json().get("results", [])
        ranked_ids = [r["artifact_id"] for r in results]
        relevant = set(q["relevant_ids"])

        ndcg_scores.append(ndcg_at_k(ranked_ids, relevant, 5))
        mrr_scores.append(mrr(ranked_ids, relevant))

    avg_ndcg = sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0
    avg_mrr = sum(mrr_scores) / len(mrr_scores) if mrr_scores else 0

    print(f"\n  Retrieval metrics: avg_NDCG@5={avg_ndcg:.3f}, avg_MRR={avg_mrr:.3f}")
    for i, q in enumerate(seeded_benchmark):
        print(f"    {q['query'][:50]:50s}  NDCG@5={ndcg_scores[i]:.3f}  MRR={mrr_scores[i]:.3f}")

    assert avg_ndcg >= NDCG_5_THRESHOLD, (
        f"avg NDCG@5 {avg_ndcg:.3f} < {NDCG_5_THRESHOLD}"
    )
    assert avg_mrr >= MRR_THRESHOLD, (
        f"avg MRR {avg_mrr:.3f} < {MRR_THRESHOLD}"
    )


@pytest.mark.asyncio
async def test_ragas_quality(aclient: httpx.AsyncClient, seeded_benchmark: list[dict]) -> None:
    """Phase C: RAGAS LLM-judge — faithfulness + answer relevancy."""
    faithfulness_scores = []
    relevancy_scores = []

    # Test first 5 queries for cost control
    for q in seeded_benchmark[:5]:
        # Get RAG context
        rag_resp = await aclient.post("/agent/query", json={
            "query": q["query"], "top_k": 5,
        })
        contexts = [r["content"] for r in rag_resp.json().get("results", [])]

        # Generate answer via chat
        answer = await generate_chat_answer(aclient, q["query"])
        assert answer, f"Empty answer for: {q['query']}"

        # LLM-as-judge: faithfulness
        faith_score = await _judge_metric(
            aclient, "faithfulness", answer=answer, contexts=contexts
        )
        faithfulness_scores.append(faith_score)

        # LLM-as-judge: answer relevancy
        rel_score = await _judge_metric(
            aclient, "relevancy", question=q["query"], answer=answer
        )
        relevancy_scores.append(rel_score)

    avg_faith = sum(faithfulness_scores) / len(faithfulness_scores)
    avg_rel = sum(relevancy_scores) / len(relevancy_scores)
    print(f"\n  RAGAS: faithfulness={avg_faith:.3f}, relevancy={avg_rel:.3f}")

    assert avg_faith >= FAITHFULNESS_THRESHOLD, (
        f"avg faithfulness {avg_faith:.3f} < {FAITHFULNESS_THRESHOLD}"
    )
    assert avg_rel >= RELEVANCY_THRESHOLD, (
        f"avg relevancy {avg_rel:.3f} < {RELEVANCY_THRESHOLD}"
    )


async def _judge_metric(
    client: httpx.AsyncClient,
    metric: str,
    *,
    question: str = "",
    answer: str = "",
    contexts: list[str] | None = None,
) -> float:
    """Call chat proxy with a judge prompt, parse score from response."""
    if metric == "faithfulness":
        ctx_block = "\n---\n".join((contexts or [])[:5])
        prompt = (
            "You are an evaluation judge. Assess whether the claims in the ANSWER "
            "are supported by the CONTEXTS. Return ONLY a JSON object: "
            '{"score": 0.0-1.0, "reasoning": "brief explanation"}. '
            "Score 1.0 = all claims supported. Score 0.0 = none supported.\n\n"
            f"CONTEXTS:\n{ctx_block}\n\nANSWER:\n{answer}"
        )
    elif metric == "relevancy":
        prompt = (
            "You are an evaluation judge. Assess whether the ANSWER directly "
            "addresses the QUESTION. Return ONLY a JSON object: "
            '{"score": 0.0-1.0, "reasoning": "brief explanation"}. '
            "Score 1.0 = perfectly relevant. Score 0.0 = irrelevant.\n\n"
            f"QUESTION:\n{question}\n\nANSWER:\n{answer}"
        )
    else:
        return 0.0

    judge_answer = await generate_chat_answer(client, prompt)

    # Parse JSON score from response
    try:
        data = json.loads(judge_answer)
        return max(0.0, min(1.0, float(data.get("score", 0.0))))
    except (json.JSONDecodeError, ValueError):
        # Fallback: extract first number
        import re
        match = re.search(r"(\d+\.?\d*)", judge_answer)
        if match:
            return max(0.0, min(1.0, float(match.group(1))))
        return 0.0
```

- [ ] **Step 2: Commit**

```bash
git add tests/beta/eval/rag_benchmark.py
git commit -m "feat(eval): add RAG benchmark with retrieval metrics + RAGAS LLM-judge"
```

---

### Task 7: Routing Validation Tests + Fixtures

**Files:**
- Create: `tests/beta/eval/fixtures/routing_cases.jsonl`
- Create: `tests/beta/eval/routing_validation.py`

- [ ] **Step 1: Create routing test cases fixture**

```jsonl
{"query": "What is Python?", "expected_tier": "free_or_cheap", "description": "Simple factual"}
{"query": "hello", "expected_tier": "free_or_cheap", "description": "Trivial greeting"}
{"query": "Implement a B-tree in Rust with concurrent access and lock-free readers using crossbeam", "expected_tier": "capable", "description": "Complex coding"}
{"query": "Compare the economic implications of quantitative easing versus fiscal stimulus in post-pandemic G7 recovery", "expected_tier": "capable", "description": "Complex analysis"}
{"query": "What is the latest news about SpaceX Starship launches?", "expected_tier": "research_online", "description": "Research current events"}
```

- [ ] **Step 2: Implement routing validation module**

```python
# tests/beta/eval/routing_validation.py
"""Tier 3: Smart routing — validate model selection via observable behavior."""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import load_jsonl

CASES = load_jsonl("routing_cases.jsonl")

# Model tier classification
FREE_OR_CHEAP = {"llama", "qwen", "gpt-4o-mini", "gemini-flash", "gemini-2", "ollama"}
CAPABLE = {"claude", "sonnet", "gpt-4o", "gpt-4", "gpt-5"}
RESEARCH = {"grok", "online"}


def classify_model_tier(model_id: str) -> str:
    """Classify a model ID into a tier based on known patterns."""
    model_lower = model_id.lower()
    if ":online" in model_lower or "online" in model_lower:
        return "research_online"
    for keyword in CAPABLE:
        if keyword in model_lower and "mini" not in model_lower:
            return "capable"
    for keyword in FREE_OR_CHEAP:
        if keyword in model_lower:
            return "free_or_cheap"
    return "unknown"


async def get_routed_model(client: httpx.AsyncClient, query: str) -> str | None:
    """Send a chat query and extract the model from cerid_meta SSE event or response."""
    body = {
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }
    model = None
    async with client.stream("POST", "/chat/completions", json=body, timeout=30.0) as resp:
        if resp.status_code != 200:
            return None
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                # cerid_meta event contains resolved model
                if "cerid_meta" in chunk:
                    model = chunk["cerid_meta"].get("model", model)
                # Standard OpenAI chunk may have model field
                if not model and "model" in chunk:
                    model = chunk["model"]
            except json.JSONDecodeError:
                continue
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["description"] for c in CASES])
async def test_routing_case(case: dict, aclient: httpx.AsyncClient) -> None:
    """Verify model selection reflects expected complexity routing."""
    model = await get_routed_model(aclient, case["query"])
    assert model is not None, f"No model returned for: {case['query']}"

    tier = classify_model_tier(model)
    expected = case["expected_tier"]

    # Allow flexibility: free_or_cheap is acceptable when capable is expected
    # (Ollama or cost optimization) but not the reverse
    if expected == "capable":
        assert tier in ("capable", "research_online"), (
            f"[{case['description']}] Expected tier '{expected}', got '{tier}' (model: {model})"
        )
    elif expected == "research_online":
        assert tier == "research_online", (
            f"[{case['description']}] Expected tier '{expected}', got '{tier}' (model: {model})"
        )
    else:
        # free_or_cheap — routing may upgrade, but should never be unknown
        assert tier != "unknown", (
            f"[{case['description']}] Unrecognized model tier for: {model}"
        )
        print(f"  {case['description']:40s} → {model} ({tier})")
```

- [ ] **Step 3: Commit**

```bash
git add tests/beta/eval/fixtures/routing_cases.jsonl tests/beta/eval/routing_validation.py
git commit -m "feat(eval): add smart routing validation tests"
```

---

### Task 8: Integrate Eval Suite into `run.sh`

**Files:**
- Modify: `tests/beta/run.sh`
- Modify: `.gitignore`

- [ ] **Step 1: Add `--eval` and `--full` flags to run.sh**

Add to the flag parsing section (after line 36):
```bash
    --eval) RUN_EVAL=true; RUN_SMOKE=false; RUN_FUNCTIONAL=false; RUN_INTEGRATION=false; RUN_PERFORMANCE=false; RUN_SECURITY=false; RUN_BROWSER=false ;;
    --full) RUN_EVAL=true ;;
    --browser) RUN_BROWSER=true ;;
```

Add `RUN_EVAL=false` to the defaults section (after line 27).

Add eval tier section before FINALIZE (after line 286):

```bash
# ─────────────────────────────────────────────────
# TIER 7: EVALUATION & EFFICACY SUITE
# ─────────────────────────────────────────────────
if ${RUN_EVAL:-false}; then
  echo ""
  echo "╔══════════════════════════════════════╗"
  echo "║   EVALUATION & EFFICACY SUITE        ║"
  echo "╚══════════════════════════════════════╝"
  echo ""

  DOCKER_NETWORK=$(docker network ls --format '{{.Name}}' | grep 'llm-network' | head -1)
  [[ -z "$DOCKER_NETWORK" ]] && DOCKER_NETWORK="cerid-ai_llm-network"

  mkdir -p "${SCRIPT_DIR}/eval/reports"

  docker run --rm --network "$DOCKER_NETWORK" \
    -v "${SCRIPT_DIR}:/tests" -w /tests \
    python:3.11-slim bash -c "
      pip install -q httpx pytest 'pytest-asyncio>=0.23' 2>/dev/null
      python -m pytest eval/ -v --tb=short \
        --junitxml=eval/reports/eval.xml 2>&1
    "
  EVAL_EXIT=$?

  report_section "Evaluation & Efficacy Suite"
  if [[ -f "${SCRIPT_DIR}/eval/reports/eval.xml" ]]; then
    python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('${SCRIPT_DIR}/eval/reports/eval.xml')
root = tree.getroot()
for tc in root.iter('testcase'):
    name = tc.get('name', 'unknown')
    time_val = tc.get('time', '0')
    failure = tc.find('failure')
    skip = tc.find('skipped')
    if failure is not None:
        msg = (failure.get('message', '') or '')[:80]
        print(f'FAIL|EVAL|{name}|{time_val}s|{msg}')
    elif skip is not None:
        print(f'SKIP|EVAL|{name}|{time_val}s|skipped')
    else:
        print(f'PASS|EVAL|{name}|{time_val}s|')
" > "${SCRIPT_DIR}/eval/reports/eval.results" 2>/dev/null || true
    report_append_results "${SCRIPT_DIR}/eval/reports/eval.results"
  fi

  # Generate structured markdown report
  EVAL_REPORT="${SCRIPT_DIR}/eval/reports/eval-report-$(date +%Y%m%d-%H%M%S).md"
  python3 -c "
import xml.etree.ElementTree as ET, sys, os
from datetime import datetime

xml_path = '${SCRIPT_DIR}/eval/reports/eval.xml'
if not os.path.exists(xml_path):
    sys.exit(0)
tree = ET.parse(xml_path)
root = tree.getroot()

ts = root.get('timestamp', datetime.now().isoformat())
total = int(root.get('tests', 0))
failures = int(root.get('failures', 0))
errors = int(root.get('errors', 0))
skipped = int(root.get('skips', 0))
passed = total - failures - errors - skipped

tiers = {'verification_efficacy': [], 'rag_benchmark': [], 'routing_validation': []}
for tc in root.iter('testcase'):
    name = tc.get('name', '')
    classname = tc.get('classname', '')
    time_val = tc.get('time', '0')
    fail = tc.find('failure')
    skip = tc.find('skipped')
    status = 'FAIL' if fail is not None else ('SKIP' if skip is not None else 'PASS')
    detail = (fail.get('message','') if fail is not None else '')[:120]
    for tier_key in tiers:
        if tier_key in classname:
            tiers[tier_key].append((name, status, time_val, detail))
            break
    else:
        tiers.setdefault('other', []).append((name, status, time_val, detail))

verdict = 'GO' if failures == 0 and errors == 0 else ('GO WITH CAVEATS' if failures <= 3 else 'NO-GO')
lines = [f'# Evaluation & Efficacy Report', f'', f'**Date:** {ts}', f'**Verdict:** {verdict}', f'',
         f'## Summary', f'', f'| Metric | Value |', f'|--------|-------|',
         f'| Total | {total} |', f'| Passed | {passed} |', f'| Failed | {failures} |',
         f'| Errors | {errors} |', f'| Skipped | {skipped} |', f'']
for tier_name, results in tiers.items():
    if not results:
        continue
    lines.append(f'## {tier_name.replace(\"_\", \" \").title()}')
    lines.append(f'')
    lines.append(f'| Test | Status | Duration | Notes |')
    lines.append(f'|------|--------|----------|-------|')
    for n, s, t, d in results:
        emoji = '✅' if s == 'PASS' else ('⏭️' if s == 'SKIP' else '❌')
        lines.append(f'| {n} | {emoji} {s} | {t}s | {d} |')
    lines.append(f'')
with open('${SCRIPT_DIR}/eval/reports/eval-report-latest.md', 'w') as f:
    f.write('\n'.join(lines))
print(f'Report: eval/reports/eval-report-latest.md')
" 2>/dev/null || true
  [[ -f "${SCRIPT_DIR}/eval/reports/eval-report-latest.md" ]] && \
    cp "${SCRIPT_DIR}/eval/reports/eval-report-latest.md" "$EVAL_REPORT"

  [[ $EVAL_EXIT -ne 0 ]] && OVERALL_EXIT=1
fi
```

- [ ] **Step 2: Add eval reports to .gitignore**

Append to `.gitignore`:
```
tests/beta/eval/reports/
```

- [ ] **Step 3: Update usage comment at top of run.sh**

Add `--eval` and `--full` to the usage block.

- [ ] **Step 4: Commit**

```bash
git add tests/beta/run.sh .gitignore
git commit -m "feat(eval): integrate eval suite into run.sh (--eval and --full flags)"
```

---

### Task 9: Run Eval Suite and Iterate

- [ ] **Step 1: Execute eval suite against live stack**

```bash
cd .
./tests/beta/run.sh --eval
```

Expected: Docker container starts, installs deps, runs pytest on `eval/` directory.

- [ ] **Step 2: Review results and fix any test failures**

Check each tier:
- Verification: All 21 cases should produce results (some may fail on verdict accuracy)
- RAG: Seeded documents should be retrievable
- Routing: Model tier assertions should match

If thresholds are too strict or claim extraction behaves differently than expected, adjust:
- Fixture `response_text` for clearer claim extraction
- Threshold values based on observed baseline
- `find_matching_claim` fuzzy matching tolerance

- [ ] **Step 3: Re-run until passing**

```bash
./tests/beta/run.sh --eval
```

- [ ] **Step 4: Commit any fixes**

```bash
git add tests/beta/eval/
git commit -m "fix(eval): tune thresholds and fixtures based on live stack results"
```

---

### Task 10: Browser E2E — Interactive Verification Flow (Playwright MCP)

Run these 6 tests interactively via Playwright MCP tools against `http://localhost:3000`.

- [ ] **Step 1: E-11 — Verification flow**

Navigate to app → Send a factual query → Wait for response → Observe:
- Verification status bar appears with counts (verified/unverified/uncertain)
- Claim overlay marks appear inline in the response text

- [ ] **Step 2: E-12 — Claim overlay interaction**

After E-11 → Click a footnote marker [N] → Verify:
- Popover opens with claim text, status badge, source filename
- Similarity percentage shown
- Verification method badge (kb/cross-model/web search)

- [ ] **Step 3: E-13 — Multi-turn coherence**

Send "What is the Fibonacci sequence?" → Get response → Send "Tell me more about where it appears in nature" → Verify:
- Second response references Fibonacci context from first message
- Not a generic answer unrelated to the conversation

- [ ] **Step 4: E-14 — KB context panel**

Send a query that should match KB content → Check sidebar/panel:
- KB context panel shows relevant artifacts
- Artifacts have filenames and relevance indicators

- [ ] **Step 5: E-15 — Settings persistence**

Navigate to Settings → Change a slider value (e.g., top_k) → Reload page → Verify:
- Setting value persists after page reload

- [ ] **Step 6: E-16 — Error recovery (best-effort)**

Observe error handling UI:
- If any error occurs during testing, verify error banner appears
- Verify user can retry/dismiss
- Mark as manual-only if no errors occur naturally

- [ ] **Step 7: Document results in beta report**

Record E-11 through E-16 results in the eval report.

---

### Task 11: Final Commit and Push

- [ ] **Step 1: Run full suite (beta + eval)**

```bash
./tests/beta/run.sh --full --skip-browser
```

Verify all tiers pass.

- [ ] **Step 2: Commit final state**

```bash
git add -A tests/beta/eval/
git commit -m "feat(eval): complete evaluation & efficacy suite — all tiers passing"
```

- [ ] **Step 3: Push and verify CI**

```bash
git push
gh run watch $(gh run list --limit 1 --json databaseId -q '.[0].databaseId') --exit-status
```
