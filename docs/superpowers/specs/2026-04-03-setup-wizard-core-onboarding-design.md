# Setup Wizard: Core Onboarding Redesign

**Date**: 2026-04-03
**Status**: Design
**Scope**: Cerid Core (community tier) first-run experience

## Context

The current setup wizard is a 4-step flow (Welcome -> API Keys -> Review -> Health) followed by a separate 4-step onboarding dialog (Welcome -> Sidebar -> Features -> Mode Selection). This gets users to a working state but leaves critical configuration undone:

- Knowledge base path and domain structure are never configured
- Ollama (free local LLM for pipeline tasks) is not surfaced
- No "first success moment" — users land on an empty chat with no documents ingested
- No lightweight mode detection for 8GB machines
- No progress persistence (closing mid-wizard restarts from step 0)
- Health check hard-blocks if any service is slow to start

The goal is to expand the wizard into a comprehensive onboarding that takes a new Core user from zero to a working first query, while keeping it fast for power users via smart defaults and skippable steps.

## Research Basis

Patterns drawn from: AnythingLLM (wizard steps = architecture), Home Assistant (auto-detection, one input -> many defaults), Notion (learn-by-doing checklist), Open WebUI (auto-detect Ollama, first account = admin). Key principle: end on a working query, not an empty screen.

---

## Wizard Structure

8 steps, expanding from the current 4. Each step maps to a layer of the Cerid architecture. Every step except Welcome, LLM Providers, Review, and Mode Selection is skippable.

```
Step 0: Welcome & System Check
Step 1: LLM Providers (API Keys)         [required]
Step 2: Knowledge Base Configuration      [skippable]
Step 3: Local LLM (Ollama)               [skippable]
Step 4: Review & Apply                    [required]
Step 5: Service Health                    [required, degraded proceed after 30s]
Step 6: First Document                    [skippable]
Step 7: Choose Your Mode                  [required]
```

### Footer Pattern

```
[Back]  [Skip ->]  ........  [step indicator]  ........  [Next ->]
```

- Back: ghost variant, hidden on step 0
- Skip: ghost variant, only shown on skippable steps, secondary to Next
- Next: primary/brand variant, disabled until step requirements met
- Step indicator: labeled step list showing name + completed/active/pending/skipped state

### Progress Persistence

Wizard state serialized to `localStorage` key `cerid-setup-progress` on every state change. On mount, if saved state exists, offer: "Resume where you left off?" / "Start over". Clear on completion.

### Onboarding Dialog Absorption

The current `onboarding-dialog.tsx` is deprecated. Its content is distributed:

- Sidebar navigation explanation -> Step 0 welcome text
- KB injection explanation -> Step 2 helper text
- Verification explanation -> Step 5 service descriptions
- Mode selection -> Step 7

Backward compat: `localStorage.getItem("cerid-onboarding-complete")` check remains in `App.tsx` so users who completed the old onboarding don't re-see the wizard.

---

## Step Details

### Step 0: Welcome & System Check

**Purpose**: Introduce Cerid, set expectations, auto-detect environment.

**UI**:
- Sparkles icon + "Let's get you set up" heading
- 3 value-prop bullets (existing: privacy-first, local KB, 100+ models)
- NEW: "System Check" card below bullets, auto-runs on mount:

| Check | Display |
|-------|---------|
| RAM | `16 GB` -> check "Recommended config" / `8 GB` -> warning "Lightweight mode recommended" |
| Docker | check Running / x Not detected |
| Existing .env | check Found (partially configured) / dash Fresh install |
| Ollama | check Detected at localhost:11434 / dash Not found |

**Smart behavior**:
- RAM < 12 GB -> set `lightweight_recommended = true` for Step 2
- Ollama detected -> pre-enable in Step 3
- Existing .env keys -> pre-populate Step 1

**Backend**: `GET /setup/system-check`

**Skip**: Not skippable (informational + auto-detection, no user input)

### Step 1: LLM Providers (API Keys)

**Purpose**: Configure required OpenRouter key + optional direct providers.

**UI**: Existing step with refinements:
- Pre-populate if system check found existing OPENROUTER_API_KEY
- Add xAI (Grok) as 4th optional provider
- After OpenRouter validates, show model count (e.g., "Valid - 342 models available")
- Keep "Don't have an OpenRouter account?" helper box

