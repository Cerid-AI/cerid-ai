# Phase 6: React GUI + Production Hardening

> **Date:** 2026-02-22
> **Status:** Design approved — ready for implementation planning

## Problem Statement

Cerid AI's current interfaces have limitations:
1. **Streamlit dashboard** — functional but generic-looking, limited layout control, not commercially presentable
2. **LibreChat dependency** — a third-party chat UI that can't deeply integrate with KB context, monitoring, or audit
3. **No production hardening** — no API auth, no caching, no feedback loop

## Solution

Build a polished React app as the single primary interface for Cerid AI. Built-in chat via Bifrost, knowledge browser with automatic context injection, monitoring, and audit — all in a commercially viable package.

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Framework | React 18 + TypeScript | Industry standard, AI-friendly, huge ecosystem |
| Styling | Tailwind CSS + shadcn/ui | Pixel-perfect design, accessible components you own |
| Charts | Recharts | Lightweight, React-native charting |
| Data fetching | TanStack Query (React Query) | Caching, background refresh, loading/error states |
| Routing | React Router v6 | Client-side navigation between panes |
| Build tool | Vite | Fast dev server, instant HMR |
| Backend | Existing MCP FastAPI (8888) + Bifrost (8080) | No backend changes needed for 6A-6C |
| Deployment | Docker + nginx on `llm-network` | Same infra pattern as other services |

## Layout Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Sidebar (collapsed/expanded)                                │
│  ┌─────┐  ┌──────────────────────────────────────────────┐  │
│  │ Nav │  │           Main Content Area                   │  │
│  │     │  │                                               │  │
│  │ Chat│  │  ┌─────────────────┬─────────────────────┐   │  │
│  │ KB  │  │  │   Chat Panel    │  KB Context Panel    │   │  │
│  │ Mon │  │  │                 │                      │   │  │
│  │ Aud │  │  │  Messages       │  Related artifacts   │   │  │
│  │ Mem │  │  │  Input box      │  Source previews     │   │  │
│  │     │  │  │  Model select   │  Graph connections   │   │  │
│  │     │  │  │                 │                      │   │  │
│  └─────┘  │  └─────────────────┴─────────────────────┘   │  │
│           └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Status Bar: health, token usage, active model       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Panes (sidebar navigation):**
1. **Chat + KB Context** — dual-panel workspace (default view)
2. **Knowledge Browser** — searchable artifact explorer with domain filtering
3. **Monitoring** — system health, token usage charts, ingestion activity
4. **Audit** — cost tracking, query patterns, activity timeline
5. **Memories** — (future) suggestions and extracted facts

## Sub-Phases

### 6A: Foundation + Chat (ship first)

**Scope:** React app scaffold + working chat interface.

**Deliverables:**
- Vite + React + TypeScript + Tailwind + shadcn/ui scaffold
- Docker container: nginx serving built app on `llm-network`
- Port: 5173 (dev) / 80 in container, exposed as 3000 on host
- Sidebar navigation with pane switching (icons + labels)
- Chat interface:
  - Streaming responses from Bifrost `/v1/chat/completions` (SSE)
  - Model selection dropdown (Claude, GPT, Gemini, Grok, DeepSeek, Llama)
  - Conversation history (localStorage initially, migrate to MongoDB/API later)
  - Markdown rendering in messages (code blocks, tables, lists)
  - Copy button on messages and code blocks
- Status bar: system health indicator (green/yellow/red from `/health`)
- Dark/light theme toggle (persisted in localStorage)
- Responsive design (works on smaller screens)

**API integration:**
- `GET /health` — status bar
- `POST bifrost:8080/v1/chat/completions` — chat (streaming)

**Development notes:**
- Use Sonnet for implementation (React/Tailwind is well within capability)
- Use frontend-design skill for component design

### 6B: Knowledge Context Pane (the differentiator)

**Scope:** Side-by-side chat + KB context, auto-querying.

**Deliverables:**
- Resizable split-pane layout (chat left, KB context right)
- On user message: auto-query KB via `/agent/query` with the user's message
- Artifact cards in context pane:
  - Title, domain badge, relevance score (percentage)
  - Snippet/preview (first ~200 chars)
  - Click to expand full text
  - "Inject into chat" button (adds artifact context to next message)
- Manual search bar within KB context pane
- Domain filter chips (coding, finance, projects, personal, general)
- Source attribution: when chat response references KB, show inline citations
- Graph preview: show RELATES_TO connections for selected artifact

