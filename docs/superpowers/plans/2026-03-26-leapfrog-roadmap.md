# Leapfrog Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 6 high-impact improvements to maximize cerid-ai's acqui-hire appeal — built-in RAG eval harness, real-time collaborative memory, observability dashboard upgrade, enterprise bridge, mobile/desktop parity, and public benchmark tooling.

**Architecture:** All improvements build on the Phase C core/app separation. Core-tier features go into `core/`, enterprise features into a new `enterprise/` directory, and app-layer integrations into `app/`. Each improvement is self-contained with its own tests and can be merged independently.

**Tech Stack:** Python 3.11, FastAPI, ChromaDB, Neo4j, Redis, React 19, Vite, WebSocket, CRDT (Yjs), ONNX

---

## Priority Order (by acqui-hire impact × effort ratio)

1. **RAG Evaluation Harness** — Core, highest signal (60% done → 100%)
2. **Observability Dashboard Upgrade** — Core, highest signal (70% done → 100%)
3. **Enterprise Bridge + Compliance Kit** — Enterprise tier, highest differentiation (50% → 100%)
4. **Real-Time Collaborative Memory** — Core, unique moat (40% → 100%)
5. **Public Benchmark & Migration Tooling** — Open-source signal (35% → 100%)
6. **Mobile-First + Desktop Parity** — App tier, polish (45% → 100%)

---

### Task 1: RAG Eval — Complete Harness + Ablation Router

**Files:**
- Modify: `src/mcp/app/eval/harness.py`
- Modify: `src/mcp/app/eval/ablation.py`
- Modify: `src/mcp/app/routers/eval.py`
- Create: `src/mcp/app/eval/leaderboard.py`
- Create: `src/mcp/app/eval/datasets/beir_subset.jsonl`
- Modify: `src/mcp/config/settings.py`
- Test: `src/mcp/tests/test_eval_harness.py`

- [ ] **Step 1: Write failing tests for ablation router + leaderboard**

```python
# tests/test_eval_harness.py
import pytest
from unittest.mock import AsyncMock, patch
from app.eval.leaderboard import LeaderboardEntry, update_leaderboard, get_leaderboard
from app.eval.harness import EvalResult

def test_leaderboard_entry_creation():
    entry = LeaderboardEntry(
        pipeline="hybrid_reranked",
        avg_ndcg_5=0.85, avg_mrr=0.90,
        avg_faithfulness=0.88, avg_answer_relevancy=0.92,
        n_queries=50, timestamp="2026-03-26T00:00:00Z",
    )
    assert entry.pipeline == "hybrid_reranked"
    assert entry.avg_ndcg_5 == 0.85

def test_leaderboard_sorted_by_ndcg():
    entries = [
        LeaderboardEntry(pipeline="a", avg_ndcg_5=0.7, avg_mrr=0.0, n_queries=1, timestamp=""),
        LeaderboardEntry(pipeline="b", avg_ndcg_5=0.9, avg_mrr=0.0, n_queries=1, timestamp=""),
    ]
    sorted_entries = sorted(entries, key=lambda e: e.avg_ndcg_5, reverse=True)
    assert sorted_entries[0].pipeline == "b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_eval_harness.py -v`
Expected: FAIL (leaderboard module not found)

- [ ] **Step 3: Implement leaderboard module**

Create `app/eval/leaderboard.py`:
- `LeaderboardEntry` dataclass with IR + RAGAS metrics
- `update_leaderboard(results, ragas_scores, redis)` — stores entry in Redis sorted set
- `get_leaderboard(redis, top_k=20)` — returns top entries by composite score
- Composite score: `0.4 * ndcg_5 + 0.2 * mrr + 0.2 * faithfulness + 0.2 * answer_relevancy`

- [ ] **Step 4: Wire ablation into eval router**

Add to `app/routers/eval.py`:
- `POST /api/eval/ablation` — run ablation study with RAGAS scoring
- `GET /api/eval/leaderboard` — return leaderboard entries
- `POST /api/eval/ragas` — run RAGAS metrics on a single query/answer/context triple
- `GET /api/eval/compare` — compare two pipeline runs

- [ ] **Step 5: Add BEIR-subset benchmark dataset**

Create `app/eval/datasets/beir_subset.jsonl` with 50 queries across 5 domains:
- 10 coding queries, 10 finance queries, 10 research queries, 10 general queries, 10 trading queries
- Each with 3-5 relevant artifact IDs (synthetic but realistic)

- [ ] **Step 6: Add eval config to settings.py**

