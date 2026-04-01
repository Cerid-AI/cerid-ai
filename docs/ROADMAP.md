# Cerid AI — Development Roadmap

> **Last updated:** 2026-03-31
> **Source of truth:** This file consolidates ALL planned work from `tasks/todo.md`, phase plans, and market analysis.
> **For completed work:** See `docs/COMPLETED_PHASES.md`

---

## Priority Legend

- **P0 — Blocker:** Must fix before any public release
- **P1 — High:** Critical for product-market fit
- **P2 — Medium:** Important for growth and retention
- **P3 — Low:** Nice-to-have, quality-of-life

---

## P0 — Blockers

_All P0 items completed. See `docs/COMPLETED_PHASES.md`._

---

## P1 — High Priority

### Private Mode (Ephemeral Sessions)
- Toggle in toolbar: "Private Mode" — nothing remembered, nothing saved
- 4 configurable security levels:
  1. No history, no memory extraction
  2. Also disable KB context injection
  3. Also force local-only models (Ollama)
  4. Also clear Redis query cache on session end
- Visual lock indicator, session data wiped on close

### Conversation Management UX (Remaining)
- ~~Hover-reveal delete button~~ (done)
- Archived conversations view toggle
- Bulk select/delete for history cleanup
- Search within conversation history

### Agent Communication Console
- Optional real-time console panel showing agent activity
- Ticker-scroll with humanized agent messages (see Agent Personality below)
- Color-coded by agent, filterable, collapsible/dockable
- Transparency into the intelligence pipeline

### Model Management & Auto-Update Detection (Remaining)
- ~~Ollama model management with hardware-aware recommendations~~ (done)
- Auto-detection of new model releases via OpenRouter API polling
- Notification badge when new models available
- Deprecation warnings for outdated models
- Cost comparison view: current model vs alternatives

### Pro Tier Purchase Path
- Stripe integration for Pro tier licensing
- License key validation endpoint
- Self-serve upgrade flow Core → Pro in Settings
- Waitlist/early access program as interim
- **Pro anchor feature:** Audio transcription (meeting notes, interviews, lectures) — the primary differentiator that justifies Pro over Core

### Pro Mode Configuration & Feature Access (Remaining)
- ~~Feature flag system + tier gating decorators~~ (done)
- ~~Runtime tier cycle button in sidebar~~ (done)
- Clear Pro settings pane showing all Pro-gated features with status
- Feature discovery: show what Pro unlocks with preview
- License key entry and validation

---

## P2 — Medium Priority

### Expanded File Type Handling (Remaining)
- ~~Table-aware Excel parsing~~ (done)
- ~~PDF table extraction~~ (done)
- Specialized parsers for code (AST extraction for Python, JS/TS)
- Image OCR for scanned PDFs (Pro tier)
- Audio transcription for meeting notes (Pro tier)
- Markdown frontmatter extraction (YAML/TOML headers → metadata)

### Bulk Import Remaining Features
- Ollama content triage (score 1-5 for value assessment)
- Scheduled folder re-scan (cron-based watch)

---

## Ingestion Pipeline Evolution (Phases 53-57)

> Full plan: [`docs/plans/PLAN_INGESTION_PIPELINE_EVOLUTION.md`](plans/PLAN_INGESTION_PIPELINE_EVOLUTION.md)

### P1 — Pipeline Hardening (Phase 53)
- Dead-letter queue, BM25 rollback, triage→ingest bridge, per-file status

### P1 — Core Data Sources (Phase 54)
- IMAP email, RSS feeds, browser bookmarks, inbound webhooks, clipboard, macOS Quick Actions

### P1 — Pro Data Sources (Phase 55)
- Gmail OAuth, Outlook Graph, Apple Notes, Calendar sync, Docling parser

### P2 — Storage Dashboard (Phase 56)
- Storage metrics, usage bars, persistent history, activity feed

### P2 — KB Interface Refresh (Phase 57)
- Live progress, source badges, previews, near-duplicate merge, quality visualizations

