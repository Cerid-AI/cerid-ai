# Multi-Session Cloud Sync Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable seamless multi-session, multi-computer state synchronization for Cerid AI via Dropbox, with frameworks for future multi-user support.

**Architecture:** File-based sync extending the existing `cerid-sync` infrastructure. User state (conversations, settings, UI preferences) is persisted to JSON files in `~/Dropbox/cerid-sync/user/`, synced automatically via Dropbox, and hydrated on startup. localStorage remains the immediate cache for fast reads and offline resilience.

**Tech Stack:** FastAPI (Python 3.11), React 19, Dropbox (file transport), JSON/JSONL

---

## Problem

Six categories of user state don't sync between machines:

| Data | Current Storage | Sync? |
|------|----------------|-------|
| Conversations (50 max) | localStorage | None |
| Settings (15+ toggles) | localStorage + in-memory server | Lost on restart |
| Verification reports | Embedded in conversations localStorage | None |
| UI preferences (mode, routing, onboarding) | localStorage | None |
| KB data | Neo4j + ChromaDB + Redis | Manual export/import |
| Archive files | ~/cerid-archive symlink | Via Dropbox |

## Approach: File-Based Sync via Dropbox

Write user state to JSON files in `~/Dropbox/cerid-sync/user/`. MCP server reads on startup, writes on change. Frontend syncs to server, server persists to sync dir. Dropbox handles transport + conflict detection.

## Sync Directory Layout

```
cerid-sync/
├── manifest.json          # Existing KB sync manifest
├── neo4j/                 # Existing
├── chroma/                # Existing
├── redis/                 # Existing
├── bm25/                  # Existing
└── user/                  # NEW: User state sync
    ├── settings.json      # All toggles + numeric params + routing mode
    ├── state.json         # UI mode, onboarding, theme prefs
    └── conversations/     # One file per conversation
        ├── {uuid}.json    # Full conversation with messages + verification reports
        └── ...
```

One file per conversation avoids merge conflicts — Dropbox only syncs changed files.

## Data Flow

### Settings Persistence
1. `PATCH /settings` → update in-memory config + write `user/settings.json`
2. On MCP startup → read `user/settings.json` → hydrate config (overrides .env defaults)
3. Frontend `useSettings` unchanged — still fire-and-forget to server

### Conversation Sync
1. Frontend saves to localStorage (unchanged)
2. Frontend also calls `POST /user-state/conversations` with conversation data
3. MCP writes `user/conversations/{id}.json` to sync dir
4. On another machine's MCP startup → reads `user/conversations/` → serves via `GET /user-state/conversations`
5. Frontend hydrates from server on mount (if localStorage is empty or stale)

### UI State Sync
1. Frontend calls `PATCH /user-state/preferences` on changes
2. MCP writes `user/state.json`
3. Other machines read on startup

## Multi-User Framework

For future multi-user support, `user/` becomes `users/{user_id}/`:

```
cerid-sync/
└── users/
    ├── default/           # Single-user mode (backward compat)
    └── {user-uuid}/       # Multi-user mode
```

The `tenant_context.py` middleware already provides `get_user_id()` for namespacing.

## New API Endpoints

```
GET  /user-state                    → { settings, preferences, conversation_ids[] }
GET  /user-state/conversations      → { conversations: ConversationSummary[] }
GET  /user-state/conversations/{id} → full conversation JSON
POST /user-state/conversations      → save/update conversation
DELETE /user-state/conversations/{id} → delete (writes tombstone)
PATCH /user-state/preferences       → save UI state (mode, onboarding, routing)
```

## Conflict Strategy

- **Settings**: Last-write-wins by timestamp
- **Conversations**: Last-write-wins per conversation (each file has `updatedAt`)
- **Deletions**: Tombstone in `user/tombstones.json` with TTL
- **Dropbox conflict copies**: Detected on read, newest wins, conflict copy deleted

## Files Modified

| Layer | File | Change |
|-------|------|--------|
| Backend | `src/mcp/routers/user_state.py` | NEW — CRUD endpoints |
| Backend | `src/mcp/sync/user_state.py` | NEW — Read/write user state files |
| Backend | `src/mcp/routers/settings.py` | MODIFY — Persist to settings.json |
| Backend | `src/mcp/main.py` | MODIFY — Include router, hydrate on startup |
| Frontend | `src/web/src/lib/api.ts` | MODIFY — Add user-state API calls |
| Frontend | `src/web/src/hooks/use-conversations.ts` | MODIFY — Sync to server |
| Frontend | `src/web/src/hooks/use-settings.ts` | MODIFY — Hydrate UI prefs |
| Frontend | `src/web/src/contexts/ui-mode-context.tsx` | MODIFY — Sync mode |
| Tests | `src/mcp/tests/test_user_state.py` | NEW — Backend tests |
| Tests | `src/web/src/__tests__/use-conversations-sync.test.ts` | NEW — Frontend sync tests |

## Scope Exclusions (YAGNI)

- No real-time WebSocket sync (Dropbox lag ~5s is fine)
- No conversation search/indexing on server
- No encryption of user state files (Dropbox already encrypted)
- No auto-triggering KB export/import (keep manual)
- No conversation pagination API (50 max, serve all at once)
