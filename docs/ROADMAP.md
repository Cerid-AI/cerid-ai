# Cerid AI -- Development Roadmap

> **Last updated:** 2026-03-31
> **For completed work:** See `docs/COMPLETED_PHASES.md`

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

### Private Mode (Ephemeral Sessions)
- Toggle in toolbar: "Private Mode" -- nothing remembered, nothing saved
- 4 configurable security levels:
  1. No history, no memory extraction
  2. Also disable KB context injection
  3. Also force local-only models (Ollama)
  4. Also clear Redis query cache on session end
- Visual lock indicator, session data wiped on close

### Conversation Management UX
- Archived conversations view toggle
- Bulk select/delete for history cleanup
- Search within conversation history

### Agent Communication Console
- Optional real-time console panel showing agent activity
- Ticker-scroll with humanized agent messages
- Color-coded by agent, filterable, collapsible/dockable
- Transparency into the intelligence pipeline

### Model Management & Auto-Update Detection
- Auto-detection of new model releases via OpenRouter API polling
- Notification badge when new models available
- Deprecation warnings for outdated models
- Cost comparison view: current model vs alternatives

### Pro Tier Purchase Path
- Stripe integration for Pro tier licensing
- License key validation endpoint
- Self-serve upgrade flow Core -> Pro in Settings
- **Pro anchor feature:** Audio transcription (meeting notes, interviews, lectures)

### Pro Mode Configuration & Feature Access
- Clear Pro settings pane showing all Pro-gated features with status
- Feature discovery: show what Pro unlocks with preview
- License key entry and validation

---

## P2 -- Medium Priority

### Expanded File Type Handling
- Specialized parsers for code (AST extraction for Python, JS/TS)
- Image OCR for scanned PDFs (Pro tier)
- Audio transcription for meeting notes (Pro tier)
- Markdown frontmatter extraction (YAML/TOML headers -> metadata)

### Bulk Import Enhancements
- Ollama content triage (score 1-5 for value assessment)
- Scheduled folder re-scan (cron-based watch)

### Ingestion Pipeline Evolution

#### Pipeline Hardening
- Dead-letter queue, BM25 rollback, triage-to-ingest bridge, per-file status

#### Core Data Sources
- IMAP email, RSS feeds, browser bookmarks, inbound webhooks, clipboard, macOS Quick Actions

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
- Currently scaffolded as feature flag only

### Enterprise Feature Scaffolding
- All Vault features get endpoint stubs returning 403 with upgrade message
- UI placeholders showing "Available in Cerid Vault"
- Scaffolded: SSO/SAML, advanced audit logging, SIEM export,
  tenant management UI, compliance reporting, dedicated support portal

### Code Quality Improvements
- Type hints on all public APIs, mypy strict mode
- Parent-child hierarchical RAG (currently feature-flagged off)
- Graph RAG with entity extraction and query rewriting