```python
# Eval harness settings
EVAL_ENABLED = os.getenv("CERID_EVAL_ENABLED", "false").lower() in ("1", "true")
EVAL_RAGAS_MODEL = os.getenv("CERID_EVAL_RAGAS_MODEL", "")  # Uses default LLM if empty
EVAL_LEADERBOARD_MAX = 50
EVAL_DEFAULT_BENCHMARK = "beir_subset.jsonl"
```

- [ ] **Step 7: Run all tests and verify**

Run: `python -m pytest tests/test_eval_harness.py tests/test_eval_metrics.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/mcp/app/eval/ src/mcp/app/routers/eval.py src/mcp/config/settings.py src/mcp/tests/test_eval_harness.py
git commit -m "feat(eval): complete RAG eval harness — ablation router, RAGAS integration, leaderboard, BEIR-subset dataset"
```

---

### Task 2: Observability — RAGAS Cards + Alerting + Enhanced Dashboard

**Files:**
- Modify: `src/mcp/app/routers/observability.py`
- Modify: `src/mcp/app/utils/metrics.py`
- Create: `src/mcp/app/routers/alerts.py`
- Modify: `src/mcp/config/settings.py`
- Test: `src/mcp/tests/test_observability.py`

- [ ] **Step 1: Write failing tests for RAGAS metric cards + alerting**

```python
def test_ragas_metrics_endpoint():
    """GET /observability/ragas should return RAGAS metric aggregations."""
    ...

def test_alert_threshold_trigger():
    """Alert should fire when metric crosses threshold."""
    ...

def test_cost_per_query_metric():
    """Cost-per-query should be computed from total cost / query count."""
    ...
```

- [ ] **Step 2: Add RAGAS metric recording to MetricsCollector**

Add new metric names: `ragas_faithfulness`, `ragas_answer_relevancy`, `ragas_context_precision`, `ragas_context_recall`. Record after each eval run.

- [ ] **Step 3: Add alerting router**

Create `app/routers/alerts.py`:
- `GET /observability/alerts` — list configured alerts
- `POST /observability/alerts` — create threshold alert (metric, operator, threshold, webhook_url)
- `DELETE /observability/alerts/{id}` — remove alert
- Alert evaluation runs on metric recording via Redis pub/sub

- [ ] **Step 4: Add RAGAS + cost-per-query endpoints to observability router**

- `GET /observability/ragas` — aggregated RAGAS metrics
- `GET /observability/cost-per-query` — average cost per query over window
- `GET /observability/claim-accuracy` — verification claim accuracy heatmap data

- [ ] **Step 5: Add alerting config to settings.py**

```python
ALERT_CHECK_INTERVAL_S = 60
ALERT_MAX_PER_METRIC = 5
ALERT_WEBHOOK_TIMEOUT_S = 10
```

- [ ] **Step 6: Run tests and verify**

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(observability): RAGAS metric cards, cost-per-query, claim accuracy heatmap, threshold alerting"
```

---

### Task 3: Enterprise Bridge — ABAC Middleware + SSO Foundation

**Files:**
- Create: `src/mcp/enterprise/__init__.py`
- Create: `src/mcp/enterprise/abac.py`
- Create: `src/mcp/enterprise/classification.py`
- Create: `src/mcp/enterprise/sso.py`
- Create: `src/mcp/enterprise/audit_immutable.py`
- Modify: `src/mcp/app/middleware/auth.py`
- Modify: `src/mcp/config/settings.py`
- Test: `src/mcp/tests/test_enterprise.py`

- [ ] **Step 1: Write failing tests for ABAC + classification**

```python
def test_abac_policy_evaluation():
    """ABAC should grant/deny based on user attributes + resource attributes."""
    policy = ABACPolicy(rules=[
        ABACRule(subject_attrs={"role": "analyst"}, resource_attrs={"classification": "SECRET"}, action="read", effect="deny"),
        ABACRule(subject_attrs={"role": "analyst"}, resource_attrs={"classification": "UNCLASSIFIED"}, action="read", effect="allow"),
    ])
    assert policy.evaluate(subject={"role": "analyst"}, resource={"classification": "UNCLASSIFIED"}, action="read") == "allow"
    assert policy.evaluate(subject={"role": "analyst"}, resource={"classification": "SECRET"}, action="read") == "deny"

def test_classification_aggregation_detection():
    """Should detect when combining UNCLASSIFIED chunks implies a higher classification."""
    ...
