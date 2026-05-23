# Cerid AI — Development Roadmap

> **Last updated:** 2026-05-21 (Phases A + B + C of the Cerid v1.0 plan landed. Phase A: Atlas 2D WebGL via sigma.js v3 + custom halo NodeProgram + 4 lenses + keyboard nav + a11y + saved views + Wiki provenance + perf infrastructure (8.3ms/frame median at 1K nodes on M2 Pro). Phase B: Constellation 3D mode (R3F + InstancedMesh), UMAP backend + `/graph/embeddings/3d` endpoint, Sources pane consolidation, quick-capture FAB with ⌘⇧N. Phase C: Settings → Diagnostics tab merges Monitoring/Audit/Agents, Simple/Advanced mode toggle removed (UIModeProvider hard-pinned to advanced), knowledge-source selector in chat composer, `docs/UI_ARCHITECTURE.md` documents the final shape. **9 → 4 sidebar panes (Chat / Subjects / Sources / Settings)** — legacy panes still resolvable via NavigationProvider redirect map. ~1,220 frontend tests + 36 new backend tests passing. Phase I (Custom Smart RAG — per-source weight tuning) is next. Phases D (Apple ecosystem), E (meeting capture), F (cloud connectors via sibling MCP), G (Swift CLI helpers), and H (metamorphic verification) all shipped — see docs/COMPLETED_PHASES.md.)
> **Shipped releases:** see [CHANGELOG.md](../CHANGELOG.md) and the [GitHub releases](https://github.com/Cerid-AI/cerid-ai/releases) page.
> **Internal sprint backlog:** `tasks/todo.md` (internal-only).

---

## Priority Legend

- **P0 -- Blocker:** Must fix before any public release
- **P1 -- High:** Critical for product-market fit
- **P2 -- Medium:** Important for growth and retention
- **P3 -- Low:** Nice-to-have, quality-of-life

---

## P0 -- Blockers

_All P0 items completed._

---

## P1 -- High Priority

### ✅ Private Mode (Ephemeral Sessions) -- SHIPPED v0.84.0

Toggle in chat toolbar; 4 configurable security levels; `CERID_PRIVATE_MODE` + `CERID_PRIVATE_MODE_LEVEL` env vars wired through `features.py`, `settings.py`, `chat-toolbar.tsx`, `chat-panel.tsx`, `use-chat.ts`, `use-conversations.ts`. Visual lock indicator in toolbar. Session data wiped on close.

**Follow-up (P2):** Level 4 ("clear Redis query cache on session end") validation sweep — confirm the cache flush path works end-to-end on session close.

### ✅ Conversation Management UX -- SHIPPED v0.84.0

Archive/unarchive, bulk select/delete/archive, and conversation search all landed. Files: `src/web/src/components/chat/conversation-list.tsx` (search + archive toggles), `src/web/src/components/layout/sidebar.tsx` (bulk ops at lines 70 + 240), `src/web/src/hooks/use-conversations.ts` (archived-default migration for pre-existing records).

### ✅ Agent Communication Console -- SHIPPED v0.84.0

Real-time activity panel with humanized agent messages. Files: `src/web/src/components/agents/agent-console.tsx` (105 LOC), `agents-pane.tsx`, `agent-cards.tsx`. SSE exponential backoff with abort-on-unmount (landed in v0.83.0 bug-hunt).

### ✅ Model Management & Auto-Update Detection -- SHIPPED v0.84.0

`src/web/src/components/settings/model-management.tsx` renders "N new models available" banners and deprecation warnings. `system-section.tsx:1174+` contains the Model Updates subsection. OpenRouter catalog polling in place.

**Follow-up (P2):** Cost-comparison view (current model vs alternatives) — catalog data is already fetched; needs a UI surface in settings.

### ✅ Pro Tier Billing Infrastructure -- SHIPPED (Stripe checkout end-to-end)

Billing backend (Stripe Checkout session creation, webhook event handling across the subscription lifecycle, license-key generation/validation, waitlist, status) lives in the internal-only distribution. The Pro Settings pane (`src/web/src/components/settings/pro-section.tsx`) wires the upgrade button to the billing endpoint and opens the Stripe-hosted Checkout URL; manual license-key entry remains as a fallback for offline activation.

This shipped the **purchase path**, not the feature suite. See the in-progress section below for the actual Pro features being built out.

### 🚧 Pro Feature Suite -- IN PROGRESS (2026-Q3 target)

The current Pro flags in `config/features.py` are declared but the feature implementations are layered in across the 2026-Q3 Pro Tier Implementation Plan. Anchor: meeting capture + speaker diarization with calendar-aware stitching. Connectors: Gmail/Calendar/Outlook via MCP, Apple Notes/Mail/Messages/EventKit/Photos via native helpers. Intelligence: metamorphic verification, custom smart RAG weights, daily KB digest. Mac-native baseline (signed/notarized universal binary, Sparkle, Keychain, TCC wizard, Voice Memos watch, Spotlight donation, Share Sheet, Shortcuts, Quick Look) ships as **community tier** — Mac integration is baseline, not paid.

### ✅ Pro Mode Configuration UI -- SHIPPED v0.84.0

Settings → Pro tab renders feature status indicators per tier, license-key entry with backend validation, current-plan display, waitlist join, and a feature-discovery matrix. The matrix currently lists declared Pro flags; the in-progress feature suite (above) lands the actual implementations behind those flags.

---

## P2 -- Medium Priority

### Expanded File Type Handling
- Specialized parsers for code (AST extraction for Python, JS/TS)
- Image OCR for scanned PDFs (community — `ocr_parsing` already enabled for all tiers)
- Plain audio transcription via Whisper (community — `audio_transcription_plain`, ships with `voice_memos_watch`)
- Meeting capture + speaker diarization (Pro — anchor feature, see in-progress Pro feature suite above)
- Markdown frontmatter extraction (YAML/TOML headers -> metadata)

### Bulk Import Enhancements
- Ollama content triage (score 1-5 for value assessment)
- Scheduled folder re-scan (cron-based watch)

### Ingestion Pipeline Evolution

#### Pipeline Hardening
- Dead-letter queue, BM25 rollback, triage-to-ingest bridge, per-file status

#### Core Data Sources
- IMAP email (env vars scaffolded in settings.py), RSS feeds, browser bookmarks, inbound webhooks, clipboard, Safari Reading List (community, Mac-native), Voice Memos watch (community, Mac-native)

#### Pro Cloud Connectors (via MCP-over-HTTP — `taylorwilsdon/google_workspace_mcp` + `softeria/ms-365-mcp-server`)
- Gmail + Google Calendar (bundled), Outlook + Outlook Calendar (bundled), Docling parser (community after v0.97)

#### Pro Apple Connectors (native helpers, requires FDA + TCC grants)
- Apple Notes, Apple Mail, Messages (iMessage), Apple Calendar via EventKit, Reminders via EventKit, Photos

#### Storage Dashboard
- Storage metrics, usage bars, persistent history, activity feed

#### KB Interface Refresh
- Live progress, source badges, previews, near-duplicate merge, quality visualizations

---

## P3 -- Low Priority / Future

### SSO / SAML Implementation (Enterprise)
- SAML 2.0 SP with IdP metadata import
- Common IdPs: Okta, Azure AD, OneLogin
- Tenant-scoped SSO configuration
- Currently scaffolded as feature flag only (SSO env vars documented in the internal `.env.example`)

### Enterprise Feature Scaffolding
- All Vault features get endpoint stubs returning 403 with upgrade message
- UI placeholders showing "Available in Cerid Vault"
- Scaffolded: SSO/SAML, advanced audit logging, SIEM export,
  tenant management UI, compliance reporting, dedicated support portal

### Code Quality Improvements
- Type hints on all public APIs, mypy strict mode
- Parent-child hierarchical RAG (currently feature-flagged off)
- Graph RAG with entity extraction and query rewriting

### Chat Messages Virtualization (deferred from v0.84.0)
- First attempt broke 46 jsdom measurement-dependent tests — needs `@tanstack/react-virtual` approach with jsdom-safe measure shim. Named-sprint candidate; high risk.

---

## Next Sprint Candidates

Released work is tracked in [CHANGELOG.md](../CHANGELOG.md) and the [GitHub releases](https://github.com/Cerid-AI/cerid-ai/releases) page; the canonical sprint backlog lives in the internal repo.
