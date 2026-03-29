# External APIs + Watched Folders + RAG Resilience

## Context

Three interconnected improvements to make Cerid a production-ready knowledge platform:

1. **External APIs visible in Knowledge Console** — users need to see which APIs are active, toggle them, and have more free public APIs available out of the box
2. **Watched folders management** — users need to add/remove/enable/disable folders, see sync status, and optionally isolate folder contents in RAG
3. **RAG orchestration resilience** — the orchestrator needs to gracefully handle folders being enabled/disabled, external sources toggling, and domain isolation

**What exists:** 3 preloaded data sources (Wikipedia, Wolfram, Exchange Rates), pluggable DataSource framework, single-folder watcher, folder scanner with preview/pause/resume/cancel, retrieval orchestrator with source_breakdown, Knowledge Console with 3-section display.

---

## Sprint 1: External APIs in Knowledge Console + More Free Sources

### 1.1 Add free public API sources

**File:** `src/mcp/utils/data_sources/` — add 3 new sources:

| Source | API | Auth | Domain Scope | What it provides |
|--------|-----|------|--------------|------------------|
| **DuckDuckGo Instant Answers** | `api.duckduckgo.com/?q={query}&format=json` | None | All | Quick answers, related topics, Wikipedia abstracts |
| **Open Library** | `openlibrary.org/search.json?q={query}` | None | All | Book metadata, author info, ISBNs |
| **PubChem** | `pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/description/JSON` | None | research, general | Chemical compound data, descriptions |

Each follows the existing `DataSource` pattern:
- Subclass `DataSource` from `base.py`
- Implement `async query()` returning `list[DataSourceResult]`
- Register in `__init__.py`
- No API keys needed (free public APIs)

### 1.2 Show APIs in Knowledge Console external section

**File:** `src/web/src/components/kb/knowledge-console.tsx`
- Add a small "APIs" indicator in the External section header showing which sources are active
- When no external results exist, show the list of enabled APIs with status dots
- Add a toggle per-source inline (calls `enableDataSource`/`disableDataSource`)

**File:** `src/web/src/lib/api/kb.ts`
- Add `fetchDataSources()` API function (already exists in settings.ts, re-export or move)

### 1.3 Move data sources from System tab to more prominent location

**File:** `src/web/src/components/settings/essentials-section.tsx`
- Add a compact "External APIs" section showing enabled sources with toggles
- Currently buried in System → Infrastructure → Data Sources (collapsed by default)

---

## Sprint 2: Watched Folders CRUD + UI

### 2.1 Backend: Watched folder registry

**File:** `src/mcp/routers/watched_folders.py` (new)

Endpoints:
- `POST /watched-folders` — add folder (path, domain_override, enabled)
- `GET /watched-folders` — list all folders with status
- `PATCH /watched-folders/{id}` — update (enable/disable, domain_override, exclude_patterns)
- `DELETE /watched-folders/{id}` — remove
- `POST /watched-folders/{id}/scan` — trigger scan on this folder
- `GET /watched-folders/{id}/status` — sync status (last_scanned_at, counts)

**Storage:** Redis hash `cerid:watched_folders` — JSON per folder. Lightweight, no Neo4j schema changes needed. Folder metadata:
```python
{
    "id": "uuid",
    "path": "/Users/justin/Dropbox",
    "label": "Dropbox",  # user-friendly name
    "enabled": true,
    "domain_override": null,  # or "finance" to force domain
    "exclude_patterns": [".git", "node_modules"],
    "search_enabled": true,  # include in RAG queries
    "last_scanned_at": "2026-03-29T12:00:00Z",
    "stats": {"ingested": 142, "skipped": 38, "errored": 2},
    "created_at": "2026-03-29T10:00:00Z"
}
```

### 2.2 Backend: Per-folder scan isolation

**File:** `src/mcp/services/folder_scanner.py`
- `scan_watched_folder(folder_id)` — scan a specific folder, namespace Redis state keys to `cerid:watched:{folder_id}:*`
- Tag ingested artifacts with `watched_folder_id` in ChromaDB metadata
- Domain detection: use `domain_override` if set, else auto-detect from path

