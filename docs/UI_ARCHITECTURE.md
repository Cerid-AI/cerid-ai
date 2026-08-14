# Cerid AI — UI Architecture

> **Last updated:** 2026-06-11 (SEXTANT settings redesign + Subjects eval cycles 1–4)
>
> **Plan reference:** [`tasks/2026-05-21-cerid-v1-systemic-implementation-plan.md`](../tasks/2026-05-21-cerid-v1-systemic-implementation-plan.md); settings redesign [`tasks/2026-06-10-settings-ia-redesign.md`](../tasks/2026-06-10-settings-ia-redesign.md)

## Sidebar layout

After Phases A → C, the sidebar has **4 top-level panes** plus theme + tier controls:

```
┌────────────────────────────────┐
│  Chat                          │  ← MessageSquare
│  Subjects                      │  ← Compass     (Atlas / Constellation / Timeline / Wiki)
│  Sources                       │  ← Files       (Library / Activity / Connectors)
│  Settings                      │  ← Settings    (Models / Knowledge / Retrieval & Answers / Privacy / Extensions / Appearance / Plan & Billing / System)
└────────────────────────────────┘
```

The shape of each pane is owned by a tabbed/mode sub-controller; deep links use a per-pane URL param (so two panes' modes don't collide on a shared `?mode=` slot):

| Pane | URL param | Modes |
|---|---|---|
| Subjects | `?mode=` | atlas / constellation / timeline / wiki |
| Sources | `?sources_mode=` | library / activity / connectors |
| Settings | `?setting=` / `?settings_q=` | `?setting=` deep-links/reveals a single setting by id (registry-driven); `?settings_q=` drives the settings search; `?diagnostics_tab=` still selects the Diagnostics sub-tab (status / analytics / activity) |

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
| Subjects | `components/subjects/subjects-pane.tsx` | Atlas (decomposition icicle + Neighborhood leaf) / Constellation (cartographic map + cosmos.gl Live) / Timeline (Tephra) / Wiki (FOLIO) |
| Sources | `components/sources/sources-pane.tsx` | Library (current KB pane) / Activity / Connectors |
| Settings | `components/settings/settings-pane.tsx` | 8 intent categories (Models, Knowledge, Retrieval & Answers, Privacy, Extensions, Appearance, Plan & Billing, System) + a separate **Diagnostics** console entry below the separator (preserves the `?diagnostics_tab=` contract) |

Settings categories are defined declaratively in `components/settings/categories/*.tsx` (one file per category) over the registry in `lib/settings-registry/` — see § Settings registry below.

Diagnostics sub-tabs (inside Settings):
- Status — `components/monitoring/monitoring-pane.tsx`
- Analytics — `components/audit/audit-pane.tsx`
- Activity — `components/agents/agents-pane.tsx`

## Global UI primitives shipped during v1.0 consolidation

| Primitive | File | Notes |
|---|---|---|
| Quick-capture FAB | `components/quick-capture/quick-capture-fab.tsx` | Floating action button + ⌘⇧N global. Note / URL / Upload modes. Mounted at AppLayout sibling level so it persists across pane switches. |
| Knowledge-source selector | `components/chat/knowledge-source-selector.tsx` | Chip in chat composer. Three modes (kb / kb+web / llm+kb) with localStorage persistence. Backend wiring lands incrementally — Phase C ships UI + state. |
| Atlas decomposition icicle | `components/subjects/atlas/decomposition/DecompositionIcicle.tsx` (+ `use-decomposition.ts`) | Default Atlas view: domains → communities → entities, backed by `GET /graph/decomposition`. Breadcrumb + Esc/Shift+Esc; ego-network demoted to a Neighborhood leaf mode (hops ≤2). |
| Graph drag-heal | `lib/graph/interactions/drag-heal.ts` | Critically-damped lerp-home on node drag (neighbor falloff, interruptible, `reduced-motion` snap). Mounted on the Constellation cartographer. |
| Shared hover plate | `lib/graph/draw-node-hover.ts` | One hover-plate renderer shared across Subjects graph modes (kills the white-box / flicker class). |
| Atlas saved views | `components/subjects/atlas/atlas-saved-views.tsx` | Per-user named Atlas configurations, Redis-backed via `/atlas/views/*`. Saved-view schema is **v3** (adds `atlasTier` for icicle-tier restore). |
| Domain / Trust lenses | `lib/graph/identity.ts` (`domainSlot`) + `compute_trust_state` job | Domain colour lens (12 `--color-domain-*` tokens via the salt-796 hash) and Trust lens (reads `Entity.trust_state`) across canvas / timeline / palette. |
| Wiki → Atlas cross-link | `components/wiki/article-infobox.tsx` | "Open in Atlas" infobox button → `useNavigation().goTo("subjects", {mode:"atlas", entity})`. |
| Atlas right-click menu | `components/subjects/atlas/atlas-context-menu.tsx` | Cite in chat / Open in Wiki / Copy entity id. |
| Search palette | `components/subjects/search-palette.tsx` | ⌘K-invoked entity picker for Subjects pane. |
| Wiki provenance markers | `components/wiki/provenance-marker.tsx` | Inline section badges: auto / user-edited / contradicted / uncertain. |
| Wiki mini-graph | `components/wiki/mini-graph.tsx` | Expandable inline Atlas at 1-hop, lazy-loaded. |
| Constellation tour mode | `components/subjects/constellation/map/map-tour.tsx` | LLM-narrated guided tour on the 2D map: sigma camera framing per stop + always-on narration panel. Pro-gated via `POST /graph/tour/generate`. (The R3F 3D scene was cut 2026-08-13.) |
| Sources activity stream | `components/sources/activity-stream.tsx` | Polling (3s active / 30s history) live ingestion stream with 4-stage pipeline progress. |
| Sources connectors panel | `components/sources/sources-connectors.tsx` | Unified list+detail of watched folders + external APIs + plugins. |
| Knowledge-source selector | `components/chat/knowledge-source-selector.tsx` | Chip in chat composer (kb / kb+web / llm+kb). |
| Settings Diagnostics tab | `components/settings/diagnostics-section.tsx` | 3 sub-tabs consolidating Monitoring / Audit / Agents. |

