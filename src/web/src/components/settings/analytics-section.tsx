// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Analytics section (ST9) — promoted out of Settings → Diagnostics into its
 * own top-level settings entry, because usage / cost / accuracy reporting is
 * high-value and was previously buried behind a third Diagnostics sub-tab.
 *
 * Hosts the Phase L advanced analytics surface over the legacy audit pane
 * (claim-accuracy + privacy-audit panels). Both panes are mounted unchanged;
 * this is a placement change, not a rewrite. Legacy `goTo("audit")` and
 * `?diagnostics_tab=analytics` deep links route here (navigation-context +
 * the shell's initialCategory).
 */

import { lazy, Suspense } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"

const AuditPane = lazy(() => import("@/components/audit/audit-pane"))
const AnalyticsPanel = lazy(() =>
  import("@/components/analytics/analytics-panel").then((m) => ({ default: m.AnalyticsPanel })),
)

function PaneLoader({ label }: { label: string }) {
  return (
    <div className="flex h-full min-h-80 items-center justify-center text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
      Loading {label}…
    </div>
  )
}

export function AnalyticsSection({ tier = "community" }: { tier?: string }) {
  const queryClient = useQueryClient()
  return (
    <div className="space-y-3" data-testid="analytics-section">
      <PaneErrorBoundary label="Analytics" queryClient={queryClient}>
        <Suspense fallback={<PaneLoader label="analytics" />}>
          <AnalyticsPanel tier={tier} />
        </Suspense>
      </PaneErrorBoundary>
      <PaneErrorBoundary label="Analytics — Audit" queryClient={queryClient}>
        <Suspense fallback={<PaneLoader label="audit" />}>
          <AuditPane />
        </Suspense>
      </PaneErrorBoundary>
    </div>
  )
}