### 2.3 Backend: Per-folder search filtering

**File:** `src/mcp/agents/decomposer.py`
- Add optional `watched_folder_ids` filter to ChromaDB query `where` clause
- When a folder has `search_enabled: false`, exclude its chunks from RAG results
- This enables users to ingest a folder for archival without polluting search

### 2.4 Frontend: Watched Folders panel in Settings

**File:** `src/web/src/components/settings/system-section.tsx`
- Add "Watched Folders" section (FolderOpen icon)
- List cards: path, label, enabled toggle, search_enabled toggle, last scan time, stats
- "Add Folder" button → inline form (path + label + domain override)
- "Scan Now" button per folder
- "Remove" button with confirmation

**File:** `src/web/src/lib/api/settings.ts`
- Add CRUD functions: `fetchWatchedFolders()`, `addWatchedFolder()`, `updateWatchedFolder()`, `removeWatchedFolder()`, `scanWatchedFolder()`

---

## Sprint 3: RAG Orchestration Resilience

### 3.1 Source availability awareness

**File:** `src/mcp/agents/retrieval_orchestrator.py`
- Before querying external sources, check which are enabled + configured
- If memory recall times out, don't fail the whole query — return KB-only results
- If all external sources error, log warning but return KB + memory results
- Add `_timings` entries for each source type (kb_ms, memory_ms, external_ms)

### 3.2 Folder-aware domain routing

**File:** `src/mcp/agents/retrieval_orchestrator.py`
- Accept optional `exclude_folder_ids` parameter
- Pass to decomposer for ChromaDB `where` filtering
- Respect per-folder `search_enabled` flag

### 3.3 Graceful degradation on source failure

**File:** `src/mcp/agents/retrieval_orchestrator.py`
- Wrap each source (KB, memory, external) in try/except with circuit breaker awareness
- If circuit breaker is open for a source, skip it immediately (don't wait for timeout)
- Return partial `source_breakdown` with available sources
- Add `source_status` to response: `{kb: "ok", memory: "timeout", external: "ok"}`

---

## Verification

### Backend
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim sh -c \
  'pip install -q -r requirements.txt -r requirements-dev.txt; python -m pytest tests/ --tb=line -q'
```

### Frontend
```bash
cd src/web && npx tsc --noEmit && npx vitest run
```

### Manual
- Open Knowledge Console → verify external APIs show with toggle
- Settings → Watched Folders → add a folder → scan → verify artifacts appear
- Disable a folder's search → verify its chunks are excluded from queries
- Disable an external API → verify it stops appearing in results

---

## Files Summary

| File | Sprint | Change |
|------|--------|--------|
| `src/mcp/utils/data_sources/duckduckgo.py` | 1 | New — DuckDuckGo Instant Answers |
| `src/mcp/utils/data_sources/openlibrary.py` | 1 | New — Open Library search |
| `src/mcp/utils/data_sources/pubchem.py` | 1 | New — PubChem compound data |
| `src/mcp/utils/data_sources/__init__.py` | 1 | Register 3 new sources |
| `src/web/src/components/kb/knowledge-console.tsx` | 1 | API status + inline toggles |
| `src/web/src/components/settings/essentials-section.tsx` | 1 | Compact external APIs section |
| `src/mcp/routers/watched_folders.py` | 2 | New — CRUD + scan endpoints |
| `src/mcp/services/folder_scanner.py` | 2 | Per-folder scan isolation |
| `src/mcp/agents/decomposer.py` | 2, 3 | Folder-aware query filtering |
| `src/web/src/components/settings/system-section.tsx` | 2 | Watched Folders UI panel |
| `src/web/src/lib/api/settings.ts` | 2 | Watched folder API functions |
| `src/mcp/agents/retrieval_orchestrator.py` | 3 | Source resilience + folder filtering |
| `src/mcp/main.py` | 2 | Register watched_folders router |
