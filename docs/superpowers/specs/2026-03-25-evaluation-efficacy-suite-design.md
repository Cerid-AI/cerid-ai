# Evaluation & Efficacy Suite — Design Spec

**Date:** 2026-03-25
**Status:** Approved
**Approach:** B (full efficacy, framework extensible to C)

---

## Goal

Provide high confidence that every user-facing intelligence system works correctly before beta deployment. Test not just "does it respond" (already covered by the beta harness) but "does it respond *correctly*."

## Success Criteria

| System | Metric | Threshold |
|--------|--------|-----------|
| Verification | Claim extraction recall | ≥80% of ground-truth claims extracted |
| Verification | Verdict accuracy | ≥70% correct verdicts on ground-truth claims |
| Verification | All 4 claim types fire | Each type exercised at least twice |
| RAG retrieval | NDCG@5 on seeded benchmark | ≥0.6 |
| RAG retrieval | MRR on seeded benchmark | ≥0.5 |
| RAGAS quality | Faithfulness (LLM judge) | ≥0.7 avg across queries |
| RAGAS quality | Answer relevancy (LLM judge) | ≥0.7 avg across queries |
| Smart routing | Model selection correctness | 100% on deterministic test cases |
| Browser E2E | Interactive verification flow | All steps complete without error |
| Browser E2E | Multi-turn chat coherence | Context maintained across turns |

## Architecture

All eval tiers (1–3) are **HTTP-only tests** running in an isolated Docker container on `llm-network`. They communicate with the MCP server exclusively via HTTP endpoints — no in-process imports. This matches the beta harness pattern and avoids needing the full MCP dependency tree.

```
tests/beta/
├── eval/                          # NEW — evaluation suite
│   ├── conftest.py                # Shared fixtures (httpx client, seed/cleanup helpers)
│   ├── verification_efficacy.py   # Tier 1: Verification ground-truth tests
│   ├── rag_benchmark.py           # Tier 2: RAG retrieval + RAGAS quality
│   ├── routing_validation.py      # Tier 3: Smart router deterministic tests
│   ├── fixtures/
│   │   ├── verification_cases.jsonl   # Ground-truth claims + expected verdicts
│   │   ├── benchmark_seed.jsonl       # 10 seed documents for RAG eval
│   │   └── routing_cases.jsonl        # Query → expected complexity mapping
│   └── reports/                   # Generated eval reports (.gitignored)
├── run.sh                         # Extended: --eval flag triggers eval suite
└── ...existing beta files...
```

Browser E2E tests (Tier 4) run via Playwright MCP interactively, same as the beta harness pattern.

### Key API Endpoints Used

| Endpoint | Purpose | Router |
|----------|---------|--------|
| `POST /ingest` | Seed KB content (`IngestRequest`: `content`, `domain`) | `routers/ingestion.py` |
| `DELETE /admin/artifacts/{id}` | Cleanup seeded content (Neo4j + ChromaDB) | `routers/kb_admin.py` |
| `POST /agent/query` | RAG retrieval (returns results with artifact_ids) | `routers/agents.py` |
| `POST /agent/verify-stream` | Verification SSE stream | `routers/agents.py` |
| `POST /chat/completions` | Generate LLM answer for RAGAS evaluation | `routers/chat.py` |

---

## Tier 1: Verification Efficacy (`verification_efficacy.py`)

Tests the full claim extraction → classification → verification pipeline end-to-end against the live stack via `POST /agent/verify-stream` SSE endpoint.

### Ground-truth fixture format (`verification_cases.jsonl`)

```jsonl
{
  "id": "V-01",
  "description": "Factual claim supported by KB",
  "response_text": "Python was created by Guido van Rossum in 1991.",
  "user_query": "Who created Python?",
  "expected_claims": [
    {"text_fragment": "Guido van Rossum", "type": "factual", "expected_verdict": "supported"}
  ],
  "seed_content": "Python is a programming language created by Guido van Rossum, first released in 1991.",
  "seed_domain": "coding"
}
```

### Test cases (21 cases across 5 claim types)

**Factual claims (6 cases):**
- V-01: KB-supported factual claim → expected "supported"
- V-02: KB-contradicted factual claim → expected "refuted"
- V-03: Claim with no KB evidence → expected "insufficient_info"
- V-04: Numerical statistic in KB → expected "supported"
- V-05: Attribution claim ("According to X...") → expected "supported"
- V-06: Multiple claims in one response (2 supported, 1 refuted)

**Evasion claims (4 cases):**
- V-10: Hedging language ("It might be possible that...") → evasion detected
- V-11: Deflection ("That depends on many factors...") → evasion detected
- V-12: Direct factual answer → NOT flagged as evasion
- V-13: Legitimate uncertainty ("Research is ongoing...") → NOT flagged as evasion