**Validation**: Real-time via existing `POST /setup/validate-key`

**Skip**: Not skippable (OpenRouter is minimum viable config)

### Step 2: Knowledge Base Configuration

**Purpose**: Set up where Cerid looks for documents and how it organizes them.

**UI**:
- Database icon + "Knowledge Base" heading
- **Archive Path**: Text input defaulting to `~/cerid-archive`, with helper: "This is where Cerid looks for your documents. Organize files into domain folders."
- **Domain Quickstart**: Checkbox grid of taxonomy domains:
  - Pre-checked: `coding`, `finance`, `general`
  - Unchecked: `personal`, `projects`, `inbox`
  - Helper: "Domains help route queries to the right documents. Add more later in Settings."
- **Lightweight Mode** (conditional, shown if RAM < 12 GB):
  - Warning banner: "Your system has X GB RAM. Lightweight mode disables Neo4j graph features for better performance."
  - Toggle: `Enable lightweight mode` (pre-checked if < 12 GB)
- **Watched Folder** toggle: "Automatically watch archive folder for new files?" (default ON)
  - Helper: "New files dropped into your archive will be auto-ingested."

**Smart defaults**: `~/cerid-archive`, coding+finance+general, lightweight auto-based on RAM, watch ON.

**Skip**: Skippable (all defaults apply)

### Step 3: Local LLM (Ollama)

**Purpose**: Optional local LLM for zero-cost pipeline tasks.

**UI**:
- Cpu icon + "Local LLM (Optional)" heading
- Explanation: "Ollama runs AI models locally for free. Cerid uses it for background tasks like verification and claim extraction - your main chat still uses OpenRouter."
- **Connection status**: Auto-detected from Step 0

**If detected**:
- Green badge "Connected"
- Installed models list
- If no models: "Ollama running but no models. Pull recommended model?"
- Recommended: `llama3.2:3b` (3.4 GB) card with "Pull Model" button + progress bar
- Toggle: `Enable Ollama for pipeline tasks` (default ON)

**If not detected**:
- Amber badge "Not detected"
- Two option cards:
  - "Install Ollama" -> link to ollama.com/download
  - "Skip for now" -> "Enable later in Settings -> System"
- Toggle stays OFF

**Smart defaults**: Enable if detected with models. Disable if not found.

**Skip**: Skippable (Ollama stays disabled, pipeline uses OpenRouter)

### Step 4: Review & Apply

**Purpose**: Summary of all configuration, single atomic apply.

**UI**: Expanded from current Review step:
- **LLM Providers**: OpenRouter check Ready, OpenAI check/dash, Anthropic check/dash, xAI check/dash
- **Knowledge Base**: Archive path, X domains enabled, lightweight on/off, watch folder on/off
- **Local LLM**: Ollama enabled/disabled, model name if pulled
- "Apply Configuration" button
- Success message + auto-advance to Health after 800ms (existing behavior)

**Backend**: Expanded `POST /setup/configure` writes all config atomically to `.env`

**Skip**: Not skippable (commit step)

### Step 5: Service Health

**Purpose**: Verify services are running after configuration.

**UI**: Existing health dashboard with additions:
- **Service descriptions** (one-line each):
  - Neo4j: "Graph relationships between documents"
  - ChromaDB: "Vector search for semantic queries"
  - Redis: "Cache and session storage"
  - Bifrost: "Routes queries to the best AI model"
  - MCP: "Core API server"
  - Verification: "Checks AI claims against your KB"
- **Degraded proceed** (after 30s timer):
  - "Some services are still starting. You can wait or proceed with reduced functionality."
  - "Proceed Anyway" button (enabled after 30s)
  - Offline services labeled "(will be available when ready)"
- **Lightweight mode**: If enabled, Neo4j shows "Disabled (lightweight mode)" not "Offline"

**Skip**: Not skippable, but "Proceed Anyway" after 30s

### Step 6: First Document (Skippable)

**Purpose**: Guide user through first ingestion + first RAG query. The "first success moment."