```

- [ ] **Step 2: Implement ABAC engine**

Create `enterprise/abac.py`:
- `ABACRule` dataclass: subject_attrs, resource_attrs, action, effect
- `ABACPolicy` class: rules list, `evaluate(subject, resource, action) -> "allow" | "deny"`
- `ABACMiddleware(BaseHTTPMiddleware)` — reads user attributes from JWT claims, resource attributes from endpoint metadata
- Policy storage in Redis (JSON-serialized rules)

- [ ] **Step 3: Implement classification metadata**

Create `enterprise/classification.py`:
- `ClassificationLevel` enum: UNCLASSIFIED, CUI, SECRET, TOP_SECRET, TS_SCI
- `classify_chunk(metadata) -> ClassificationLevel` — derives from source metadata
- `detect_aggregation_risk(chunks) -> list[AggregationWarning]` — flags when combining chunks from different sources could imply higher classification
- Add `classification_level` and `compartment` fields to ChromaDB metadata schema

- [ ] **Step 4: Implement SSO foundation**

Create `enterprise/sso.py`:
- `SSOConfig` dataclass: provider (saml/oidc), metadata_url, client_id, client_secret, attribute_mapping
- `validate_saml_assertion(xml) -> dict` — parse SAML response, extract user attributes
- `exchange_oidc_code(code, config) -> dict` — exchange authorization code for tokens
- Wire into JWT auth middleware as alternative token source

- [ ] **Step 5: Implement immutable audit logging**

Create `enterprise/audit_immutable.py`:
- Uses Redis Streams (XADD, immutable append-only)
- `audit_log(event_type, actor, resource, action, result, metadata)` — append to stream
- `query_audit_log(filters, since, until) -> list[AuditEntry]` — XRANGE with filtering
- Satisfies AU-2/AU-6/AU-12 compliance requirements

- [ ] **Step 6: Add enterprise config to settings.py**

```python
# Enterprise features (disabled by default)
CERID_ENTERPRISE = os.getenv("CERID_ENTERPRISE", "false").lower() in ("1", "true")
ABAC_POLICY_KEY = "cerid:enterprise:abac_policy"
SSO_PROVIDER = os.getenv("CERID_SSO_PROVIDER", "")  # saml | oidc
SSO_METADATA_URL = os.getenv("CERID_SSO_METADATA_URL", "")
CLASSIFICATION_ENABLED = os.getenv("CERID_CLASSIFICATION", "false").lower() in ("1", "true")
AUDIT_STREAM_KEY = "cerid:audit:stream"
AUDIT_RETENTION_DAYS = int(os.getenv("CERID_AUDIT_RETENTION_DAYS", "365"))
```

- [ ] **Step 7: Run tests and verify**

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(enterprise): ABAC middleware, classification-by-aggregation detection, SSO foundation, immutable audit logging"
```

---

### Task 4: Collaborative Memory — WebSocket + CRDT Live Sync

**Files:**
- Create: `src/mcp/app/routers/ws_sync.py`
- Create: `src/mcp/app/sync/crdt.py`
- Create: `src/mcp/app/sync/presence.py`
- Modify: `src/mcp/app/sync/export.py`
- Modify: `src/mcp/app/sync/import_.py`
- Modify: `src/mcp/app/main.py`
- Modify: `src/mcp/config/settings.py`
- Create: `src/web/src/hooks/use-live-sync.ts`
- Create: `src/web/src/hooks/use-presence.ts`
- Test: `src/mcp/tests/test_sync_crdt.py`

- [ ] **Step 1: Write failing tests for CRDT merge + WebSocket protocol**

```python
def test_crdt_lww_register_merge():
    """Last-Writer-Wins register should resolve concurrent updates by timestamp."""
    ...

def test_crdt_or_set_merge():
    """Observed-Remove Set should handle concurrent add/remove correctly."""
    ...

def test_presence_tracking():
    """Presence should track connected users and their cursor positions."""
    ...
```

- [ ] **Step 2: Implement CRDT primitives**

Create `app/sync/crdt.py`:
- `LWWRegister` — Last-Writer-Wins register for scalar values (artifact titles, tags)
- `ORSet` — Observed-Remove Set for collection membership (domain membership, related artifacts)
- `LWWElementDict` — LWW map for metadata key-value pairs
- `merge(local_state, remote_state) -> merged_state` — deterministic merge
- All operations produce deltas that can be sent over WebSocket

- [ ] **Step 3: Implement WebSocket sync endpoint**

Create `app/routers/ws_sync.py`:
- `WebSocket /ws/sync` — bidirectional sync channel
- Protocol: JSON messages with types: `delta`, `presence`, `ack`, `conflict`
- Server maintains per-connection state, broadcasts deltas to other connections
- Redis pub/sub for cross-instance synchronization
- Authentication via query param token (validated against JWT secret)

- [ ] **Step 4: Implement presence tracking**

