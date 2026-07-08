// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure predicates for per-community combos (A10): a user can collapse a
// single community into one disc from its card. Members (and their incident
// edges) hide; the supernode layer draws a disc at the community anchor.
// Distinct from the GLOBAL zoom collapse (collapsedRef), which hides every
// member at once.

export function memberHidden(
  entityCommunity: string | null | undefined,
  manualCollapsed: ReadonlySet<string>,
): boolean {
  if (manualCollapsed.size === 0 || entityCommunity == null) return false
  return manualCollapsed.has(String(entityCommunity))
}

export function edgeHidden(
  srcCommunity: string | null | undefined,
  tgtCommunity: string | null | undefined,
  manualCollapsed: ReadonlySet<string>,
): boolean {
  return (
    memberHidden(srcCommunity, manualCollapsed) ||
    memberHidden(tgtCommunity, manualCollapsed)
  )
}
