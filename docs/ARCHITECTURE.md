# Cerid AI — Architecture

> **Last refresh:** 2026-08-09 (v1.0.1 — 55 MCP tools, 60 with the optional trading module, registered via the `app/tool_registry.py` decorator pattern; schema-fidelity CI gate; per-tool audit log + metrics + Sentry tag; SSE staleness eviction; `POST /mcp/call-sync` direct-HTTP fallback; `_warnings` envelope; `/health.invariants.mcp` rollups.)
> **Scope:** System layout, service topology, Phase C layer contract, data flow
> **Owner:** Anyone modifying the stack topology, adding a service, or splitting core/app boundaries

## Mission

Cerid AI is a **self-hosted, privacy-first Personal AI Knowledge Companion.** It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. Knowledge base stays local; LLM API calls send query context to the configured provider. Optional cloud sync (Dropbox) for cross-machine settings/conversations, encrypted when `CERID_ENCRYPTION_KEY` is set.

Core capabilities inventory lives in [`docs/PRESERVATION.md`](PRESERVATION.md) (§ "Invariants"). The preservation harness guards those capabilities across every consolidation sprint.

## Services

Microservices on a shared `llm-network` Docker bridge network. Services communicate by container name.

| Service | Port | Tech | Location |
|---|---|---|---|
| MCP Server (API) | 8888 | FastAPI / Python 3.12 | `src/mcp/` |
| ChromaDB | 8001 | Vector DB | `stacks/infrastructure/` |
| Neo4j | 7474, 7687 | Graph DB | `stacks/infrastructure/` |
| Redis | 6379 | Cache + audit log | `stacks/infrastructure/` |
| React GUI | 3000 | React 19 + Vite + nginx | `src/web/` |
| quenchforge (Ollama-compatible, optional) | 11434 | Local LLM pipeline tasks | External or Docker |

(The marketing site is deployed from its own repo (cerid.ai) — `packages/marketing/` no longer exists here.)

## Data flow

```
User → React GUI (:3000) → MCP Server (:8888) → OpenRouter → LLM Provider
                                              ↘ ChromaDB + Neo4j + Redis (RAG)

File ingestion:
~/cerid-archive/ → Watcher → POST /ingest_file → Parse → Dedup → Chunk
                                                                 ↘ ChromaDB
                                                                 ↘ Neo4j
                                                                 ↘ Redis
```

React GUI talks to MCP directly (CORS `*`). Chat + smart-router traffic routes through `core/utils/llm_client.py` straight to OpenRouter — no proxy layer.

## Directory structure

```
cerid-ai-internal/
├── CLAUDE.md                # Agent directives (under 150 lines post-Sprint H)
├── docker-compose.yml       # Unified root compose
├── .env.age / .env.example  # Encrypted secrets / template
├── Makefile                 # lock-python, install-hooks, deps-check, preservation-check
├── scripts/                 # start-cerid.sh, validate-env.sh, sync-repos.py, gen_*
├── docs/                    # ARCHITECTURE.md (this), API_REFERENCE.md, SYNC_PROTOCOL.md,
│                            # PRESERVATION.md, CONVENTIONS.md, ROUTER_REGISTRY.md
├── plugins/                 # BUSL-1.1 pro-tier plugins
├── plugins-premium/         # proprietary premium tier — NOT distributed;
│                            # absent from the public repository by design
├── src/mcp/                 # FastAPI MCP server (Python 3.12)
│   ├── core/                # Portable orchestrator core (FSL-1.1-ALv2)
│   │   ├── agents/          # Query, memory, hallucination, curator, self_rag, memory_consolidation
│   │   ├── contracts/       # ABCs: VectorStore, GraphStore, CacheStore, LLMClient
│   │   ├── retrieval/       # BM25, reranker, semantic cache, query decomposition
│   │   ├── routing/         # Smart router, model providers
│   │   └── utils/           # Embeddings, circuit breaker, LLM client, temporal, diversity, text, etc.
│   ├── app/                 # Application layer (concrete implementations)
│   │   ├── routers/         # ~68 router modules (count drifts — ls src/mcp/app/routers/)
│   │   ├── agents/          # Orchestration wrappers: assembler, curator, decomposer, memory,
│   │   │                    #                        retrieval_orchestrator, templates, triage,
│   │   │                    #                        hallucination/{confidence, verdict_parsing, ...}
│   │   ├── stores/          # ChromaVectorStore, Neo4jGraphStore, RedisCacheStore (internal-only)
│   │   ├── db/neo4j/        # Canonical Neo4j code: artifacts, memory, relationships, schema,
│   │   │                    # taxonomy, users, agents (CustomAgent CRUD), migrations (m0001, m0002)
│   │   ├── services/        # ingestion.py (ingest_content, ingest_file, dedup)
│   │   ├── middleware/      # auth, rate_limit, request_id, jwt, tenant_context
│   │   ├── parsers/         # PDF, office, structured, email, ebook
│   │   ├── eval/            # Retrieval evaluation harness + benchmark suite (internal-only)
│   │   ├── sync/            # CRDT, export, import, manifest, status
│   │   ├── models/          # Pydantic schemas
│   │   ├── main.py          # FastAPI entry + lifespan
│   │   ├── tools.py         # Legacy MCP tool dispatcher; most tools register via
│   │   │                    # @register_tool in tool_registry.py + mcp_tools/
│   │   │                    # (55 tools; 60 with the optional trading module)
│   │   └── internal_modules.py  # /health.invariants.internal_modules flags
│   ├── config/              # settings.py, taxonomy.py, features.py, providers.py
│   ├── routers/             # billing.py ONLY (internal-only; whole dir stripped from public)
│   ├── utils/               # 35 standalone utility modules (post-Sprint-E bridges retired)
│   ├── tests/
│   │   ├── integration/     # Preservation harness of integration invariants (I1-I8)
│   │   └── test_*.py        # 4,800+ Python tests
│   └── requirements.txt/.lock   # Python deps
├── src/web/                 # React GUI (React 19, Vite 7, Tailwind v4, shadcn/ui)
│   ├── src/components/      # chat/, kb/, settings/, monitoring/, audit/, memories/, ui/
│   ├── src/hooks/           # use-chat, use-verification-orchestrator, use-kb-context, ...
│   ├── src/contexts/        # Settings, KBInjection, Conversations, Auth
│   ├── src/lib/             # types.ts, api/, model-router.ts, canonical-claim alignment
│   └── src/__tests__/       # 2,700+ frontend tests
├── packages/desktop/        # Electron desktop app (internal-only)
├── stacks/                  # infrastructure/ (Neo4j, ChromaDB, Redis)
├── artifacts/ → ~/Dropbox/AI-Artifacts   (symlink)
└── data/ → src/mcp/data                  (symlink)
```