**Citation claims (4 cases):**
- V-20: Citation to known source → citation type detected
- V-21: Fabricated citation ("According to Smith et al., 2024") → citation type, refuted
- V-22: Real reference in KB → citation type, supported
- V-23: URL citation → citation type detected

**Recency claims (4 cases):**
- V-30: Claim with past year reference ("In 2023, X announced...") → recency type detected
  - Uses year < current_year + temporal markers to trigger `_reclassify_recency()`
- V-31: Claim about "current" state ("The current version is...") → recency type detected
- V-32: Timeless factual claim ("Water boils at 100°C") → NOT flagged as recency
- V-33: Future-tense with temporal marker ("The upcoming release will include...") → recency type
  - Uses "upcoming" marker which triggers recency via `STALE_KNOWLEDGE_PATTERNS` without requiring year condition

**Ignorance claims (3 cases):**
- V-40: "I don't have information about..." → ignorance type detected
- V-41: Hedged knowledge ("I'm not sure, but...") → ignorance type detected
- V-42: "I cannot access real-time data for..." → ignorance type detected

### Execution

```python
async def test_verification_case(case, client):
    # 1. Seed KB content if needed
    if case.seed_content:
        resp = await client.post("/ingest", json={
            "content": case.seed_content, "domain": case.seed_domain
        })
        assert resp.status_code == 200
        seed_artifact_id = resp.json()["artifact_id"]
        # Poll until artifact appears in search (max 10s)
        await wait_for_indexed(client, seed_artifact_id, timeout=10)

    # 2. Stream verification via POST /agent/verify-stream
    claims = await stream_verify(client, case.response_text, case.user_query)

    # 3. Assert claim extraction
    for expected in case.expected_claims:
        matched = find_matching_claim(claims, expected.text_fragment)
        assert matched, f"Claim not extracted: {expected.text_fragment}"
        if expected.type != "any":
            assert matched.claim_type == expected.type
        if expected.expected_verdict:
            assert matched.status == expected.expected_verdict

    # 4. Cleanup seeded content via DELETE /admin/artifacts/{id}
    if case.seed_content:
        await client.delete(f"/admin/artifacts/{seed_artifact_id}")
```

### Indexing wait strategy

Instead of a fixed `sleep(2)` (unreliable under load), use a poll-based wait:

```python
async def wait_for_indexed(client, artifact_id, timeout=10):
    """Poll until artifact is retrievable, with exponential backoff."""
    deadline = time.time() + timeout
    delay = 0.5
    while time.time() < deadline:
        resp = await client.get(f"/artifacts/{artifact_id}")
        if resp.status_code == 200:
            return
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 3)
    raise TimeoutError(f"Artifact {artifact_id} not indexed within {timeout}s")
```

### Metrics collected

- Extraction recall: (claims matched / claims expected)
- Extraction precision: (claims matched / claims extracted) — flag noisy extraction
- Verdict accuracy: (correct verdicts / total verdicts)
- Per-type breakdown: accuracy per claim type
- Latency: P50/P95 per verification call

---

## Tier 2: RAG Benchmark (`rag_benchmark.py`)

Two sub-tests: retrieval quality and generation quality. All tests use HTTP endpoints only — no in-process imports of `eval.harness` or `agents.query_agent`.

### Phase A: Seed deterministic benchmark content

Ingest 10 known documents via `POST /ingest`. Each contains unique eval marker text for deterministic matching.

```python
SEED_DOCUMENTS = [
    {"content": "The Fibonacci sequence starts 0, 1, 1, 2, 3, 5, 8, 13, 21... Each number is the sum of the two preceding ones. [EVAL_MARKER_FIB]",
     "domain": "coding"},
    {"content": "Compound interest formula: A = P(1 + r/n)^(nt) where P is principal, r is annual rate, n is compounding frequency, t is years. [EVAL_MARKER_COMPOUND]",
     "domain": "finance"},
    # ... 8 more across coding, finance, general domains
]
```

After seeding, build benchmark queries dynamically with actual artifact IDs returned by `/ingest`.

### Phase B: Retrieval metrics (HTTP-based)

For each benchmark query, call `POST /agent/query` and compute IR metrics locally in the test container:

```python
async def evaluate_retrieval(client, queries):
    """HTTP-based retrieval evaluation — reimplements eval.metrics locally."""
    results = []
    for q in queries:
        resp = await client.post("/agent/query", json={
            "query": q["query"], "top_k": 20, "domains": [q["domain"]]
        })
        ranked_ids = [r["artifact_id"] for r in resp.json()["results"]]
        relevant = set(q["relevant_ids"])

        results.append({
            "query": q["query"],
            "ndcg_5": ndcg_at_k(ranked_ids, relevant, 5),
            "ndcg_10": ndcg_at_k(ranked_ids, relevant, 10),
            "mrr": mrr(ranked_ids, relevant),
            "precision_5": precision_at_k(ranked_ids, relevant, 5),
            "recall_10": recall_at_k(ranked_ids, relevant, 10),
        })
    return results
```

