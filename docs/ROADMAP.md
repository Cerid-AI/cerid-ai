# Cerid AI — Development Roadmap

> **Last updated:** 2026-03-30
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

### Startup Wizard & Setup Streamlining
- Web-based first-run wizard (runs before main app if no config detected)
- Docker detection and auto-install guidance
- API key setup with provider validation
- Ollama install option integrated into wizard
- Remove age encryption requirement for first-time setup
- Pre-built Docker images on GitHub Container Registry (skip local build)
- **Goal:** Actual quick setup for Docker-ready users

### Verification Health Check
- Add verification pipeline self-test at startup (fire a test claim)
- Show verification status in health dashboard
- Alert on consecutive extraction failures
- Prevents the silent model routing failure from recurring

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

### Conversation Management UX
- Hover-reveal action buttons on sidebar conversations (delete, archive)
- Archived conversations view toggle
- Bulk select/delete for history cleanup
- Search within conversation history

### Agent Communication Console
- Optional real-time console panel showing agent activity
- Ticker-scroll with humanized agent messages (see Agent Personality below)
- Color-coded by agent, filterable, collapsible/dockable
- Transparency into the intelligence pipeline

### Model Management & Auto-Update Detection
- Dedicated model management pane in Settings
- All available models with: provider, cost, context window, capabilities
- Auto-detection of new model releases via OpenRouter API polling
- Notification badge when new models available
- One-click model swap with capability comparison
- Deprecation warnings for outdated models
- Prevents casual users from being stuck on previous-generation models

### Direct Provider SDKs
- Direct API key support for Anthropic, OpenAI, Google (bypass OpenRouter)
- Provider selection in settings: OpenRouter (default) vs direct API
- Key validation per provider, preserves OpenRouter as fallback

### Pro Tier Purchase Path
- Stripe integration for Pro tier licensing
- License key validation endpoint
- Self-serve upgrade flow Core → Pro in Settings
- Waitlist/early access program as interim

### Pro Mode Configuration & Feature Access
- Clear Pro settings pane showing all Pro-gated features with status
- Feature discovery: show what Pro unlocks with preview
- License key entry and validation
- Visual distinction between Core and Pro UI elements

---

## P2 — Medium Priority

### External APIs in Knowledge Console
- Show enabled APIs with status in Knowledge Console external section
- Inline enable/disable toggles per API
- Add free public APIs: DuckDuckGo Instant Answers, Open Library, PubChem
- Move data sources to more prominent Essentials location

### Watched Folders Management
- CRUD API for watched folders with Redis storage
- Per-folder: enable/disable, domain override, exclude patterns, search_enabled toggle
- Per-folder scan isolation (namespaced Redis state keys)
- Settings UI: folder list with toggles, scan button, stats

### RAG Orchestration Resilience
- Source availability awareness before querying
- Folder-aware domain routing in decomposer
- Graceful degradation: partial source_breakdown on failure
- Per-source timing and status in response

### Expanded File Type Handling
- Specialized parsers for code (AST extraction for Python, JS/TS, Go, Rust)
- Table-aware Excel parsing (preserve sheet structure)
- Image OCR for scanned PDFs (Pro tier)
- Audio transcription for meeting notes (Pro tier)
- Markdown frontmatter extraction (YAML/TOML headers → metadata)

### Separate Trading Tools
- Move 5 trading MCP tools to cerid-trading-agent repo
- Core Cerid ships 21 MCP tools (no trading dependency)
- Trading agent connects via A2A protocol or SDK endpoints

### Bulk Import Remaining Features
- Ollama content triage (score 1-5 for value assessment)
- Persistent import queue (Redis-backed, survives restarts)
- File type error recovery (magic byte sniffing, fallback parsing)
- Import progress in Knowledge Console UI
- Scheduled folder re-scan (cron-based watch)

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

### Code Quality Improvements
- File decomposition: split files >500 lines into focused modules
- Type hints on all public APIs, mypy strict mode
- Module documentation (MODULE.md per module)
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