**What changed post-Sprint E/F:**

| Directory | Before consolidation | After |
|---|---|---|
| `src/mcp/services/` | 3 bridge files re-exporting from `app.services` | **deleted** |
| `src/mcp/agents/` | 14 files: 7 bridges + 5 standalones + 1 adapter + 1 subpackage | **deleted** — standalones moved to `app/agents/` |
| `src/mcp/utils/` | 56 files (21 bridges + 35 standalones) | 35 standalones only |
| `src/mcp/routers/` | 43 files: 32 bridge stubs + 11 legacy real + billing | **billing-only** — the two billing router modules (internal-strip target) |
| `src/mcp/db/neo4j/` | 8 bridge shims + 2 orphan implementations (`agents.py`, `graph_rag.py`) | **deleted** — canonical at `app/db/neo4j/`; `agents.py` relocated, `graph_rag.py` deleted as dead code (2026-04-21) |

Consumer code imports canonical paths (`core.utils.*`, `app.routers.*`, `app.agents.*`, `app.db.neo4j.*`). No more "which of three paths?" ambiguity. A `lint / no-legacy-neo4j-tree` CI guard prevents resurrection of the old shim tree.

## Phase C layer contract

Three layers, one rule: **core must not import app.**

### `core/` — portable orchestrator
- Licensed FSL-1.1-ALv2 (source-available) like the rest of the product,
  with an Apache-2.0 future license at two years. The Apache-2.0 carve-outs
  are `packages/sdk/**` and the client packages, not `core/`.
- Zero FastAPI, zero Chroma/Neo4j/Redis driver imports.
- Abstractions only: `core.contracts.VectorStore`, `GraphStore`, `CacheStore`, `LLMClient`.
- Houses pipeline algorithms: BM25, reranker, semantic cache, query decomposition, NLI entailment, smart routing, claim canonicalization (`core.agents.hallucination.models.ClaimVerification`).

### `app/` — concrete implementations
- FastAPI routers, store adapters (`ChromaVectorStore`, `Neo4jGraphStore`, `RedisCacheStore`), parsers, middleware, sync, eval, entry point.
- Free to import from `core` and bring in framework code (FastAPI, httpx, Pydantic).
- Houses orchestration wrappers that stitch core algorithms into runtime flows (`app/agents/assembler.py` etc.).

### `import-linter` gate
- Declared in `src/mcp/.importlinter` and `pyproject.toml`.
- Fails CI on: `core → app`, `core → routers`, `core → services`, `core → middleware`, `core → parsers`, `core → sync`, `core → models`, `core → db`, `core → deps`, `core → tools`, `core → main`, `core → scheduler`, `core → eval`, `core → stores`, `core → agents` (top-level bridge — now an empty dir but the rule stays).
- No layering exceptions. The former `utils.data_sources` narrow exception was resolved by the 2026-04-20 sprint: the package moved to `app/data_sources/` and `authoritative_verify` now receives the registry via dependency injection (see `set_data_source_registry()` wired from `app/main.py`).