The IR metric functions (NDCG, MRR, P@K, R@K) are reimplemented locally in the eval test module (~40 lines, pure math, no dependencies). This avoids importing `src/mcp/eval/metrics.py` which would require mounting the MCP source tree.

**Pipeline comparison framework:** Phase B can be extended for Approach C by calling with `use_reranking` parameter variations. The current implementation tests the default pipeline configuration.

### Phase C: RAGAS generation quality

For 5 benchmark queries, generate an LLM answer via `POST /chat/completions` (the SSE chat proxy), then evaluate with a lightweight LLM-judge:

```python
async def evaluate_generation(client, queries_with_contexts):
    """RAGAS-style LLM-as-judge evaluation via HTTP."""
    for q in queries_with_contexts:
        # 1. Get RAG context
        rag_resp = await client.post("/agent/query", json={
            "query": q["query"], "top_k": 5, "domains": [q["domain"]]
        })
        contexts = [r["content"] for r in rag_resp.json()["results"]]

        # 2. Generate answer via chat proxy (SSE, collect full response)
        answer = await generate_chat_answer(client, q["query"])

        # 3. LLM-as-judge: call chat proxy with judge prompts
        faithfulness = await judge_faithfulness(client, answer, contexts)
        relevancy = await judge_relevancy(client, q["query"], answer)
```

**`generate_chat_answer()`**: Calls `POST /chat/completions` with SSE streaming, collects the full response text. Uses the default model routed by the chat proxy.

**LLM judge functions**: Each sends a structured judge prompt to `POST /chat/completions` requesting JSON `{"score": 0.0-1.0, "reasoning": "..."}`. This reuses the existing chat infrastructure rather than importing `ragas_metrics.py` directly.

### Cleanup

All seeded documents cleaned up via `DELETE /admin/artifacts/{id}` (exists in `routers/kb_admin.py:340`).

---

## Tier 3: Smart Routing Validation (`routing_validation.py`)

Tests the router's **heuristic classification** via observable HTTP behavior. Since `_classify_complexity()` is a private function not accessible via HTTP, we test it indirectly:

### Approach: Classification via observable model selection

Call `POST /chat/completions` with different query types and inspect the `cerid_meta` SSE event which reports the resolved model. The routing decision reveals the complexity classification:

```python
async def test_routing(client, case):
    """Verify model selection reflects expected complexity routing."""
    model, meta = await get_routed_model(client, case["query"])
    # Simple → free/cheap model, Complex → capable model, Research → :online model
    if case.get("expected_model_tier"):
        assert model_tier(model) == case["expected_model_tier"]
```

### Deterministic test cases (`routing_cases.jsonl`)

```jsonl
{"query": "What is Python?", "expected_model_tier": "free_or_cheap", "description": "Simple factual"}
{"query": "Implement a B-tree in Rust with concurrent access and lock-free readers", "expected_model_tier": "capable", "description": "Complex coding"}
{"query": "What is the current stock price of AAPL?", "expected_model_tier": "research_online", "description": "Research current events"}
{"query": "hello", "expected_model_tier": "free_or_cheap", "description": "Trivial greeting"}
{"query": "Compare the economic implications of quantitative easing versus fiscal stimulus in post-pandemic recovery across G7 nations", "expected_model_tier": "capable", "description": "Complex analysis"}
```

### Ollama caveat

When `OLLAMA_ENABLED=true`, simple queries may route to Ollama instead of free OpenRouter models. Tests use tier-based assertions (`free_or_cheap` matches both Ollama and free OpenRouter) rather than exact model strings. If Ollama is unavailable (cached availability check returns false), the fallback to OpenRouter free is deterministic.

---

## Tier 4: Interactive Browser E2E (Playwright MCP)

Extends the existing 10 E2E tests with 6 new interaction-focused tests:

| ID | Test | Steps | Assert |
|----|------|-------|--------|
| E-11 | Verification flow | Send factual query → wait for response → observe verification badges | Claim overlays appear, status bar shows counts |
| E-12 | Claim overlay interaction | Click footnote marker [N] → popover opens | Popover shows claim text, status badge, source, similarity % |
| E-13 | Multi-turn coherence | Send "What is X?" → get response → send "Tell me more about it" | Second response references context from first |
| E-14 | KB context panel | Send query with KB content → check KB panel | Shows relevant artifacts with filenames and relevance scores |
| E-15 | Settings persistence | Change a setting → reload page → verify | Setting value persists across page reload |
| E-16 | Error recovery | Send query → observe error banner if any → verify retry | Error handling UI works gracefully |