**API integration:**
- `POST /agent/query` — auto-query on chat message
- `GET /artifacts` — browse/filter
- `POST /query` — manual search

### 6C: Monitoring + Audit Panes

**Scope:** System observability and cost tracking.

**Deliverables:**

**Monitoring pane:**
- System health cards (ChromaDB, Neo4j, Redis, Bifrost — from `/health`)
- Collection size bar chart (from `/collections`)
- Ingestion timeline (from `/ingest_log`)
- Scheduled job status (from `/agent/maintain`)
- Auto-refresh every 30s (React Query refetchInterval)

**Audit pane:**
- Token usage over time chart (from `/agent/audit` activity report)
- Cost estimate breakdown by tier (smart/pro/rerank)
- Most-queried domains (from `/agent/audit` queries report)
- Recent failures list (from `/agent/audit` activity report)
- Ingestion stats: files by type, avg chunks, duplicate rate

**API integration:**
- `POST /agent/audit` — all audit data
- `POST /agent/maintain` — health/collection data
- `GET /health`, `/collections`, `/ingest_log` — monitoring data

### 6D: Backend Hardening + Feedback Loop

**Scope:** Production-quality backend improvements.

**Deliverables:**
- **Redis query caching:**
  - Cache `/query` and `/agent/query` results
  - TTL: 5 minutes for queries, invalidate on ingest
  - Cache key: hash(query + domain + top_k)
- **API authentication:**
  - API key header (`X-API-Key`) for all MCP endpoints
  - Key stored in `.env`, checked via FastAPI middleware
  - Exempt: `/health` (for Docker healthcheck)
- **LLM feedback loop:**
  - Capture chat responses from the React GUI
  - Extract code blocks, key facts, and summaries
  - Auto-ingest into KB as `domain=conversations` (new domain)
  - Toggle in settings (opt-in)
- **CORS configuration:**
  - Allow React app origin
  - Restrict in production
- **Rate limiting:**
  - Per-client rate limit on expensive endpoints (agent/*)
  - Simple in-memory or Redis-based

## Data Flow (New)

```
User → React GUI (3000) → Bifrost (8080) → OpenRouter → LLM
                        ↘ MCP Server (8888) → ChromaDB/Neo4j (KB context)
                        ↗ KB results injected into chat context panel

Chat response → [opt-in] → Extract facts → /ingest (conversations domain)
```

## Directory Structure (New Files)

```
cerid-ai/
├── src/gui/                          # Current Streamlit (kept for now)
├── src/web/                          # New React app
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── layout/              # Sidebar, StatusBar, SplitPane
│   │   │   ├── chat/                # ChatPanel, MessageBubble, ModelSelect
│   │   │   ├── kb/                  # KBContextPanel, ArtifactCard, DomainFilter
│   │   │   ├── monitoring/          # HealthCards, CollectionChart, IngestTimeline
│   │   │   └── audit/               # TokenChart, CostBreakdown, QueryStats
│   │   ├── hooks/                   # useChat, useQuery, useHealth
│   │   ├── lib/                     # API client, types, utils
│   │   └── styles/                  # Global styles, theme
│   └── public/
│       └── cerid-logo.svg
```

## Implementation Strategy

- **Sonnet for all React/TypeScript code** — well within capability, saves tokens
- **Opus for architecture decisions and design review** — complex integration points
- **Parallel subagents** where possible — e.g., component scaffolding + API client can be built simultaneously
- **frontend-design skill** for component aesthetics
- Sub-phases can each be a single focused session

## Future Phases (Not in Scope)

- **Phase 7:** Smart ingestion — fact/memory extraction, drive scanning, triage improvements
- **Phase 8:** Encryption at rest (LUKS), Tauri desktop wrapper, multi-user support
- **Memories/suggestions pane** — surfaces related knowledge proactively

## Verification

### 6A:
1. React app builds and serves from Docker container
2. Chat produces streaming responses from Bifrost
3. Model selection works across all providers
4. Dark/light theme persists across sessions
5. Status bar shows real health data

### 6B:
1. Sending a chat message auto-surfaces relevant KB results in the context pane
2. Clicking an artifact shows full preview
3. Domain filtering works
4. "Inject into chat" adds context to next message

### 6C:
1. Monitoring pane shows live health and collection data
2. Audit pane shows token usage charts and cost estimates
3. Data refreshes automatically

### 6D:
1. Repeated identical queries hit Redis cache
2. API rejects requests without valid API key
3. Feedback loop captures chat outputs into KB (when enabled)
