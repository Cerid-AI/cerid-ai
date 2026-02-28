# Phase 11C: Knowledge Curation Agent

> **Date:** 2026-02-28
> **Status:** Design
> **Depends on:** Phase 11B (taxonomy tree), Phase 10D (test coverage)

---

## Problem Statement

Cerid AI can ingest, search, and retrieve knowledge artifacts, but has no mechanism for improving artifact quality over time. Summaries are auto-generated at ingestion and never refined. Keywords may be stale or irrelevant. There's no quality scoring to surface well-maintained artifacts over neglected ones.

**Current gaps:**
- No artifact quality assessment (completeness, freshness, keyword accuracy)
- No automated content improvement (re-summarize, refine keywords, fix categorization)
- No quality signals in search ranking (query agent treats all artifacts equally)
- No user-facing curation workflow (review → approve → apply improvements)

---

## Solution Overview

A new `agents/curator.py` module that:
1. **Scores** artifact quality on multiple dimensions
2. **Suggests** improvements (re-summarize, re-keyword, re-categorize)
3. **Applies** improvements when approved (auto or manual)
4. **Feeds** quality scores into the query agent's reranking pipeline

The agent operates in two modes:
- **Audit mode** — score and suggest (read-only, safe to run anytime)
- **Fix mode** — apply approved suggestions (writes to ChromaDB/Neo4j)

---

## Architecture

### Quality Scoring Dimensions

| Dimension | Weight | How Measured |
|-----------|--------|-------------|
| **Summary quality** | 0.25 | Length (50-500 chars optimal), coherence (LLM score) |
| **Keyword relevance** | 0.20 | Keywords vs. actual content similarity (embedding distance) |
| **Freshness** | 0.20 | Days since ingestion or last update (exponential decay) |
| **Completeness** | 0.20 | Has summary, keywords, tags, sub-category (checklist) |
| **Usage signal** | 0.15 | Query hit count from Redis audit log |

**Output:** `quality_score: float` in `[0.0, 1.0]` per artifact.

### Agent Function Signature

```python
async def curate(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
    redis_client=None,
    mode: str = "audit",           # "audit" | "fix"
    domains: Optional[List[str]] = None,
    min_score: float = 0.0,        # only process artifacts below this score
    max_artifacts: int = 50,       # limit per run
    improvements: Optional[List[str]] = None,  # ["summary", "keywords", "category", "tags"]
) -> Dict[str, Any]:
```

### Response Shape

```json
{
  "timestamp": "2026-02-28T12:00:00Z",
  "mode": "audit",
  "artifacts_scored": 42,
  "artifacts_improved": 0,
  "avg_quality_score": 0.72,
  "score_distribution": {
    "excellent": 12,
    "good": 18,
    "fair": 8,
    "poor": 4
  },
  "suggestions": [
    {
      "artifact_id": "abc123",
      "filename": "deployment-guide.md",
      "domain": "coding",
      "current_score": 0.35,
      "issues": ["summary_too_short", "stale_keywords", "missing_tags"],
      "suggested_summary": "...",
      "suggested_keywords": ["..."],
      "suggested_tags": ["..."]
    }
  ],
  "improvements_applied": []
}
```

---

## Implementation Plan

### Step 1: Quality Scoring Engine

**File:** `src/mcp/agents/curator.py`

Core functions:
- `score_summary(summary: str, content: str) -> float` — length check + optional LLM coherence
- `score_keywords(keywords: List[str], content: str, chroma_client) -> float` — embedding similarity
- `score_freshness(ingested_at: str, updated_at: Optional[str]) -> float` — exponential decay
- `score_completeness(artifact: Dict) -> float` — field presence checklist
- `score_usage(artifact_id: str, redis_client) -> float` — query hit count from audit log
- `compute_quality_score(artifact, chroma_client, redis_client) -> QualityReport`

### Step 2: Suggestion Generator

Functions:
- `suggest_summary(artifact, chroma_client) -> str` — re-summarize via LLM using stored chunks
- `suggest_keywords(artifact, chroma_client) -> List[str]` — extract from content via LLM
- `suggest_tags(artifact, taxonomy) -> List[str]` — map keywords to taxonomy tags
- `suggest_category(artifact, taxonomy) -> Tuple[str, str]` — domain + sub-category

### Step 3: Improvement Applier

Functions:
- `apply_improvements(artifact_id, suggestions, neo4j_driver, chroma_client)` — update Neo4j metadata + ChromaDB metadata
- Respects existing `update_artifact_metadata()` from `db/neo4j/artifacts.py`

### Step 4: Router Endpoint

**File:** `src/mcp/routers/agents.py`

```python
class CurateRequest(BaseModel):
    mode: str = Field("audit", pattern="^(audit|fix)$")
    domains: Optional[List[str]] = None
    min_score: float = Field(0.5, ge=0.0, le=1.0)
    max_artifacts: int = Field(50, ge=1, le=500)
    improvements: Optional[List[str]] = None

@router.post("/agent/curate")
async def curate_endpoint(req: CurateRequest): ...
```

### Step 5: Query Agent Integration

**File:** `src/mcp/agents/query_agent.py`

Add quality score as a reranking signal:
```python
# In rerank_results() or deduplicate_results()
# Boost results with higher quality scores
adjusted_relevance = relevance * (0.8 + 0.2 * quality_score)
```

This is a lightweight multiplier — high-quality artifacts get up to 20% boost.

### Step 6: Frontend Panel

**File:** `src/web/src/components/kb/curation-panel.tsx`

- Quality score badge on artifact cards (color-coded: green/yellow/red)
- Curation dashboard: score distribution chart, improvement suggestions
- "Run Curation" button with mode toggle (audit/fix)
- Per-artifact "Apply" button for individual improvements

---

## Integration Points

| System | How Curator Integrates |
|--------|----------------------|
| **ChromaDB** | Read chunks for re-summarization; read embeddings for keyword scoring |
| **Neo4j** | Read artifact metadata; write updated summary/keywords/tags |
| **Redis** | Read query hit counts for usage scoring; log curation events |
| **Bifrost** | LLM calls for re-summarization and keyword extraction |
| **Query Agent** | Quality scores feed into reranking multiplier |
| **Taxonomy** | Suggested tags validated against TAXONOMY dict |

---

## Testing Strategy

- Unit tests for each scoring function (mock DB responses)
- Integration test: ingest a low-quality artifact → curate audit → verify suggestions
- Integration test: curate fix → verify Neo4j/ChromaDB metadata updated
- Test quality score range normalization (always [0.0, 1.0])
- Test reranking boost (query agent returns higher-quality results first)

---

## Open Questions

1. **LLM cost:** Re-summarization requires LLM calls per artifact. Should we batch or limit to N artifacts per run? (Current design: `max_artifacts=50` default)
2. **Scheduled runs:** Should curation run on APScheduler like maintenance? (Recommendation: yes, weekly, audit-only)
3. **User approval flow:** Should "fix" mode require per-artifact approval in the UI, or allow bulk auto-fix? (Recommendation: start with per-artifact approval)
4. **Quality score storage:** Store in Neo4j as artifact property, or compute on-demand? (Recommendation: store in Neo4j, recompute on curation runs)