---

## P3 — Low Priority / Future

### SSO / SAML Implementation (Enterprise)
- SAML 2.0 SP with IdP metadata import
- Common IdPs: Okta, Azure AD, OneLogin
- Tenant-scoped SSO configuration
- Currently scaffolded as feature flag only

### Enterprise Feature Scaffolding
- All Vault features get endpoint stubs returning 403 with upgrade message
- UI placeholders showing "Available in Cerid Vault"
- Scaffolded: SSO/SAML, advanced audit logging, SIEM export,
  tenant management UI, compliance reporting, dedicated support portal
- Actual implementation deferred to enterprise development phase

### Repo Maintenance
- Marketing site separated to `Cerid-AI/cerid-ai-marketing` (migration script at `scripts/separate-marketing.sh`)

### Code Quality Improvements
- Type hints on all public APIs, mypy strict mode
- Parent-child hierarchical RAG (currently feature-flagged off)
- Graph RAG with entity extraction and query rewriting

---

## Agent Personality Design (for Communication Console)

Each agent has a distinct voice used in the communication console ticker:

| Agent | Personality | Example Messages |
|-------|------------|------------------|
| **Query** | Confident coordinator, takes charge | "On it — searching 3 domains for you..." · "Found 8 strong matches, assembling context." |
| **Decomposer** | Analytical, methodical | "Breaking this down into 3 sub-questions..." · "Sub-query 2 looks like a comparison — splitting further." |
| **Assembler** | Detail-oriented craftsman | "Weaving 5 sources into a coherent answer..." · "Cutting redundant chunks — keeping the best 3." |
| **Triage** | Efficient sorter, no-nonsense | "PDF detected — 12 pages, running table extraction." · "Skipped 3 junk files. 8 ready for ingestion." |
| **Curator** | Quality inspector, discerning | "Artifact quality: 0.72 — above threshold, approved." · "Found 2 near-duplicates. Flagging for review." |
| **Rectify** | Problem solver, constructive | "Spotted a stale chunk from January — marking for refresh." · "Fixed 3 orphaned relationships in the graph." |
| **Audit** | Accountant, precise with numbers | "Today's cost: $0.0043 across 12 queries." · "Cache hit rate at 67% — good efficiency." |
| **Maintenance** | Janitor, quietly keeps things clean | "Running scheduled cleanup... 0 orphans found." · "BM25 index rebuilt for finance domain." |
| **Memory** | Thoughtful librarian, remembers everything | "Noted: you prefer dark mode in code editors." · "Interesting — this contradicts a decision from last week. Keeping both for now." |
| **Verification** | Skeptical fact-checker, trust-but-verify | "Checking 4 claims against your KB..." · "Claim 2 looks suspicious — cross-checking with GPT-4o Mini." · "3 verified, 1 uncertain. Flagged for your review." |

### Console Display Format
```
[14:32:05] 🔍 Query      → Searching finance + general domains...
[14:32:05] 🧩 Decomposer → Split into 2 sub-queries: "tax deductions" + "Montana rules"
[14:32:06] 📊 Query      → Found 8 matches (top relevance: 0.92)
[14:32:06] ✂️  Assembler  → Deduplicating... keeping 5 of 8 chunks
[14:32:07] 🛡️ Verify     → Checking 3 claims against KB...
[14:32:08] 🛡️ Verify     → Claim 1: ✓ verified (KB match, 0.91 confidence)
[14:32:09] 🛡️ Verify     → Claim 2: ⚠ uncertain — cross-model check initiated
[14:32:10] 🧠 Memory     → Noted: user interested in Montana tax law
```

### Design Principles
- Messages should feel like overhearing a competent team working together
- Never robotic ("Processing query...") — always purposeful ("Searching 3 domains for you...")
- Show progress without overwhelming (max 1 message per agent per second)
- Errors are honest but not alarming ("Verification timed out — using KB results only")
- Gold-colored messages for premium operations (Pro/Vault features)
