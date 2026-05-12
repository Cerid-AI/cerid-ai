// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { LastUpdated } from "@/components/ui/last-updated"
import { DigestCard } from "./digest-card"
import { HealthCards } from "./health-cards"
import { InvariantsCard } from "./invariants-card"
import { CollectionChart } from "./collection-chart"
import { IngestionTimeline } from "./ingestion-timeline"
import { SchedulerStatus } from "./scheduler-status"
import { KBOperations } from "./kb-operations"
import { ObservabilityDashboard } from "./observability-dashboard"
import { TrustScoreChip } from "@/components/trust-score"
import { ProcessorPane } from "@/components/processor"
import { fetchMaintenance, fetchIngestLog, fetchSchedulerStatus, fetchDigest } from "@/lib/api"

export function MonitoringPane() {
  const { data: maintenance, isLoading: loadingMaintenance, isError: errorMaintenance, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["maintenance"],
    queryFn: () => fetchMaintenance(["health", "collections"]),
    refetchInterval: 30_000,
  })

  const { data: ingestLog } = useQuery({
    queryKey: ["ingest-log"],
    queryFn: () => fetchIngestLog(200),
    refetchInterval: 30_000,
  })

  const { data: scheduler } = useQuery({
    queryKey: ["scheduler"],
    queryFn: fetchSchedulerStatus,
    refetchInterval: 30_000,
  })

  const [digestHours, setDigestHours] = useState(24)
  const { data: digest, isLoading: loadingDigest } = useQuery({
    queryKey: ["digest", digestHours],
    queryFn: () => fetchDigest(digestHours),
    refetchInterval: 60_000,
  })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Health</h2>
          <div className="flex items-center gap-3">
            {/* TrustScore chip — operator shortcut; same chip as the status bar */}
            <TrustScoreChip />
            <LastUpdated timestamp={dataUpdatedAt} />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">Live infrastructure status and recent operations</p>
      </div>

      {/* D.2: loading state */}
      {loadingMaintenance ? (
        <div className="space-y-3 p-4" aria-label="Loading system status" role="status">
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-32 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
        </div>
      ) : errorMaintenance ? (
        /* D.2: error state */
        <div className="flex items-center justify-center p-8">
          <div className="w-full max-w-md">
            <PaneError
              title="Failed to load system status"
              description="Check that the backend is running, then retry."
              onRetry={() => void refetch()}
            />
          </div>
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-4">
            <PaneErrorBoundary label="Knowledge Digest">
              <DigestCard digest={digest} isLoading={loadingDigest} onPeriodChange={setDigestHours} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Processor">
              <ProcessorPane />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Observability">
              <ObservabilityDashboard />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Health Cards">
              <HealthCards health={maintenance?.health} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Operational Invariants">
              <InvariantsCard />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Collection Chart">
              <CollectionChart collections={maintenance?.collections} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="KB Operations">
              <KBOperations />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Ingestion Timeline">
              <IngestionTimeline entries={ingestLog?.entries} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Scheduler Status">
              <SchedulerStatus scheduler={scheduler} />
            </PaneErrorBoundary>
          </div>
        </ScrollArea>
      )}
    </div>
  )
}

export default MonitoringPane
