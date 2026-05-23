# Cerid AI — UI Architecture

> **Last updated:** 2026-05-21 (Phase C close — final 4-pane shape)
>
> **Plan reference:** [`tasks/2026-05-21-cerid-v1-systemic-implementation-plan.md`](../tasks/2026-05-21-cerid-v1-systemic-implementation-plan.md)

## Sidebar layout

After Phases A → C, the sidebar has **4 top-level panes** plus theme + tier controls:

```
┌────────────────────────────────┐
│  Chat                          │  ← MessageSquare
│  Subjects                      │  ← Compass     (Atlas / Constellation / Timeline / Wiki)
│  Sources                       │  ← Files       (Library / Activity / Connectors)
│  Settings                      │  ← Settings    (Essentials / Pipeline / System / Governance / Plugins / Diagnostics / Pro)
└────────────────────────────────┘
```

The shape of each pane is owned by a tabbed/mode sub-controller; deep links use a per-pane URL param (so two panes' modes don't collide on a shared `?mode=` slot):

| Pane | URL param | Modes |
|---|---|---|
| Subjects | `?mode=` | atlas / constellation / timeline / wiki |
| Sources | `?sources_mode=` | library / activity / connectors |
| Settings | `?diagnostics_tab=` | (only Diagnostics sub-tab uses this — status / analytics / activity) |

The legacy `?entity=` param is shared across all panes for cross-pane deep linking (an entity opened from a Communities link, for example).

## Pre-consolidation history

The sidebar started at **9 panes** at the beginning of the Cerid v1.0 plan (May 2026):

```
Chat / Knowledge / Wiki / Communities / Monitoring / Audit / Memories / Agents / Settings
```

Consolidation landed in three phases:

| Phase | Days | Consolidation |
|---|---|---|
| A | Days 8-9 | Wiki, Communities, Memories → Subjects pane modes |
| B | Days 8-9 | Knowledge → Sources pane (Library mode) |
| C | Day 2 | Monitoring, Audit, Agents → Settings → Diagnostics tab |
| C | Day 3 | Simple/Advanced mode toggle removed |

**9 → 4 panes** — net reduction of 5 sidebar entries.

## Legacy redirect map

Code that still calls `goTo("monitoring")` or `goTo("wiki")` continues to work — `NavigationProvider` transparently routes legacy panes to their consolidated destinations:

```ts
// src/web/src/contexts/navigation-context.tsx
const LEGACY_PANE_REDIRECTS = {
  wiki:        { pane: "subjects",  mode: "wiki" },
  communities: { pane: "subjects",  mode: "atlas" },
  memories:    { pane: "subjects",  mode: "atlas" },
  knowledge:   { pane: "sources",   mode: "library" },
  monitoring:  { pane: "settings",  mode: "status" },
  audit:       { pane: "settings",  mode: "analytics" },
  agents:      { pane: "settings",  mode: "activity" },
}
```

Each redirect writes the destination pane's URL param (`?mode=` / `?sources_mode=` / `?diagnostics_tab=`) before triggering the pane change, so the user lands on the right tab even on first hit.

The Pane union retains the legacy values for one release window so existing tests + direct programmatic mounts keep working. Pane render-case removal is tracked for v1.1.

## Component layout reference

| Pane | Entry component | Sub-tabs / modes |
|---|---|---|
| Chat | `components/chat/chat-panel.tsx` | (no sub-tabs) |
| Subjects | `components/subjects/subjects-pane.tsx` | Atlas (2D WebGL) / Constellation (3D R3F) / Timeline / Wiki |
| Sources | `components/sources/sources-pane.tsx` | Library (current KB pane) / Activity / Connectors |
| Settings | `components/settings/settings-pane.tsx` | Essentials, Pipeline, System, Governance, Plugins, **Diagnostics**, Pro |

Diagnostics sub-tabs (inside Settings):
- Status — `components/monitoring/monitoring-pane.tsx`
- Analytics — `components/audit/audit-pane.tsx`
- Activity — `components/agents/agents-pane.tsx`

## Global UI primitives shipped during v1.0 consolidation

