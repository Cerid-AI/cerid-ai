// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// URL state hygiene (F-URL-01).
//
// Each primary pane owns a small set of sub-tab URL params (Subjects
// owns `?mode=` / `?entity=` / `?since=` / `?lens=`, Sources owns
// `?sources_mode=`, Settings owns `?diagnostics_tab=`). Switching the
// primary nav used to leave the previous pane's params in the URL —
// bookmarking from Settings could capture `?mode=wiki&sources_mode=…`
// even though those params no longer described any visible state.
//
// `clearForeignPaneParams(newPane)` is called from the central pane-
// change handler in AppLayout (and from NavigationProvider.goTo) right
// before the new pane mounts, so each pane reads a URL that only
// contains its own params. Unrelated query string entries (`utm_*`,
// `?ref=`, etc.) are left untouched.

import type { Pane } from "@/components/layout/sidebar"

/**
 * Per-pane sub-tab URL params. The pane that owns the param is the one
 * that reads + writes it; switching to any other pane strips it.
 *
 * Kept in sync with:
 *   - `components/subjects/subjects-pane.tsx`  (mode, entity, since)
 *   - `components/wiki/entity-detail-view.tsx` (lens — subjects-scoped)
 *   - `components/sources/sources-pane.tsx`    (sources_mode)
 *   - `components/settings/settings-pane.tsx`  (diagnostics_tab)
 */
const PANE_PARAMS: Partial<Record<Pane, readonly string[]>> = {
  subjects: ["mode", "entity", "since", "lens"],
  sources: ["sources_mode"],
  settings: ["diagnostics_tab"],
}

// Flat list of every param any pane owns — used to compute "foreign"
// params (the ones to strip on a primary-nav switch).
const ALL_PANE_PARAMS: readonly string[] = Array.from(
  new Set(Object.values(PANE_PARAMS).flat() as string[]),
)

/**
 * Strip URL search params belonging to other panes, leaving only the
 * params the new pane owns (plus any unrelated params like `utm_*`).
 *
 * Uses `history.replaceState` so the browser back/forward stack is not
 * polluted with a synthetic navigation entry.
 *
 * No-op when running outside a browser (SSR / Vitest setup before
 * jsdom is initialised).
 */
export function clearForeignPaneParams(newPane: Pane): void {
  if (typeof window === "undefined") return
  const params = new URLSearchParams(window.location.search)
  const keep = new Set(PANE_PARAMS[newPane] ?? [])
  let mutated = false
  for (const key of ALL_PANE_PARAMS) {
    if (keep.has(key)) continue
    if (params.has(key)) {
      params.delete(key)
      mutated = true
    }
  }
  if (!mutated) return
  const next = params.toString()
  const url = `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`
  window.history.replaceState({}, "", url)
}
