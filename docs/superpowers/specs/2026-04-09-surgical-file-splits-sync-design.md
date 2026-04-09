# Design: Surgical File Splits for Clean Repo Sync

**Date:** 2026-04-09
**Status:** Approved
**Scope:** cerid-ai-internal repo restructuring + sync tooling

## Problem

The cerid-ai-internal (canonical) and cerid-ai (public distribution) repos share ~95% of code, but 7 files contain both public and internal-only content (trading, boardroom, billing, enterprise). This forces manual cherry-picking on every sync — 18-26% of all commits are sync-related, and 60% of those fix sync breakage. A recent 16-commit sync required 4 parallel subagents doing line-by-line merges of exclusion-list files.

## Solution

Split each mixed file into a **public base** + **internal extension** (`*_internal.py`). The base file is identical in both repos with zero trace of internal features. Internal extensions exist only in the internal repo and register themselves via hook functions. A sync script + CI validation automates the process.

## Constraints

- **Zero trace** of internal features in public repo (no stubs, no conditional imports, no hook references)
- **Bidirectional sync** (develop in either repo, sync to the other)
- **Surgical scope** — only split the 7 mixed files, keep existing core/app/enterprise layering

## File Split Map

### Config Layer

| Base File | Internal Extension | What Moves |
|-----------|--------------------|------------|
| `config/settings.py` | `config/settings_internal.py` | `CERID_TRADING_ENABLED`, `TRADING_AGENT_URL`, `CERID_BOARDROOM_ENABLED`, `CERID_BOARDROOM_TIER`, trading/boardroom CONSUMER_REGISTRY entries |
| `config/taxonomy.py` | `config/taxonomy_internal.py` | 6 boardroom domains, trading sub-categories, boardroom affinity weights, boardroom tag vocabularies |

### Router Layer

| Base File | Internal Extension | What Moves |
|-----------|--------------------|------------|
| `app/routers/agents.py` | `app/routers/agents_internal.py` | 5 trading endpoints (`/agent/trading/*`), trading model imports, `_TRADING_ENABLED` gate |
| `app/routers/sdk.py` | `app/routers/sdk_internal.py` | 5 trading SDK endpoints, 4 boardroom ops endpoints, trading/boardroom response models |

### App Layer

| Base File | Internal Extension | What Moves |
|-----------|--------------------|------------|
| `app/main.py` | `app/main_internal.py` | alerts/migration/ws_sync/trading_proxy/eval/billing router imports + registrations, trading proxy shutdown |
| `app/tools.py` | `app/tools_internal.py` | 5 trading MCP tool definitions, 5 trading dispatch cases |
| `app/scheduler.py` | `app/scheduler_internal.py` | 3 trading job functions, trading job registration block |

### Frontend

| Base File | Internal Extension | What Moves |
|-----------|--------------------|------------|
| `src/web/src/lib/types.ts` | `src/web/src/lib/types_internal.ts` | `trading_enabled` field, enterprise `PluginStatus`/`FeatureTier` variants |

## Wiring Pattern

Each internal extension exposes a hook function. The internal version of the base file appends a small block (after a marker comment) that calls the hook:

```python
# ── Internal feature bootstrap ─────────────────────────────
try:
    from app.main_internal import register_internal_routers
    register_internal_routers(app)
except ImportError:
    pass
```

The public version of the base file ends before the marker — no reference to `_internal` modules.

**Key invariant:** `internal main.py = public main.py + appended hook block`. The sync script enforces this by truncating at the marker (to-public) or appending the hook block (from-public).

## Sync Manifest (`.sync-manifest.yaml`)

Declares three categories:

1. **`internal_only`** — files that exist only in internal repo (all `*_internal.py`, enterprise/, trading models, desktop/, internal tests)
2. **`mixed_files`** — files where internal = public + appended hook block, identified by `hook_marker` comment
3. **`forbidden_in_public`** — strings that must never appear in the public repo (leak detection)

## Sync Script (`scripts/sync-repos.sh`)

Three commands:
- `to-public [--dry-run]` — copy internal→public, skip internal_only, truncate mixed files at marker, scan for leaks
- `from-public [--dry-run]` — copy public→internal, re-append hook blocks from current internal, skip internal_only files
- `validate` — scan both repos for leaks and missing config

## CI Integration

- **Internal repo:** `sync-validate` job runs `validate` on every PR
- **Public repo:** `no-internal-leaks` job greps for forbidden strings from `.sync-forbidden.txt`

## Implementation Phases

1. **Create 7 internal extension files** — extract internal-only code, add hook functions
2. **Update base files** — remove internal lines, add hook marker + bootstrap blocks (internal versions only)
3. **Update frontend types** — split types.ts, create types_internal.ts
4. **Update imports & tests** — fix any imports of moved symbols, update mock paths
5. **Create sync tooling** — manifest, script, CI jobs
6. **Verify end-to-end** — dry-run sync, validate, run full test suite
7. **Execute first sync** — push clean public version

## Success Criteria

- `ruff check` + `mypy` + `tsc --noEmit` pass
- All existing tests pass (zero regressions)
- `sync-repos.sh validate` passes both directions
- `sync-repos.sh to-public --dry-run` output has zero forbidden strings
- `CERID_TRADING_ENABLED=true` activates all trading features in internal repo
- Future syncs require zero manual file merging