Create `app/sync/presence.py`:
- `PresenceManager` — tracks connected users via Redis hash
- Heartbeat every 30s, timeout after 90s
- Reports: user_id, display_name, active_domain, cursor_artifact_id
- Broadcasts presence changes to all connected WebSocket clients

- [ ] **Step 5: Wire into existing sync system**

Modify `app/sync/export.py` and `app/sync/import_.py`:
- Generate CRDT deltas during export/import
- Detect and auto-resolve conflicts using CRDT merge semantics
- Fall back to existing tombstone/manifest system for offline sync

- [ ] **Step 6: Add WebSocket route to main.py**

```python
from app.routers.ws_sync import router as ws_sync_router
app.include_router(ws_sync_router)
```

- [ ] **Step 7: Implement React hooks for live sync**

Create `src/web/src/hooks/use-live-sync.ts`:
- WebSocket connection with auto-reconnect
- Delta application to local state
- Optimistic updates with server confirmation

Create `src/web/src/hooks/use-presence.ts`:
- Presence indicator component data
- User avatar + active domain display

- [ ] **Step 8: Add sync config to settings.py**

```python
WS_SYNC_ENABLED = os.getenv("CERID_WS_SYNC", "false").lower() in ("1", "true")
WS_HEARTBEAT_INTERVAL_S = 30
WS_PRESENCE_TIMEOUT_S = 90
WS_MAX_CONNECTIONS = 50
SYNC_CRDT_ENABLED = True  # Use CRDT for conflict resolution
```

- [ ] **Step 9: Run tests and verify**

- [ ] **Step 10: Commit**

```bash
git commit -m "feat(sync): real-time collaborative memory — WebSocket live sync, CRDT conflict resolution, presence tracking"
```

---

### Task 5: Public Benchmark + Migration Tooling

**Files:**
- Create: `src/mcp/app/eval/benchmark_suite.py`
- Create: `src/mcp/app/eval/datasets/` (multiple .jsonl files)
- Create: `src/mcp/scripts/migrate_from.py`
- Create: `src/mcp/app/parsers/notion.py`
- Create: `src/mcp/app/parsers/obsidian.py`
- Create: `src/mcp/app/routers/migration.py`
- Modify: `src/mcp/config/settings.py`
- Test: `src/mcp/tests/test_benchmark_suite.py`
- Test: `src/mcp/tests/test_migration.py`

- [ ] **Step 1: Write failing tests for benchmark suite**

```python
def test_benchmark_suite_runs_all_categories():
    """Suite should run queries across all categories and produce a report."""
    ...

def test_benchmark_report_format():
    """Report should include per-category scores, latency, and comparison table."""
    ...
```

- [ ] **Step 2: Implement benchmark suite**

Create `app/eval/benchmark_suite.py`:
- `BenchmarkCategory` dataclass: name, queries, expected_metrics
- `BenchmarkSuite` class: categories list, `run_all()`, `generate_report()`
- Categories: factual-recall, multi-hop-reasoning, temporal-queries, cross-domain, adversarial
- Report format: Markdown table with scores, comparison vs baselines
- `compare_against(other_report)` — generates "Cerid vs X" comparison

- [ ] **Step 3: Create benchmark datasets**

Create domain-specific JSONL files in `app/eval/datasets/`:
- `factual_recall.jsonl` — 20 queries testing direct fact retrieval
- `multi_hop.jsonl` — 20 queries requiring cross-artifact reasoning
- `temporal.jsonl` — 20 queries about time-sensitive information
- `cross_domain.jsonl` — 20 queries spanning multiple knowledge domains
- `adversarial.jsonl` — 20 queries designed to test robustness

- [ ] **Step 4: Implement migration importers**

Create `app/parsers/notion.py`:
- Parse Notion export ZIP (HTML or Markdown format)
- Extract page title, content, metadata, parent/child relationships
- Map Notion databases to cerid domains
- Preserve internal links as Neo4j relationships

Create `app/parsers/obsidian.py`:
- Parse Obsidian vault directory
- Handle `[[wiki-links]]` → Neo4j relationships
- Extract YAML frontmatter as metadata
- Map folder structure to cerid domains

- [ ] **Step 5: Create migration router**

Create `app/routers/migration.py`:
- `POST /api/migrate/notion` — upload Notion export ZIP, ingest all pages
- `POST /api/migrate/obsidian` — upload Obsidian vault ZIP, ingest all notes
- `GET /api/migrate/status/{job_id}` — check migration progress
- Background task execution with progress tracking in Redis

- [ ] **Step 6: Create CLI migration script**

Create `scripts/migrate_from.py`:
- `python -m scripts.migrate_from notion /path/to/export.zip`
- `python -m scripts.migrate_from obsidian /path/to/vault/`
- Progress bar, dry-run mode, domain mapping config