**UI**:
- FileText icon + "Try It Out" heading
- Two option cards:
  - **"Upload a document"**: Drag-and-drop zone (reuse `use-drag-drop` hook). Accepts PDF, TXT, MD, DOCX. Shows ingestion progress: parsing -> chunking -> embedding -> stored.
  - **"Use sample content"**: Button that ingests bundled `sample-knowledge.md`. Badge: "Quick start"
- After ingestion, transition to mini-chat:
  - Suggestion chips: "What is this document about?", "Summarize the key points", "What topics does it cover?"
  - User can type own question or click chip
  - Response shows with KB injection indicator lit (proving RAG works)
  - Success: green checkmark + "Your knowledge base is working!"

**Sample content**: `src/mcp/data/sample-knowledge.md` — ~500 words about "What is Cerid AI?" covering RAG, verification, memory, and privacy model.

**Skip**: "Skip - I'll add documents later" in footer

### Step 7: Choose Your Mode

**Purpose**: Select UI mode. Final step.

**UI**: Mode selection from current onboarding dialog:
- Summary line: "You configured X LLM providers, Y KB domains, and [Ollama enabled/disabled]."
- Two mode cards:
  - Simple: Clean chat interface, KB/verification/analytics hidden
  - Advanced: Full control, all pipeline settings visible
- Helper: "You can change this anytime from the sidebar."
- "Open Cerid AI" finish button

**On finish**: Set mode via UIModeContext, save `cerid-onboarding-complete` to localStorage, clear `cerid-setup-progress`, call `onComplete()`

---

## Backend Changes

### New Endpoint: `GET /setup/system-check`

```python
class SystemCheckResponse(BaseModel):
    ram_gb: float
    docker_running: bool
    env_exists: bool
    env_keys_present: list[str]
    ollama_detected: bool
    ollama_url: str | None = None
    ollama_models: list[str]
    lightweight_recommended: bool
    archive_path_exists: bool
    default_archive_path: str
```

Implementation:
- RAM: `psutil.virtual_memory().total / (1024**3)` (psutil already in requirements)
- Docker: check if Docker socket exists or run `docker info` with timeout
- .env: read existing keys from `_ENV_FILE`
- Ollama: HTTP GET to `http://localhost:11434/api/tags` with 2s timeout
- Archive: check if `~/cerid-archive` exists
- lightweight_recommended: `ram_gb < 12`

### Expanded: `POST /setup/configure`

Add fields to `ConfigureRequest`:

```python
class ConfigureRequest(BaseModel):
    # Existing
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    xai_api_key: str | None = None
    neo4j_password: str | None = None
    # New
    archive_path: str | None = None
    domains: list[str] | None = None
    lightweight_mode: bool | None = None
    watch_folder: bool | None = None
    ollama_enabled: bool | None = None
    ollama_model: str | None = None
```

New fields map to `.env` vars:
- `archive_path` -> `WATCH_FOLDER`
- `lightweight_mode` -> `CERID_LIGHTWEIGHT`
- `ollama_enabled` -> `OLLAMA_ENABLED`
- `ollama_model` -> `OLLAMA_DEFAULT_MODEL`
- `watch_folder` -> `CERID_WATCH_ENABLED`
- `domains` -> `CERID_ACTIVE_DOMAINS` (comma-separated, e.g., `coding,finance,general`). The configure handler writes this to `.env`, and `config/taxonomy.py` reads it at startup to filter the active domain set. If unset, all default domains are active (backward compat).

The handler also:
- Creates archive directory + domain subdirectories if they don't exist
- Auto-generates `NEO4J_PASSWORD` and `REDIS_PASSWORD` if not already set (using `secrets.token_hex(16)`)

### New File: `src/mcp/data/sample-knowledge.md`

~500 word document about Cerid AI for first-document ingestion in Step 6.

---

## Frontend Changes

### Component Structure

```
components/setup/
  setup-wizard.tsx          # Refactored: useReducer, 8 steps, progress persistence
  api-key-input.tsx         # Existing (unchanged)
  health-dashboard.tsx      # Modified: + degraded proceed + service descriptions
  system-check-card.tsx     # NEW
  kb-config-step.tsx        # NEW
  ollama-step.tsx           # NEW
  first-document-step.tsx   # NEW
  mode-selection-step.tsx   # NEW (extracted from onboarding-dialog.tsx)
  step-indicator.tsx        # NEW (replaces dot indicators)
```

