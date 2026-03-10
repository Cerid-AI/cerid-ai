// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Loader2, RefreshCw } from "lucide-react"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { LastUpdated } from "@/components/ui/last-updated"
import { ActivityChart } from "./activity-chart"
import { CostBreakdown } from "./cost-breakdown"
import { QueryStats } from "./query-stats"
import { IngestionStats } from "./ingestion-stats"
import { RecentFailures } from "./recent-failures"
import { ConversationStats } from "./conversation-stats"
import { AccuracyDashboard } from "./accuracy-dashboard"
import { fetchAudit } from "@/lib/api"
import { cn } from "@/lib/utils"

const TIME_RANGES = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
] as const

const REPORT_OPTIONS = [
  { key: "activity", label: "Activity" },
  { key: "ingestion", label: "Ingestion" },
  { key: "costs", label: "Costs" },
  { key: "queries", label: "Queries" },
  { key: "conversations", label: "Conversations" },
  { key: "verification", label: "Verification" },
] as const

export function AuditPane() {
  const [hours, setHours] = useState(24)
  const [enabledReports, setEnabledReports] = useState<Record<string, boolean>>(
    Object.fromEntries(REPORT_OPTIONS.map((r) => [r.key, true])),
  )
  const queryClient = useQueryClient()

  const activeReports = REPORT_OPTIONS.filter((r) => enabledReports[r.key]).map((r) => r.key)

  const { data: audit, isLoading, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["audit", hours, activeReports],
    queryFn: () => fetchAudit(activeReports, hours),
    refetchInterval: 60_000,
    enabled: activeReports.length > 0,
  })

  const toggleReport = (key: string) => {
    setEnabledReports((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ["audit"] })
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Analytics</h2>
          <div className="flex items-center gap-2">
            <LastUpdated timestamp={dataUpdatedAt} />
            <Button
              variant="ghost"
              size="sm"
              className="h-7"
              onClick={handleRefresh}
              disabled={isFetching}
              aria-label="Refresh analytics"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">Historical usage, costs, and accuracy reports</p>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-6 p-4">
          {/* ── Analytics ───────────────────────────────── */}
          <section className="space-y-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Analytics
            </h3>

            {/* Filters — colocated with analytics content */}
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-1">
                <span className="text-[10px] font-medium uppercase text-muted-foreground">Period</span>
                <div className="flex rounded-md border">
                  {TIME_RANGES.map((range) => (
                    <Button
                      key={range.hours}
                      variant="ghost"
                      size="sm"
                      className={cn(
                        "h-6 rounded-none border-r px-2 text-xs last:border-r-0",
                        hours === range.hours && "bg-primary/10 font-medium text-primary",
                      )}
                      onClick={() => setHours(range.hours)}
                    >
                      {range.label}
                    </Button>
                  ))}
                </div>
              </div>
              <Separator orientation="vertical" className="h-5" />
              <div className="flex items-center gap-1">
                <span className="text-[10px] font-medium uppercase text-muted-foreground">Show</span>
                <div className="flex gap-0.5">
                  {REPORT_OPTIONS.map((report) => (
                    <Button
                      key={report.key}
                      variant="ghost"
                      size="sm"
                      className={cn(
                        "h-6 px-2 text-xs",
                        enabledReports[report.key]
                          ? "bg-primary/10 font-medium text-primary"
                          : "text-muted-foreground",
                      )}
                      onClick={() => toggleReport(report.key)}
                    >
                      {report.label}
                    </Button>
                  ))}
                </div>
              </div>
            </div>

            {/* Analytics content */}
            {activeReports.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
                Select at least one report to display
              </div>
            ) : isLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading audit data...
              </div>
            ) : (
              <div className="space-y-4">
                {enabledReports.activity && (
                  <PaneErrorBoundary label="Activity Chart">
                    <ActivityChart activity={audit?.activity} />
                  </PaneErrorBoundary>
                )}
                {enabledReports.costs && (
                  <PaneErrorBoundary label="Cost Breakdown">
                    <CostBreakdown costs={audit?.costs} hours={hours} />
                  </PaneErrorBoundary>
                )}
                {enabledReports.conversations && (
                  <PaneErrorBoundary label="Conversation Stats">
                    <ConversationStats conversations={audit?.conversations} />
                  </PaneErrorBoundary>
                )}
                {enabledReports.queries && (
                  <PaneErrorBoundary label="Query Stats">
                    <QueryStats queries={audit?.queries} />
                  </PaneErrorBoundary>
                )}
                {enabledReports.ingestion && (
                  <PaneErrorBoundary label="Ingestion Stats">
                    <IngestionStats ingestion={audit?.ingestion} />
                  </PaneErrorBoundary>
                )}
                {enabledReports.activity && (
                  <PaneErrorBoundary label="Recent Failures">
                    <RecentFailures failures={audit?.activity?.recent_failures} />
                  </PaneErrorBoundary>
                )}
                {enabledReports.verification && (
                  <PaneErrorBoundary label="Verification Accuracy">
                    <AccuracyDashboard verification={audit?.verification} />
                  </PaneErrorBoundary>
                )}
              </div>
            )}
          </section>
        </div>
      </ScrollArea>
    </div>
  )
}

export default AuditPane