- [ ] **Step 7: Run tests and verify**

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(benchmark): public benchmark suite (5 categories, 100 queries) + Notion/Obsidian one-click importers"
```

---

### Task 6: Mobile-First + Desktop Electron Parity

**Files:**
- Modify: `src/web/src/components/layout/Sidebar.tsx`
- Modify: `src/web/src/components/layout/MainLayout.tsx`
- Create: `src/web/src/hooks/use-offline-queue.ts`
- Create: `src/web/src/hooks/use-swipe-navigation.ts`
- Modify: `src/web/vite.config.ts`
- Modify: `packages/desktop/src/main.ts`
- Modify: `packages/desktop/package.json`
- Create: `src/web/public/manifest.json`
- Create: `src/web/public/sw.js`
- Test: `src/web/src/__tests__/offline-queue.test.ts`
- Test: `src/web/src/__tests__/swipe-navigation.test.ts`

- [ ] **Step 1: Write failing tests for offline queue + swipe navigation**

```typescript
describe('useOfflineQueue', () => {
  it('should queue requests when offline', () => { ... });
  it('should replay queued requests when back online', () => { ... });
  it('should deduplicate repeated requests in queue', () => { ... });
});

describe('useSwipeNavigation', () => {
  it('should detect left swipe to open sidebar', () => { ... });
  it('should detect right swipe to close sidebar', () => { ... });
});
```

- [ ] **Step 2: Implement offline queue hook**

Create `src/web/src/hooks/use-offline-queue.ts`:
- IndexedDB-backed request queue
- `navigator.onLine` detection with custom events
- Queue-and-replay pattern for mutations (ingest, settings changes)
- Exponential backoff retry on reconnection
- Visual indicator in UI when offline

- [ ] **Step 3: Implement swipe navigation**

Create `src/web/src/hooks/use-swipe-navigation.ts`:
- Touch event handlers with velocity detection
- Swipe left → open sidebar, swipe right → close
- Swipe threshold: 50px minimum, 200ms maximum duration
- Integrates with existing sidebar collapse state

- [ ] **Step 4: Add PWA manifest + service worker**

Create `src/web/public/manifest.json` with app metadata.
Create `src/web/public/sw.js` — cache-first for static assets, network-first for API.
Add `vite-plugin-pwa` to vite config for automatic SW registration.

- [ ] **Step 5: Enhance responsive layout**

Modify `MainLayout.tsx`:
- Bottom navigation bar on mobile (< 768px) replacing sidebar
- Floating action button for quick-ingest on mobile
- Pull-to-refresh on conversation list
- Safe area inset support for notched devices

- [ ] **Step 6: Desktop Electron updates**

Modify `packages/desktop/src/main.ts`:
- Auto-updater with differential updates
- System tray with quick-query popup
- Native file drag-drop integration
- macOS menu bar integration
- Sign with `electron-builder` code signing config

- [ ] **Step 7: Run tests and verify**

Run: `cd src/web && npx vitest run`
Expected: All PASS including new tests

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(mobile): PWA with offline queue, swipe navigation, bottom nav bar + Electron desktop parity"
```

---

## Integration Verification

After all 6 tasks are complete:

- [ ] **Run full Python test suite** in Docker
- [ ] **Run full frontend test suite** with vitest
- [ ] **Verify import-linter** still passes with new enterprise/ directory
- [ ] **Check CI pipeline** — all 9 jobs should pass
- [ ] **Update CLAUDE.md** with new features, endpoints, and config
- [ ] **Update README.md** with leapfrog highlights
- [ ] **Update docs/API_REFERENCE.md** with new endpoints

## Critical Files Summary

| Area | Key Files |
|------|-----------|
| Eval Harness | `app/eval/leaderboard.py`, `app/eval/benchmark_suite.py`, `app/routers/eval.py` |
| Observability | `app/routers/observability.py`, `app/routers/alerts.py`, `app/utils/metrics.py` |
| Enterprise | `enterprise/abac.py`, `enterprise/classification.py`, `enterprise/sso.py`, `enterprise/audit_immutable.py` |
| Collaborative | `app/sync/crdt.py`, `app/routers/ws_sync.py`, `app/sync/presence.py` |
| Benchmarks | `app/eval/benchmark_suite.py`, `app/parsers/notion.py`, `app/parsers/obsidian.py`, `app/routers/migration.py` |
| Mobile/Desktop | `src/web/src/hooks/use-offline-queue.ts`, `src/web/src/hooks/use-swipe-navigation.ts`, `packages/desktop/` |