## Sparse retrieval

Cerid runs three independent retrievers and fuses their rankings via
N-way Reciprocal Rank Fusion:

1. **Dense bi-encoder** — ChromaDB vector search (existing).
2. **BM25** — `core/retrieval/bm25.py` per-domain inverted index.
3. **SPLADE++ learned-sparse** — `core/retrieval/sparse.py` encoder +
   `core/retrieval/sparse_index.py` per-domain inverted index. Default
   OFF. Enabled via `RETRIEVAL_SPARSE_ENABLED=true` AND
   `HYBRID_FUSION_MODE=tri_rrf`. The Settings PATCH endpoint sets both
   atomically when the user flips the sparse toggle on.

The fusion happens in `core/agents/query_agent.py` — when `tri_rrf` is
active, sparse runs in `asyncio.gather` with BM25, and `rrf_fuse` is
called with three rankings + three weights
(`HYBRID_RRF_{VECTOR,BM25,SPARSE}_WEIGHT`). Zero-cost when the flag is
off: no encoder load, no JSONL probe.

`core ↛ app` is preserved — neither sparse module imports anything
under `app/`. The recommendation engine that surfaces the sparse
toggle to the user lives in `core/config/recommendations.py` (pure
declarative registry) consumed by `app/processor/jobs/config_recommender.py`
which writes to Redis and is served back via `/health.recommended_features`.

## Inference routing

Every inference workload has a tiered dispatch chain.  The active
provider is observable at `GET /health.inference_routing`.

```
LLM chat / generation
  └─ call_internal_llm(stage=...)
      ├─ INTERNAL_LLM_PROVIDER=quenchforge → /api/chat on QUENCHFORGE_URL
      ├─ INTERNAL_LLM_PROVIDER=ollama      → /api/chat on OLLAMA_URL
      └─ else                              → OpenRouter /v1/chat/completions

Dense embeddings
  └─ OnnxEmbeddingFunction.__call__
      ├─ EMBEDDINGS_PROVIDER=quenchforge → /v1/embeddings (AMD GPU)
      ├─ cfg.provider=fastembed-sidecar  → /embed (CoreML / CUDA)
      └─ else                            → in-process ONNX (CPU)

Cross-encoder reranking
  └─ _rerank_cross_encoder
      ├─ RERANK_PROVIDER=quenchforge     → /v1/rerank (AMD GPU)
      ├─ cfg.provider=fastembed-sidecar  → /rerank (CoreML / CUDA)
      └─ else                            → in-process ONNX (CPU)

SPLADE++ sparse
  └─ core.retrieval.sparse.encode_batch
      ├─ cfg.provider=fastembed-sidecar  → /encode/sparse (CoreML / CUDA)
      └─ else                            → in-process ONNX (CPU)
  No Quenchforge — upstream gateway has no sparse endpoint.

NLI verification
  └─ core.utils.nli.nli_score
      └─ in-process ONNX (CPU — providers=["CPUExecutionProvider"])
  No sidecar, no Quenchforge — only GPU path on AMD Mac is none.
```

Provider selection is env-driven so a PATCH /settings flip takes
effect on the next request without restart.  The four `QUENCHFORGE_*`
env vars (URL + three model names) are the operator's surface; the
`docs/AMD_GPU_MODEL_RECOMMENDATIONS.md` matrix picks GGUFs by VRAM
tier.

Three workloads stay CPU on Intel Mac + AMD even with Quenchforge
configured:

* **SPLADE sparse encoding** — Quenchforge has no sparse endpoint
  (verified against the routing table in upstream `gateway.go`).
* **NLI verification** — no GPU path exists in cerid's stack today.
  Adding NLI to cerid's own sidecar would unlock CoreML / CUDA but
  still leave Intel Mac + AMD on CPU.
* **`:online` web-search claim verification** — OpenRouter-specific
  feature.  Quenchforge has no web-search proxy.

**External-source latency budget.** `/agent/query` may consult external
knowledge sources (e.g. DuckDuckGo) on the hot path. Each such call is bounded
by `EXTERNAL_SOURCE_QUERY_TIMEOUT = 2.0` (`src/mcp/config/constants.py`) so a
hung source can't blow the cold-start SLO — before this cap a single slow
source serialized a ~5 s wait into the first-touch response until its circuit
opened. The orchestrator adds a small outer margin on top of the per-source
budget.

## Observability contract

The canonical endpoint is `GET /health`. Every observability signal must appear in `/health.invariants`:

- `healthy_invariants: bool` — criticality gate, flips HTTP to 503 on failure
- `nli_model_loaded: bool` — hard gate, verification depends on it
- `verification_report_orphans: int` — Neo4j drift signal, m0002 keeps it at 0
- `collections_empty: [str]` — observability-only, empty domains
- `internal_modules: dict[str, bool]` — build identity flags (public vs internal distribution)
- `swallowed_errors_last_hour: {module: int}` — `log_swallowed_error` counter