**Note on E-16:** Testing rate-limit recovery in-browser is impractical without temporarily lowering limits. Instead, test general error recovery: send a query, verify error handling UI appears gracefully on any transient error, and that the user can retry successfully. Mark as best-effort / manual-only if the test environment doesn't produce errors naturally.

---

## Orchestration

### Docker execution for Tiers 1–3

Runs inside Docker on `llm-network` (same pattern as existing beta functional tests):

```bash
docker run --rm --network cerid-ai_llm-network \
  -v "$(pwd)/tests/beta:/tests" -w /tests \
  python:3.11-slim bash -c \
  "pip install -q httpx pytest pytest-asyncio && \
   python -m pytest eval/ -v --tb=short --junitxml=eval/reports/eval.xml"
```

No MCP source tree mounted. All communication is HTTP. The eval module includes its own lightweight IR metric functions (~40 lines) and LLM-judge helpers.

### Integration with `run.sh`

```bash
./tests/beta/run.sh --eval              # Run eval suite only
./tests/beta/run.sh --eval --browser    # Eval + interactive E2E
./tests/beta/run.sh --full              # All beta tests + eval suite
```

Gating: Eval suite runs AFTER all beta P0 tests pass. Browser E2E runs last.

### Report output

Generates `tests/beta/eval/reports/eval-report-<timestamp>.md` with:
- Verification efficacy table (per-case pass/fail, per-type accuracy)
- RAG benchmark table (per-query NDCG/MRR, aggregate scores)
- RAGAS quality scores (faithfulness, relevancy, precision, recall)
- Routing validation results
- Browser E2E interaction results
- Overall efficacy verdict: GO / GO-WITH-CAVEATS / NO-GO

---

## Extensibility for Approach C

The framework supports future ablation and pipeline comparison:

1. **`benchmark_seed.jsonl`** — Expandable; add more seed documents for broader corpus
2. **Pipeline comparison** — `rag_benchmark.py` can call `/agent/query` with different `use_reranking` values to compare hybrid_reranked vs hybrid vs vector_only
3. **Ablation via feature flags** — Future work: expose `src/mcp/eval/ablation.py`'s 7 feature toggles via an admin endpoint (`POST /admin/eval/toggle`), then the HTTP-based eval suite can toggle features and re-run benchmarks without in-process coupling
4. **RAGAS expansion** — Add more benchmark queries + judge prompts to increase statistical power
5. **Benchmark persistence** — Save eval results to Redis or a file for trend analysis across runs

To enable Approach C later:
```bash
./tests/beta/run.sh --eval --ablation   # Future: run ablation study (requires admin toggle endpoint)
```

---

## Cost Estimate

| Component | LLM Calls | Est. Cost |
|-----------|-----------|-----------|
| Verification (21 cases × ~3 claims) | ~63 verification calls (GPT-4o-mini) | ~$0.01 |
| RAGAS quality (5 queries × 2 judges) | 10 LLM judge calls | ~$0.02 |
| RAG generation (5 queries) | 5 chat completions | ~$0.02 |
| Routing validation (5 queries) | 5 chat completions | ~$0.01 |
| Smart routing (no LLM) | 0 | $0.00 |
| **Total** | **~83 calls** | **~$0.06** |

---

## Files to Create

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `tests/beta/eval/conftest.py` | ~100 | Shared fixtures, httpx client, seed/cleanup, SSE parser, IR metrics |
| `tests/beta/eval/verification_efficacy.py` | ~280 | 21 verification ground-truth tests |
| `tests/beta/eval/rag_benchmark.py` | ~220 | RAG retrieval + RAGAS quality evaluation |
| `tests/beta/eval/routing_validation.py` | ~100 | Smart router observable behavior tests |
| `tests/beta/eval/fixtures/verification_cases.jsonl` | ~65 | 21 verification test cases |
| `tests/beta/eval/fixtures/benchmark_seed.jsonl` | ~30 | 10 seed documents for RAG eval |
| `tests/beta/eval/fixtures/routing_cases.jsonl` | ~15 | Router test cases |

**Files to modify:**
| File | Change |
|------|--------|
| `tests/beta/run.sh` | Add `--eval` and `--full` flags |
| `.gitignore` | Add `tests/beta/eval/reports/` |

---

## Verification Plan

1. Run `./tests/beta/run.sh --eval` — all eval tiers execute
2. Verification efficacy: ≥80% extraction recall, ≥70% verdict accuracy
3. RAG benchmark: NDCG@5 ≥0.6, MRR ≥0.5
4. RAGAS: faithfulness ≥0.7, answer_relevancy ≥0.7
5. Routing: Correct model tier selection on all deterministic cases
6. Browser E2E: E-11 through E-16 pass via Playwright MCP
7. Final report reviewed for completeness
