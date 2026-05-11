// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { AlertCircle, RefreshCw } from "lucide-react"
import { PaneErrorBoundary } from "@/components/ui/pane-error-boundary"
import { LastUpdated } from "@/components/ui/last-updated"
import { DigestCard } from "./digest-card"
import { HealthCards } from "./health-cards"
import { CollectionChart } from "./collection-chart"
import { IngestionTimeline } from "./ingestion-timeline"
import { SchedulerStatus } from "./scheduler-status"
import { KBOperations } from "./kb-operations"
import { ObservabilityDashboard } from "./observability-dashboard"
import { TrustScoreChip } from "@/components/trust-score"
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
        <div className="flex flex-col items-center justify-center gap-4 p-8">
          <Alert variant="destructive" className="max-w-md">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            <AlertDescription>
              Failed to load system status. Check that the backend is running.
            </AlertDescription>
          </Alert>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-4">
            <PaneErrorBoundary label="Knowledge Digest">
              <DigestCard digest={digest} isLoading={loadingDigest} onPeriodChange={setDigestHours} />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Observability">
              <ObservabilityDashboard />
            </PaneErrorBoundary>
            <PaneErrorBoundary label="Health Cards">
              <HealthCards health={maintenance?.health} />
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