## Settings registry (SEXTANT, 2026-06-10)

Settings is registry-driven: every control is one `SettingDef` in
`lib/settings-registry/` (per-category def files). A def carries
`{id, category, group, level: core|advanced, label, helpText, scopeOfEffect,
keywords, type, writer, entitlement→featureFlag, dependsOn, danger,
visibleWhen}`. `keywords` retains the **old tab names** so search still finds a
moved setting. The `writer` is a discriminated union
(`settings-patch | preferences | endpoint | local | env | readonly`) so storage
dispatch is type-safe.

| Piece | File | Notes |
|---|---|---|
| Registry | `lib/settings-registry/` | Single source of truth; one def file per category. |
| Category pages | `components/settings/categories/*.tsx` | 8 files: models, knowledge, retrieval-answers, privacy, extensions, appearance, plan-billing, system. |
| Search | `components/settings/settings-search.tsx` | Registry-driven token-AND search; `/` keyboard shortcut; persisted in `?settings_q=`. |
| Reveal channel | `components/settings/reveal-context.tsx` | `?setting=<id>` deep links + search-result clicks; force-opens the containing `AdvancedDisclosure` regardless of detail level. |
| Detail level | `lib/settings-mode.ts` + `AdvancedDisclosure` in `settings-primitives.tsx` | "Settings detail level" Simple\|Advanced radiogroup (localStorage `cerid-settings-mode`, default `simple`). Consumed **only** by `AdvancedDisclosure` default-open state — it is NOT an app-wide UI mode. |
| Entitlements | `hooks/use-entitlements.ts` | `useEntitlements()` → per-setting `{available \| locked \| flag-off \| degraded}`; one consolidated entitlement treatment + one Recommendations card (`recommendation-banner.tsx`). |

> **Removed:** the old app-wide `ui-mode-context.tsx` / `useUIMode()` /
> `AdvancedMode` wrapper and the `cerid-ui-mode` localStorage key were deleted
> (SET-01). The Simple\|Advanced control is now settings-scoped only. Old
> settings tabs (Essentials / Pipeline / Governance / Pro) no longer exist.

## What's NOT in the architecture

- **No client-side router.** Pane state is in-memory via NavigationProvider; URL params decorate but don't drive navigation. Adding React Router or TanStack Router is a future option but not blocking — the redirect map already serves shareable links.
- **No right-side KB column in chat.** (As of Phase C Day 4 the column is deprecated. Removal is tracked for Phase C.2 because removing it requires moving HallucinationPanel + KBContextPanel to a different surface or deleting them.)
- **No global ContextMenu component on canvas-backed surfaces.** Atlas's right-click is portal-rendered floating menu, not radix ContextMenu, because sigma's canvas can't host shadcn primitives directly.

## Test surface

| Test type | Location | Notes |
|---|---|---|
| Pane tests | `src/web/src/__tests__/*-pane.test.tsx` | Each top-level pane has its own integration test mocking the heavy sub-components |
| Navigation redirect tests | `src/web/src/__tests__/navigation-redirect.test.tsx` | Locks the LEGACY_PANE_REDIRECTS contract per redirect |
| Settings pane | `src/web/src/__tests__/settings-pane.test.tsx` | Registry-driven render, detail-level radiogroup, reveal/round-trip (replaced the deleted `ui-mode.test.tsx`). |
| Component-level tests | `src/web/src/components/**/*.test.{ts,tsx}` | Inline next to each component |

## Performance budgets

See [`PERF_BUDGETS.md`](PERF_BUDGETS.md) for Atlas + Constellation measured budgets. Settings/Sources/Chat are unbounded today — their dominant cost is React reconciliation, not WebGL, and they're nowhere near the budget envelope.
