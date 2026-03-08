// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { CSSProperties } from "react"

/**
 * Shared Recharts tooltip style matching the app's popover theme.
 * Used by activity-chart, model-accuracy-chart, and query-stats.
 */
export const CHART_TOOLTIP_STYLE: CSSProperties = {
  fontSize: 12,
  borderRadius: 8,
  backgroundColor: "hsl(var(--popover))",
  color: "hsl(var(--popover-foreground))",
  border: "1px solid hsl(var(--border))",
}

/**
 * Domain → badge class map (background + text + hover).
 * Canonical source for DomainBadge and DomainFilter.
 */
export const DOMAIN_BADGE_COLORS: Record<string, string> = {
  coding: "bg-blue-500/10 text-blue-700 dark:text-blue-400 hover:bg-blue-500/20",
  finance: "bg-green-500/10 text-green-700 dark:text-green-400 hover:bg-green-500/20",
  projects: "bg-purple-500/10 text-purple-700 dark:text-purple-400 hover:bg-purple-500/20",
  personal: "bg-orange-500/10 text-orange-700 dark:text-orange-400 hover:bg-orange-500/20",
  general: "bg-zinc-500/10 text-zinc-700 dark:text-zinc-400 hover:bg-zinc-500/20",
}

/**
 * Domain → text-only color class map.
 * Used by TaxonomyTree folder icons.
 */
export const DOMAIN_TEXT_COLORS: Record<string, string> = {
  coding: "text-blue-600 dark:text-blue-400",
  finance: "text-green-600 dark:text-green-400",
  projects: "text-purple-600 dark:text-purple-400",
  personal: "text-orange-600 dark:text-orange-400",
  general: "text-zinc-600 dark:text-zinc-400",
  conversations: "text-cyan-600 dark:text-cyan-400",
}