### State Management

Single `useReducer` with `WizardState`:

```typescript
interface WizardState {
  step: number
  systemCheck: SystemCheckResult | null
  keys: Record<string, ProviderKey>
  kb: {
    archivePath: string
    domains: string[]
    lightweightMode: boolean
    watchFolder: boolean
  }
  ollama: {
    detected: boolean
    enabled: boolean
    model: string | null
    pulling: boolean
  }
  applied: boolean
  allHealthy: boolean
  healthTimeout: boolean
  firstDoc: {
    ingested: boolean
    queried: boolean
    skipped: boolean
  }
  selectedMode: "simple" | "advanced"
}
```

Actions: `SET_STEP`, `SET_SYSTEM_CHECK`, `SET_KEY`, `SET_KB`, `SET_OLLAMA`, `SET_APPLIED`, `SET_HEALTH`, `SET_HEALTH_TIMEOUT`, `SET_FIRST_DOC`, `SET_MODE`, `RESTORE_PROGRESS`

### API Layer

Add to `src/web/src/lib/api/settings.ts`:

```typescript
export interface SystemCheckResponse {
  ram_gb: number
  docker_running: boolean
  env_exists: boolean
  env_keys_present: string[]
  ollama_detected: boolean
  ollama_url: string | null
  ollama_models: string[]
  lightweight_recommended: boolean
  archive_path_exists: boolean
  default_archive_path: string
}

export async function fetchSystemCheck(): Promise<SystemCheckResponse> {
  const res = await fetch(`${BASE_URL}/setup/system-check`)
  if (!res.ok) throw new Error("System check failed")
  return res.json()
}
```

Expand `applySetupConfig` to accept the full config shape including KB and Ollama fields.

### Step Indicator Component

Replaces the current dot indicators with a vertical or horizontal labeled step list:

```
  [check] Welcome          <- completed
  [check] API Keys         <- completed
  [>] Knowledge Base       <- active
  [ ] Ollama               <- pending
  [ ] Review               <- pending
  [ ] Health               <- pending
  [ ] First Document       <- pending
  [ ] Choose Mode          <- pending
```

Each step shows: icon (check/arrow/circle), label, and optional "(skipped)" badge.

**Layout decision**: The step indicator renders as a **horizontal compact bar above the footer**, replacing the current dot indicators in the same position. Each step is an abbreviated label (e.g., "Welcome", "API Keys", "KB", "Ollama", "Review", "Health", "First Doc", "Mode") with state-colored icon. This avoids widening the dialog or adding a sidebar.

**Dialog sizing**: Expand from `max-w-lg` to `max-w-xl` to accommodate the wider step indicator and new step content (especially the KB domain grid and first-document drag-drop zone).

---

## App.tsx Changes

```typescript
// Remove onboarding dialog rendering
// Keep backward compat check
const [showOnboarding] = useState(() => {
  try { return !localStorage.getItem("cerid-onboarding-complete") } catch { return false }
})

// Setup wizard now handles everything
if (setupRequired || showOnboarding) {
  return <SetupWizard open onComplete={handleSetupComplete} />
}
```

If `setupRequired` is false but `showOnboarding` is true (returning user who never completed old onboarding), the wizard detects existing configuration and can skip to Step 7 (mode selection).

---

## Testing Strategy

### Unit Tests (per component)
- `system-check-card.test.tsx`: renders detected/not-detected states for each check
- `kb-config-step.test.tsx`: domain toggle, archive path input, lightweight mode conditional
- `ollama-step.test.tsx`: detected vs not-detected paths, model pull progress
- `first-document-step.test.tsx`: upload flow, sample content flow, mini-chat
- `mode-selection-step.test.tsx`: mode toggle, finish button
- `step-indicator.test.tsx`: completed/active/pending/skipped states

### Integration Tests
- Full 8-step flow with mocked API responses
- Skip behavior: verify skipped steps use smart defaults
- Progress persistence: serialize -> close -> restore -> verify state
- Degraded health: verify 30s timeout enables "Proceed Anyway"
- Pre-populated state: verify system check results pre-fill later steps
- Backward compat: old onboarding-complete users don't re-see wizard

