// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { CSSProperties } from "react"

/**
 * Shared Recharts tooltip style matching the app's popover theme.
 * Used by activity-chart, model-accuracy-chart, and query-stats.
 */
export const CHART_TOOLTIP_STYLE: CSSProperties = {
  fontSize: 12,
  borderRadius: 8,
  backgroundColor: "var(--popover)",
  color: "var(--popover-foreground)",
  border: "1px solid var(--border)",
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