Resist adding health signals anywhere else. The preservation harness (I1) enforces field presence.

## Version contract

Served by `/`, `/health`, and `/openapi.json`. Single source of truth: `pyproject.toml` via `core/utils/version.py::get_version()`. Docker builds require `make version-file` to write a stubbed VERSION before `docker build` — otherwise the container returns the 0.0.0 fallback. `scripts/start-cerid.sh --build` calls `make version-file` automatically.

## Where things live (quick index)

| Question | Where |
|---|---|
| What does `/agent/query` return? | [`docs/API_REFERENCE.md`](API_REFERENCE.md); preservation I2 |
| What's the canonical claim shape? | `src/mcp/core/agents/hallucination/models.py` |
| How do I add a new route? | Write in `src/mcp/app/routers/`; regenerate `docs/ROUTER_REGISTRY.md` |
| Where does internal code live? | `*_internal.py` files listed in `.sync-manifest.yaml` |
| How does the sync work? | [`docs/SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md) |
| What must not break? | [`docs/PRESERVATION.md`](PRESERVATION.md) I1-I8 |
| What are the project conventions? | [`docs/CONVENTIONS.md`](CONVENTIONS.md) |
| What's resolved / shipped? | [`docs/COMPLETED_PHASES.md`](COMPLETED_PHASES.md) |
| Current sprint work? | `tasks/todo.md` |
| Sidebar pane shape + redirect map? | [`docs/UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) |
| Atlas + Constellation perf budgets? | [`docs/PERF_BUDGETS.md`](PERF_BUDGETS.md) |
| Visualization endpoints? | `/graph/decomposition`, `/graph/map` (`?layout=`), `/graph/domains`, `/graph/neighborhood`, `/graph/timeline/strata`, `/graph/tour/generate`, `/atlas/views/*` |
| How are entity domains assigned? | § Domain backbone (below); `DeriveDomainsJob`, `GET /graph/domains` |
| How do connectors work? | `core/ingest/sources/base.py` + `core/ingest/sources/registry.py`; one connector module per `kind` under `core/ingest/sources/connectors/` |
| How does the webhook receiver dispatch by provider? | `core/ingest/adapters/` recipes; `(kind, provider)` index resolves `kind=webhook + config.provider=slack` → `chat_capture/slack` recipe |
| Where's the Source-management REST surface? | `src/mcp/app/routers/sources.py` (list / kinds / create / test / policy / webhook-url / delete) |

## Visualization tier (Cerid v1.0)

Sidebar consolidates to 4 panes (Chat / Subjects / Sources / Settings). The
Subjects pane hosts four visualization modes (Atlas / Constellation / Timeline
/ Wiki), reworked across the 2026-06 eval cycles (TRELLIS / Tephra / FOLIO /
STRATA):

- **Atlas (STRATA, Cycle 4)** — default mode is now a DOM **decomposition
  icicle**: domains → L1 → L0 communities → entities, backed by
  `GET /graph/decomposition` (with `?community=<id>` for the leaf walk). (The
  intermediate "subcategory group" tier was removed 2026-07-02 — it replicated
  the full community list under every subcategory, and the client flattens it
  anyway, so `/graph/decomposition` now emits a flat `communities` list per domain.) The old ego-network view is demoted to an explicit
  **Neighborhood** leaf mode (hops promoted to ≤2), backed by
  `GET /graph/neighborhood`. Fresh installs degrade to a Domain→Entity two-tier
  via the `no_communities_computed` flag.
- **Constellation (Cycle 4)** — 2D-in-3D cartographic map backed by
  `GET /graph/map?layout=force|wells|domain` (per-layout cache keys,
  `layout_fallback`, 422 on invalid). Adds **drag-heal** interactivity
  (`lib/graph/interactions/drag-heal.ts` — critically-damped lerp-home,
  neighbor falloff, interruptible, `reduced-motion` snap), a layout switcher,
  3D token restyle (neon → opt-in Ambient), z-axis recency, and tour mode
  (`POST /graph/tour/generate`, Pro-gated).
- **Timeline (Tephra, Cycle 2)** — stratigraphic timeline backed by
  `GET /graph/timeline/strata` (+ lazy `…/track/{id}?bucket=`): event-horizon
  strip, since-you-last-looked band, domain lanes with summary-derived labels,
  pre-ledger hairline (`ledger_start_date`).
- **Wiki (FOLIO, Cycle 3)** — Vector-2022 encyclopedia anatomy; see § Knowledge
  architecture and `GET /wiki/{entities,entities/{slug},concepts,log,index}`.
- **Lenses + saved views** — lens transforms (contradiction, open-question,
  provenance, quality, **Domain**, **Trust**) compose via sigma's
  nodeReducer/edgeReducer. The Trust lens reads `Entity.trust_state` from the
  nightly `compute_trust_state` job. Per-user named view CRUD via
  `/atlas/views/*`; saved-view schema is **v3** (adds `atlasTier`).