### Backend Tests
- `test_setup_system_check`: verify RAM, Docker, .env, Ollama detection
- `test_setup_configure_expanded`: verify all new fields write to .env correctly
- `test_setup_configure_directory_creation`: verify archive + domain dirs created

---

## Verification Plan

1. **Fresh install**: Delete `.env`, clear localStorage, load app -> full 8-step wizard
2. **Partial install**: Set OPENROUTER_API_KEY in .env -> wizard pre-populates Step 1
3. **Skip path**: Click Skip on steps 2, 3, 6 -> verify defaults applied
4. **Health degraded**: Stop Neo4j container -> verify 30s timeout + "Proceed Anyway"
5. **Lightweight mode**: Set RAM mock to 8GB -> verify warning shown + toggle pre-checked
6. **Ollama detected**: Run Ollama locally -> verify auto-detection in Step 0 + Step 3
7. **First document**: Upload a PDF -> verify ingestion progress -> ask question -> verify RAG response
8. **Sample content**: Click "Use sample content" -> verify ingestion + query works
9. **Progress persistence**: Complete 3 steps -> refresh browser -> verify "Resume?" prompt
10. **Backward compat**: Set `cerid-onboarding-complete` in localStorage -> verify no re-wizard
11. **Run all tests**: `cd src/web && npx vitest run`
12. **Backend tests**: `docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/test_setup.py -v"`

---

## Files to Create/Modify

### New Files
- `src/web/src/components/setup/system-check-card.tsx`
- `src/web/src/components/setup/kb-config-step.tsx`
- `src/web/src/components/setup/ollama-step.tsx`
- `src/web/src/components/setup/first-document-step.tsx`
- `src/web/src/components/setup/mode-selection-step.tsx`
- `src/web/src/components/setup/step-indicator.tsx`
- `src/mcp/data/sample-knowledge.md`
- `src/web/src/__tests__/system-check-card.test.tsx`
- `src/web/src/__tests__/kb-config-step.test.tsx`
- `src/web/src/__tests__/ollama-step.test.tsx`
- `src/web/src/__tests__/first-document-step.test.tsx`
- `src/web/src/__tests__/mode-selection-step.test.tsx`
- `src/web/src/__tests__/step-indicator.test.tsx`

### Modified Files
- `src/web/src/components/setup/setup-wizard.tsx` — refactor to useReducer, 8 steps, progress persistence
- `src/web/src/components/setup/health-dashboard.tsx` — add degraded proceed, service descriptions
- `src/web/src/App.tsx` — absorb onboarding dialog, update setup flow logic
- `src/mcp/routers/setup.py` — add system-check endpoint, expand configure endpoint
- `src/web/src/lib/api/settings.ts` — add fetchSystemCheck, expand applySetupConfig types
- `src/web/src/lib/types.ts` — add SystemCheckResponse, expand SetupConfig types
- `src/web/src/__tests__/setup-wizard.test.tsx` — expand for 8 steps
- `src/web/src/__tests__/setup-wizard-full.test.tsx` — expand integration tests

### Deprecated (no longer rendered, keep for reference)
- `src/web/src/components/onboarding/onboarding-dialog.tsx`
- `src/web/src/__tests__/onboarding-dialog.test.tsx`

---

## Existing Code to Reuse

- `_update_env_file()` in `src/mcp/routers/setup.py` — .env writing logic
- `validate_provider_key()` in `src/mcp/config/providers.py` — key validation
- `use-drag-drop.ts` hook — file drag-and-drop for Step 6
- `fetchOllamaStatus()` / `pullOllamaModel()` in `src/web/src/lib/api/settings.ts` — Ollama API
- `UIModeProvider` / `useUIMode()` context — mode selection in Step 7
- `fetchSetupStatus()` / `fetchSetupHealth()` — existing setup API layer
- `TAXONOMY_DOMAINS` from `src/mcp/config/taxonomy.py` — domain list for Step 2
- Ingestion: `POST /ingest_file` endpoint for Step 6 document upload
- Query: `POST /agent/query` endpoint for Step 6 mini-chat
