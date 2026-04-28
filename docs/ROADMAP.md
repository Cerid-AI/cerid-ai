# Cerid AI — Development Roadmap

> **Last updated:** 2026-04-26 (post-v0.90.0)
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

### ✅ Pro Tier Purchase Path -- SHIPPED v0.84.0+ (Stripe checkout still open)

Billing backend (license-key generation/validation, waitlist, status) lives in the internal-only distribution. Pro Settings pane (`src/web/src/components/settings/pro-section.tsx`) ships license-key entry, waitlist join, current-plan display, and Pro/Community/Enterprise feature matrices.

**Follow-up (P1):** Stripe checkout end-to-end (interim flow is email waitlist + manual key issuance). Pro-anchor feature (audio transcription) still pending.

### ✅ Pro Mode Configuration & Feature Access -- SHIPPED v0.84.0

Settings → Pro tab renders feature status indicators per tier, license-key entry with backend validation, current-plan display, waitlist join, and a feature-discovery matrix listing all Pro-gated capabilities.

---

## P2 -- Medium Priority

### Expanded File Type Handling
- Specialized parsers for code (AST extraction for Python, JS/TS)
- Image OCR for scanned PDFs (Pro tier)
- Audio transcription for meeting notes (Pro tier — anchor feature for Pro path)
- Markdown frontmatter extraction (YAML/TOML headers -> metadata)

### Bulk Import Enhancements
- Ollama content triage (score 1-5 for value assessment)
- Scheduled folder re-scan (cron-based watch)

### Ingestion Pipeline Evolution

#### Pipeline Hardening
- Dead-letter queue, BM25 rollback, triage-to-ingest bridge, per-file status

#### Core Data Sources
- IMAP email (env vars scaffolded in settings.py), RSS feeds, browser bookmarks, inbound webhooks, clipboard, macOS Quick Actions

#### Pro Data Sources
- Gmail OAuth, Outlook Graph, Apple Notes, Calendar sync, Docling parser

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