See [`docs/UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) for the full pane shape,
NavigationProvider redirect map, and component layout reference, and
[`docs/BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md) § Knowledge-graph nightly jobs
for the jobs that feed these surfaces.

## Domain backbone (TRELLIS, Cycle 1)

Entities carry a derived domain spine so every Subjects surface can group,
colour, and filter by domain consistently:

- **Per-entity fields** (written by `DeriveDomainsJob`): `primary_domain`,
  `domain_mix` (JSON, sorted desc by count then name), `primary_subcategory`,
  `domains_updated_at`. Primary selection uses a 4-rung tie-break
  (count → non-`general` → recency → lexicographic). Orphan entities have the
  domain fields `REMOVE`d. See [`docs/BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md)
  § Knowledge-graph nightly jobs.
- **Rollup endpoint:** `GET /graph/domains` — per-domain entity/artifact
  counts; `derived_at: null` means the job has never run.
- **Frontend colour:** a stable `domainSlot(domain)` hash (salt 796,
  collision-free for the canonical 12) in `lib/graph/identity.ts` maps each
  domain to a `--color-domain-0..11` CSS token (plus an `other` token). The old
  hard-coded `DOMAIN_BADGE_COLORS` map was deleted.

## Ingestion architecture

Every ingestion stream is a `(:Source)` node in Neo4j with a `kind`
from one of 22 supported kinds (11 Core + 11 Pro) across 9 families
(files / feeds / chat / mail / calendar / media / webhook / adapter /
pack). Connectors are protocol objects under
`core/ingest/sources/connectors/`; each implements the four
lifecycle methods of `core.ingest.sources.base.SourceConnector`:

| Method | When called |
|---|---|
| `connect(config) → ConnectResult` | Once per source — validates config, performs one-time setup (OAuth callback, watch handle, …), returns initial cursor + connection_time_ms |
| `fetch_since(source_id, cursor, config)` | Driven by the `source_poll` scheduler worker (`SCHEDULE_SOURCE_POLL`, rss/url_watch); async-iterates `SourceArtifactEvent`, persisting `cursor_after` after each ingested artifact (crash-safe). `config` carries the feed url/domain so core stays app-import-free. |
| `health_check(source_id, config)` | Cheap probe; surfaces on the source-detail pane and `/observability/connector-health` |
| `disconnect(source_id, config)` | Cleanup — OAuth revocation, watch teardown, daemon stop. Idempotent |

`health_check` and `disconnect` take `config` alongside `source_id`
so connectors stay inside the `core → app` import contract — the
router owns the Neo4j round-trip.

### Source kinds + tiers

11 Core kinds: `folder`, `bookmarks`, `rss`, `url_watch`, `webhook`,
`chat_capture`, `dev_events`, `clipboard`, `voice_note`,
`external_adapter`, `knowledge_pack`.

11 Pro kinds: `gmail`, `outlook`, `google_calendar`,
`outlook_calendar`, `meeting_audio`, `apple_notes`, `apple_mail`,
`imessage`, `apple_calendar`, `apple_photos`, `apple_reminders`.

Single source of truth: `core/ingest/sources/kinds.py` (Literal +
KIND_FAMILY + KIND_TIER maps; import-time asserts enforce drift).

### Sync cursor service

`app.services.sync_cursor` — Redis-first hot reads (sub-ms), Neo4j
fallback + cache warm on miss. Writes go to BOTH stores so a Redis
flush loses at most the last in-flight cursor. Cursor shape is
connector-defined; the service treats it as opaque JSON.

### Webhook receiver + adapter recipes

Inbound HTTP traffic lands at `POST /sdk/v1/ingest/webhook/{token}`.
The receiver:

1. Resolves the `(:Source)` by token (constant-time compare, kind
   filter to `webhook` for security).
2. Optionally verifies `X-Cerid-Signature: sha256=<hex>` against the
   source's `hmac_secret`.
3. Parses JSON body.
4. Looks up an **adapter recipe** by `config.provider` via the
   `core.ingest.adapters` registry's provider→canonical-kind index.
   13 recipes ship: Slack / Discord / Teams / Matrix (chat_capture),
   GitHub / Linear / Sentry / Stripe (dev_events), Readwise / Pocket /
   Instapaper / Raindrop / Telegram (external_adapter). Each recipe
   normalizes the provider-shaped payload into one or more
   `CanonicalArtifact` records.
5. Enqueues the (raw + normalized) payload to
   `cerid:webhook_inbox:{source_id}` for the ingest worker.

Returns 202 immediately. The token is the routing credential; the
canonical destination kind comes from the recipe — `kind=webhook`
stays the security boundary, recipes provide the routing flexibility.

### Knowledge Stats + sparklines

`GET /observability/knowledge-stats` returns five orthogonal corpus
dimensions (artifacts, chunks, entities, edges-total, source-kinds
diversity) in a single Cypher round-trip; Redis-cached 60s.
`GET /observability/knowledge-stats/history?days=N` returns daily
snapshots for sparkline rendering. The nightly snapshot scheduler
(`SCHEDULE_KNOWLEDGE_STATS_SNAPSHOT`, default midnight UTC) MERGEs
one `:KnowledgeStatsSnapshot` per day.

### Per-source policies

Each source carries:

- `retention_policy: { mode: "keep_all" | "days" | "count", … }`
  applied nightly by `SCHEDULE_RETENTION_ENFORCE` via
  `core.ingest.retention.plan_for_source` + `app.services.retention.apply_retention_plan`.
  **Retention is opt-in.** `create_source()` defaults every new source to
  `mode: "keep_all"`, and `enforce_all_retention()` skips `keep_all`
  sources entirely — nothing is purged unless an operator explicitly
  sets a `"days"` or `"count"` policy via `POST /sources/{id}/policy`.
  The nightly pass logs how many sources it skipped for this reason.
- `quality_floor: float [0.0, 1.0]` — artifacts with a computed
  quality_score below the floor are dropped before chunking +
  embedding. Lookup is memoized per-source in
  `app.services.quality_floors`.

Both edit through `POST /sources/{id}/policy`.

### OAuth flow (Pro cloud connectors)

`app.routers.oauth` exposes start + callback endpoints for Google
(Gmail + Calendar) and Microsoft (Outlook + Calendar). Redis-backed
state tokens with 10-minute TTL and single-use semantics. Token
exchange against the upstream providers is configuration-driven via
the sibling MCP servers (`google_workspace`, `ms365`).

### Browser extension + Apple ecosystem

`packages/extension/` is a Manifest V3 browser extension — popup
with Save Page → readability extraction → `POST /sdk/v1/ingest`.
Works on Chrome + Firefox; Edge + Safari deferred.

`packages/desktop/swift/CeridMail/` + `CeridReminders/` are TCC-
scoped Swift helper binaries. The Python connector at
`core/ingest/sources/connectors/apple_mail.py` subprocesses to
`ceridmail`; `ceridreminders` is driven only by the desktop bridge
(`packages/desktop/.../apple_reminders.ts`) — the backend reminders
plugin was removed 2026-08-12 because a Linux container can never run
a macOS binary. Health-check reports helper-binary availability
on the host's PATH.

## Pro tier (Cerid v1.0 Phases D-H)

The Pro feature surface sits atop the visualization tier as three
architectural additions:

### Desktop-host connectors (Phase D)

The Electron main process (`packages/desktop/src/main/`) reads
on-disk Apple data directly via Node.js, then POSTs structured
payloads to `/ingest/structured`. Three connectors in this shape:
Apple Notes (gzipped protobuf via `better-sqlite3` + `protobufjs`),
Apple Mail (`.emlx` parser walking V10/MailData), iMessage (chat.db
+ minimal NSKeyedArchiver typedstream decoder). FDA + per-category
TCC grants surfaced via `permissions-step.tsx` (`node-mac-permissions`
≥ 2.5 + Electron's `systemPreferences`).

### Sibling MCP servers (Phase F)

`stacks/connectors/docker-compose.yml` brings up two opt-in
profile=pro services — `google-workspace-mcp` (taylorwilsdon v1.21.0)
and `ms365-mcp` (Softeria v0.111.0, pinned by commit SHA because of
its high release velocity). Both speak streamable-HTTP MCP on
loopback with a static bearer (`CERID_CONNECTORS_BEARER`). Cerid
backend uses `MCPClientPool` with per-connector headers; the sibling
servers own OAuth + refresh-token rotation. Four plugin
ConnectorPlugins (`gmail`, `google_calendar`, `outlook`,
`outlook_calendar`) wrap their MCP tool surface behind
`DataSource` subclasses, joining the standard `query_all` fan-out.

### Native Swift helpers (Phase G)

`packages/desktop/swift/` ships five SPM CLI executables built via
`swift build` (no Xcode `.xcodeproj` needed): `ceridek` (EventKit),
`ceridphotos` (PhotoKit metadata), `ceridreminders` (EventKit
reminders), `ceridmail` (Mail.app), and `ceridspotlight`
(CoreSpotlight donor). Python plugins (`src/mcp/plugins/apple_calendar`,
`src/mcp/plugins/apple_photos`, `src/mcp/plugins/apple_mail`) invoke
`ceridek`, `ceridphotos` and `ceridmail` via
`asyncio.subprocess` and parse JSON-over-stdio; `ceridspotlight` is
driven from the desktop main process instead
(`packages/desktop/src/main/connectors/spotlight.ts`), because
CoreSpotlight indexes the host, not the container. TCC grants inherit
from the parent Electron app's signed bundle — load-bearing contract
documented in `packages/desktop/swift/README.md`.

**Spotlight retention.** A donated item is knowledge-base content living in a
macOS index outside the app, so it needs a way out. Two, in fact: every item
carries an `expirationDate` (`expiration_days`, operator-configurable in
Settings → Extensions → Spotlight, 90 days by default, `0` for never), and the
main process sweeps the whole domain at launch when `spotlight_donation` is no
longer entitled (`purgeIfUnentitled`). The expiry window is also the only answer
to uninstall — dragging an `.app` to the Trash runs no code, so nothing can
purge on the way out. Until 2026-08-11 neither existed: the field was in the
helper's input schema from the day it was written and the donor never sent it,
and `purgeSpotlight` had one call site, a button in Settings.

The sweep distinguishes three states, not two. A capabilities response that
cannot be read is `skipped`, never folded into either answer: read as
"entitled" it leaves a lapsed customer's knowledge base searchable forever;
read as "unentitled" a backend that is merely still booting wipes a working
index on every launch.

Until 2026-08-10 this section said "three" and pointed at
`plugins/apple_calendar` / `plugins/apple_photos`. The count predates
`ceridreminders` and `ceridmail`, and the Apple plugins have never lived
under the top-level `plugins/` tree — that tree exists and holds the
BUSL-1.1 Pro plugins, so the wrong path leads somewhere real and empty
rather than to an obvious error.

The three Xcode-required native targets (App Intents, Share
Extension, Quick Look) are deferred — see
`docs/PHASE_G_DEFERRED.md`.

### Calendar stitching fallback chain

`meeting_capture.calendar_stitch.match_to_event` (async since Phase F)
resolves calendar events from the first available source in this
order: `google_calendar` → `outlook_calendar` → `apple_calendar`
(Swift helper) → `apple_calendar_eventkit` (legacy). Documented in
`docs/PRO_GOOGLE_CALENDAR.md`.

### Privacy filter

`utils/domain_privacy.py` enforces per-domain visibility floors
against the active `private_mode` level. Currently:
`messages`/`imessage` require Level 2+. Wired into
`pkb_search_filtered` so iMessage content disappears from retrieval
when the floor isn't met. Privacy-defaults to closed on Redis
unavailability.

### Metamorphic verification (Phase H)

`plugins/metamorphic/plugin.py` extends the hallucination pipeline
with per-claim metamorphic scoring: each factoid gets synonym +
antonym mutations via the internal LLM, then heuristic entailment
checks classify it as `ok` / `suspicious` / `likely_hallucinated`.
Registered via the existing `set_metamorphic_handler` stub
interface in `app/agents/hallucination/metamorphic.py`.

See [`docs/COMPLETED_PHASES.md`](COMPLETED_PHASES.md) for the
Phase D-H cumulative metrics and per-phase shipping log.

## Knowledge architecture (Cerid v1.0 Phases K1-K6)

The K-program turns the four primitives (vectors / graph / wiki /
episodic memory) into an integrated architecture inspired by
Karpathy's LLM Wiki pattern, Palantir Ontology-Augmented Generation,
and A-Mem agentic memory.

### Four knowledge surfaces

| Surface | Primary key | Cost profile |
|---|---|---|
| **W**iki | `entity_slug` | Read-cheap (1 Neo4j round-trip), write-batched |
| **V**ector | `chunk_id` | Read-medium (50-200ms Chroma + rerank), write-incremental |
| **G**raph | `(entity, edge)` | Read-cheap (20-100ms Cypher), write-incremental |
| **M**emory | `memory_id` | Read-medium, write-explicit + decay-scored |

The surfaces are orthogonal; a query can hit two or three. The
top-level surface router decides.

### Surface router (Phase K3)

`core/retrieval/surface_router.py` classifies user queries into
five intent buckets via regex-only fast path (~0.5ms/query):

| Intent | Detection signal | Primary surface |
|---|---|---|
| `compiled_summary` | "what is X / who is X / tell me about Y" | wiki |
| `specific_fact` | quoted spans, "find the X where Y" | vector |
| `relational` | "how does X relate to Y / what connects" | graph |
| `personal_context` | "what did we decide / I prefer" | memory |
| `mixed` (fallback) | no regex match | vector + graph + wiki |

Precedence: `personal_context` > `specific_fact` > `relational`
> `compiled_summary`. Personal-context first because "what did we
decide about X" must NOT route to wiki even though X looks like a
summary target.

Exposed as `pkb_surface_route` (MCP tool) and consumed inside
`pkb_agent_query` (optional `surfaces=[...]` arg) and
`pkb_answer_with_citations` (wiki page prepended to context budget
when W surface fires).

### Event hooks + compounding loop (Phase K1)

`app/processor/event_hooks.py` is a lightweight in-process pub/sub
that wires the ingest path to the wiki refresh path without
coupling the two jobs:

```
ingest_content() -> Neo4j commit + Chroma flip
    -> EntityExtractionJob (enqueued post-commit)
        -> upsert entities + MENTIONS edges
        -> emit "entities_added" event
            -> wiki_refresh subscriber (with per-entity Redis debounce)
                -> WikiRefreshJob (enqueued)
                    -> generate prose summary via local LLM
                    -> write_entity_summary + external enrichment
                    -> emit "wiki_refreshed" knowledge log entry
```

Failure isolation: each subscriber's exceptions are caught by the
dispatcher so a broken handler can't break the emitter.

Three freshness loops feed back into the queue:

1. **On-write debounced refresh** (Phase K1.3) — per-entity Redis
   debounce (5min TTL) prevents bulk-ingest write amplification.
2. **Nightly stale-sweep** (Phase K1.4) — 3 AM cron picks the top
   `WIKI_STALE_SWEEP_LIMIT` (default 100) entities with
   `summary_updated_at < now()-24h` ordered by mention_count.
3. **Weekly drift lint** (Phase K2.4) — Sunday 4 AM scans for
   unresolved contradictions on stale summaries (force refresh)
   + high-mention coverage gaps (debounced refresh).

### Karpathy log + index (Phase K4)

`(:KnowledgeLog)` Neo4j label is an append-only ledger written by
`WikiRefreshJob.on_success` — Karpathy's `log.md` equivalent.
`GET /wiki/log` paginates by entity/since. `GET /wiki/index` is a
Karpathy-shaped catalog (slug + one-liner + activity_score +
has_summary) that the surface router consults when fuzzy name
matching misses.

### Cross-surface linking (Phase K2)

Per Palantir's OAG influence — the graph is the typed cross-
reference layer; every other surface references entities by
`canonical_id`:

| From | To | Edge |
|---|---|---|
| Memory artifact | Entity | `(:Artifact {memory_type})-[:MENTIONS]->(:Entity)` |
| Wiki page | Memory | `WikiEntityPage.episodic_memories` (decay-scored) |
| Wiki page | Contradiction | `(:Entity)-[:HAS_CONTRADICTION]->(:ContradictionFinding)` |
| Conversation | Wiki touch | `EXTRACTED_FROM` edge + `entities_added` event |

### Graph edge model (hybrid: co-mention + semantic)

The knowledge graph carries two relationship types between `(:Entity)` nodes:

| Relationship | How built | Cypher label |
|---|---|---|
| Co-occurrence | Entities that appear together in the same artifact chunk are linked with a weight proportional to co-mention frequency. Written by the ingestion pipeline. | `CO_MENTIONED {weight}` |
| Semantic similarity | Nightly kNN over per-entity embeddings (mean-pooled from their MENTIONS chunk vectors, quenchforge name-embed fallback). Top-k cosine neighbours above a threshold. Written by `build_similarity_edges` (`app/db/neo4j/semantic_edges.py`). | `SIMILAR_TO {score}` |

**Default graph view** excludes degree-0 isolated nodes (entities with no `CO_MENTIONED` or `SIMILAR_TO` edge). Endpoints `/graph/map`, `/graph/embeddings/3d`, and `/graph/neighborhood` accept `include_isolated=false` (default) and return `isolated_count` for the toggle label. Isolated entities carry `community_id='isolated'` so they never receive a Leiden community colour.

Link tuples in the API are 4-tuples `[src_idx, tgt_idx, weight, kind]` where `kind` is `"co_mention"` or `"similar"`. The force layout (`compute_umap_3d`) uses both relationship types; semantic edges are down-weighted by `SEMANTIC_EDGE_SPRING_SCALE` (default 0.6) so co-mention structure stays dominant. The `force` layout (2026-07-02 re-tune) warm-starts from a Vogel/sunflower disc-fill seed and confines nodes with **strong/harmonic gravity** (`UMAP_FORCE_GRAVITY=0.08`) so the map fills a disc instead of a hollow ring; a weaker **domain-centroid cohesion** spring (`UMAP_FORCE_DOMAIN_PULL=0.3`), layered under the Leiden community pull, gathers same-domain communities into macro-regions. The nightly cadence is: `compute_entity_embeddings` (03:15) → `build_similarity_edges` (03:22) → `compute_umap_3d` (03:30).

### Observability (Phase K6)

`/health.wiki_freshness` returns six metrics in one Cypher round-
trip: total/active entity counts, coverage %, unresolved
contradictions, 24h log activity. Surfaces in Settings →
Diagnostics → Analytics via `KnowledgePanel`. Preservation
invariants (`tests/test_knowledge_architecture_invariants.py`)
gate the wiring against regression.

See [`docs/COMPLETED_PHASES.md`](COMPLETED_PHASES.md) for the
Phase K1-K6 cumulative metrics and the
`tasks/2026-05-22-knowledge-architecture-redesign.md` design doc
for the strategic rationale.
