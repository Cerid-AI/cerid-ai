// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Diagnostics tab — Phase C Day 2. Merges the legacy Monitoring +
// Agents top-level panes into a single Settings tab with two sub-tabs:
//   - Status: health gauges + invariants + processor pane (Monitoring)
//   - Activity: agent event console + custom agents (Agents)
//
// The Audit / usage-analytics surface was promoted out to its own
// top-level Analytics section (ST9, 2026-06-22) — see analytics-section.tsx.
// Both remaining pane components are mounted unchanged; this is a
// routing-level consolidation, not a rewrite. NavigationProvider redirects
// legacy goTo("monitoring"|"agents") calls here, and goTo("audit") to Analytics.

import { lazy, Suspense } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"

const MonitoringPane = lazy(() => import("@/components/monitoring/monitoring-pane"))
const AgentsPane = lazy(() => import("@/components/agents/agents-pane"))

export type DiagnosticsSubTab = "status" | "activity"

interface DiagnosticsSectionProps {
  /** Initial sub-tab — supports deep-link via ?diagnostics_tab= */
  initialTab?: DiagnosticsSubTab
  onTabChange?: (tab: DiagnosticsSubTab) => void
  /** Pro / community tier — gates Pro-only viz. */
  tier?: string
}

function PaneLoader({ label }: { label: string }) {
  return (
    <div className="flex h-full min-h-[300px] items-center justify-center text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
      Loading {label}…
    </div>
  )
}

export function DiagnosticsSection({ initialTab = "status", onTabChange }: DiagnosticsSectionProps) {
  const queryClient = useQueryClient()
  return (
    <Tabs
      defaultValue={initialTab}
      onValueChange={(v) => onTabChange?.(v as DiagnosticsSubTab)}
      className="flex h-full flex-col"
    >
      <TabsList className="w-full shrink-0">
        <TabsTrigger value="status" className="flex-1">Status</TabsTrigger>
        <TabsTrigger value="activity" className="flex-1">Activity</TabsTrigger>
      </TabsList>

      <TabsContent value="status" className="grow overflow-auto pt-2">
        <PaneErrorBoundary label="Diagnostics — Status" queryClient={queryClient}>
          <Suspense fallback={<PaneLoader label="status" />}>
            <MonitoringPane />
          </Suspense>
        </PaneErrorBoundary>
      </TabsContent>

      <TabsContent value="activity" className="grow overflow-auto pt-2">
        <PaneErrorBoundary label="Diagnostics — Activity" queryClient={queryClient}>
          <Suspense fallback={<PaneLoader label="activity" />}>
            <AgentsPane />
          </Suspense>
        </PaneErrorBoundary>
      </TabsContent>
    </Tabs>
  )
}