| Primitive | File | Notes |
|---|---|---|
| Quick-capture FAB | `components/quick-capture/quick-capture-fab.tsx` | Floating action button + ⌘⇧N global. Note / URL / Upload modes. Mounted at AppLayout sibling level so it persists across pane switches. |
| Knowledge-source selector | `components/chat/knowledge-source-selector.tsx` | Chip in chat composer. Three modes (kb / kb+web / llm+kb) with localStorage persistence. Backend wiring lands incrementally — Phase C ships UI + state. |
| Atlas saved views | `components/subjects/atlas/atlas-saved-views.tsx` | Per-user named Atlas configurations, Redis-backed via `/atlas/views/*`. |
| Atlas right-click menu | `components/subjects/atlas/atlas-context-menu.tsx` | Cite in chat / Open in Wiki / Copy entity id. |
| Search palette | `components/subjects/search-palette.tsx` | ⌘K-invoked entity picker for Subjects pane. |
| Wiki provenance markers | `components/wiki/provenance-marker.tsx` | Inline section badges: auto / user-edited / contradicted / uncertain. |
| Wiki mini-graph | `components/wiki/mini-graph.tsx` | Expandable inline Atlas at 1-hop, lazy-loaded. |
| Constellation ambient particles | `components/subjects/constellation/ambient-particles.tsx` | 800-point THREE.Points cloud, AdditiveBlending. One draw call. |
| Constellation tour mode | `components/subjects/constellation/tour-controller.tsx` | LLM-narrated camera arc + GSAP-style lerp + Web Speech API TTS + always-on subtitle. Pro-gated via `POST /graph/tour/generate`. |
| Sources activity stream | `components/sources/activity-stream.tsx` | Polling (3s active / 30s history) live ingestion stream with 4-stage pipeline progress. |
| Sources connectors panel | `components/sources/sources-connectors.tsx` | Unified list+detail of watched folders + external APIs + plugins. |
| Knowledge-source selector | `components/chat/knowledge-source-selector.tsx` | Chip in chat composer (kb / kb+web / llm+kb). |
| Settings Diagnostics tab | `components/settings/diagnostics-section.tsx` | 3 sub-tabs consolidating Monitoring / Audit / Agents. |

## UIModeProvider (legacy)

Phase C Day 3 reduced `contexts/ui-mode-context.tsx` to a pass-through. The Provider now always returns `{mode:"advanced", isSimple:false, setMode:no-op, toggle:no-op}`. Existing `useUIMode()` consumers (settings-pane, essentials-section, sidebar, chat-panel, setup-wizard, advanced-mode wrapper, system-section) read this constant unchanged. The Provider + hook are retained — not deleted — to avoid a 7-file cleanup in one commit; their bodies are now trivial.

`AdvancedMode` wrapper (`components/common/advanced-mode.tsx`) is correspondingly a pass-through.

localStorage key `cerid-ui-mode` is no longer written; existing values are read but ignored. Cleanup tracked for v1.1.

## What's NOT in the architecture

- **No client-side router.** Pane state is in-memory via NavigationProvider; URL params decorate but don't drive navigation. Adding React Router or TanStack Router is a future option but not blocking — the redirect map already serves shareable links.
- **No right-side KB column in chat.** (As of Phase C Day 4 the column is deprecated. Removal is tracked for Phase C.2 because removing it requires moving HallucinationPanel + KBContextPanel to a different surface or deleting them.)
- **No global ContextMenu component on canvas-backed surfaces.** Atlas's right-click is portal-rendered floating menu, not radix ContextMenu, because sigma's canvas can't host shadcn primitives directly.

## Test surface

| Test type | Location | Notes |
|---|---|---|
| Pane tests | `src/web/src/__tests__/*-pane.test.tsx` | Each top-level pane has its own integration test mocking the heavy sub-components |
| Navigation redirect tests | `src/web/src/__tests__/navigation-redirect.test.tsx` | Locks the LEGACY_PANE_REDIRECTS contract per redirect |
| UI mode contract | `src/web/src/__tests__/ui-mode.test.tsx` | Asserts the always-advanced pass-through |
| Component-level tests | `src/web/src/components/**/*.test.{ts,tsx}` | Inline next to each component |

## Performance budgets

See [`PERF_BUDGETS.md`](PERF_BUDGETS.md) for Atlas + Constellation measured budgets. Settings/Sources/Chat are unbounded today — their dominant cost is React reconciliation, not WebGL, and they're nowhere near the budget envelope.
