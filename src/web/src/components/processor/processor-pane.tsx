// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ProcessorPane — background-job processor status card (Phase P.2).
 *
 * Mounted inside monitoring-pane.tsx as a card in the scroll area.
 * Provides: activity chip, queue depth, pause/resume, per-priority
 * breakdown, and a scrollable recent-job list.
 */

import { cn } from "@/lib/utils"
import { formatCost } from "@/lib/utils"
import { useProcessorStatus, useProcessorRecent, useProcessorMutations } from "@/hooks/use-processor"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { EmptyState } from "@/components/ui/empty-state"
import { ScrollArea } from "@/components/ui/scroll-area"
import { JobRow } from "./job-row"
import {
  Cpu,
  Pause,
  Play,
  List,
  LayoutGrid,
} from "lucide-react"
import type { ProcessorPriority } from "@/lib/types/processor"

// ---------------------------------------------------------------------------
// Activity chip
// ---------------------------------------------------------------------------

interface ActivityChipProps {
  isLoading: boolean
  paused: boolean
  totalQueued: number
}

function ActivityChip({ isLoading, paused, totalQueued }: ActivityChipProps) {
  if (isLoading) {
    return (
      <span role="status" aria-label="Loading processor status">
        <Skeleton className="h-5 w-28 rounded-full" />
      </span>
    )
  }

  let label: string
  let chipClass: string

  if (paused) {
    label = "Cerid: paused"
    chipClass =
      "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
  } else if (totalQueued > 0) {
    label = `Cerid: ${totalQueued} job${totalQueued === 1 ? "" : "s"} pending`
    chipClass =
      "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
  } else {
    label = "Cerid: idle"
    chipClass =
      "border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-300"
  }

  return (
    <span
      data-testid="processor-activity-chip"
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        chipClass,
      )}
      aria-label={`Processor status: ${label}`}
      aria-live="polite"
    >
      <Cpu className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}

// ---------------------------------------------------------------------------
// Priority breakdown (Queue tab)
// ---------------------------------------------------------------------------

const PRIORITIES: ProcessorPriority[] = ["high", "medium", "low"]

interface QueueBreakdownProps {
  queue_sizes: Record<string, number>
}

function QueueBreakdown({ queue_sizes }: QueueBreakdownProps) {
  const allEmpty = PRIORITIES.every((p) => (queue_sizes[p] ?? 0) === 0)

  if (allEmpty) {
    return (
      <p className="py-4 text-center text-sm text-muted-foreground">
        All queues empty
      </p>
    )
  }

  return (
    <div className="space-y-1.5 py-2" role="list" aria-label="Queue depths by priority">
      {PRIORITIES.map((priority) => {
        const count = queue_sizes[priority] ?? 0
        return (
          <div
            key={priority}
            className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm"
            role="listitem"
          >
            <span className="capitalize font-medium text-foreground">{priority}</span>
            <Badge
              variant={count > 0 ? "secondary" : "outline"}
              className="tabular-nums"
              aria-label={`${count} job${count === 1 ? "" : "s"} in ${priority} queue`}
            >
              {count}
            </Badge>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ProcessorPane() {
  const {
    data: status,
    isLoading: statusLoading,
    isError: statusError,
    refetch: refetchStatus,
  } = useProcessorStatus()
  const {
    data: recent,
    isLoading: recentLoading,
    isError: recentError,
    refetch: refetchRecent,
  } = useProcessorRecent(50)
  const { pause, resume, isPending } = useProcessorMutations()

  const totalQueued = status
    ? Object.values(status.queue_sizes).reduce((sum, n) => sum + n, 0)
    : 0

  if (statusError) {
    return (
      <Card>
        <CardContent className="p-3">
          <PaneError
            title="Failed to load processor status"
            description="Check that the backend is running, then retry."
            onRetry={() => void refetchStatus()}
          />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="space-y-0 p-3 pb-2">
        {/* Title row */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <CardTitle className="text-sm">Processor</CardTitle>
          </div>
          <ActivityChip
            isLoading={statusLoading}
            paused={status?.paused ?? false}
            totalQueued={totalQueued}
          />
        </div>

        {/* Stats + pause/resume row */}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          {statusLoading ? (
            <div className="flex gap-3">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-4 w-20" />
            </div>
          ) : statusError ? (
            <span className="text-xs text-destructive">Status unavailable</span>
          ) : (
            <div
              className="flex flex-wrap gap-3 text-xs text-muted-foreground"
              aria-label="Processor metrics"
            >
              <span>
                <span className="font-medium text-foreground">{status?.jobs_completed_24h ?? 0}</span>
                {" "}jobs (24h)
              </span>
              <span>
                <span className="font-medium text-foreground">
                  {formatCost(status?.cost_usd_7d ?? 0)}
                </span>
                {" "}(7d)
              </span>
              <span>
                <span className="font-medium text-foreground">{status?.throttled_ticks_1h ?? 0}</span>
                {" "}throttled (1h)
              </span>
            </div>
          )}

          {/* Pause / resume */}
          {!statusLoading && !statusError && (
            status?.paused ? (
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1.5 text-xs"
                onClick={() => void resume()}
                disabled={isPending}
                aria-label="Resume processor — allow new jobs to dequeue"
                data-testid="processor-resume-button"
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                Resume
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1.5 text-xs"
                onClick={() => void pause()}
                disabled={isPending}
                aria-label="Pause processor — halt new dequeues"
                data-testid="processor-pause-button"
              >
                <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                Pause
              </Button>
            )
          )}
        </div>
      </CardHeader>

      <CardContent className="p-3 pt-0">
        <Tabs defaultValue="queue">
          <TabsList className="h-8 w-full">
            <TabsTrigger value="queue" className="flex-1 gap-1.5 text-xs">
              <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
              Queue
            </TabsTrigger>
            <TabsTrigger value="recent" className="flex-1 gap-1.5 text-xs">
              <List className="h-3.5 w-3.5" aria-hidden="true" />
              Recent
            </TabsTrigger>
          </TabsList>

          {/* Queue depth tab */}
          <TabsContent value="queue">
            {statusLoading ? (
              <div className="space-y-1.5 py-2" role="status" aria-label="Loading queue depths">
                <Skeleton className="h-8 w-full rounded-md" />
                <Skeleton className="h-8 w-full rounded-md" />
                <Skeleton className="h-8 w-full rounded-md" />
              </div>
            ) : (
              // Note: statusError is handled by the outer early-return at the
              // top of ProcessorPane — it never reaches the tabs. Audit P2.6.
              <QueueBreakdown queue_sizes={status?.queue_sizes ?? {}} />
            )}
          </TabsContent>

          {/* Recent jobs tab */}
          <TabsContent value="recent">
            {recentLoading ? (
              <div className="space-y-1 py-2" role="status" aria-label="Loading recent jobs">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-7 w-full rounded-md" />
                ))}
              </div>
            ) : recentError ? (
              <div className="mt-2">
                <PaneError
                  title="Failed to load recent jobs"
                  onRetry={() => void refetchRecent()}
                />
              </div>
            ) : !recent || recent.length === 0 ? (
              <EmptyState
                icon={Cpu}
                title="No recent jobs"
                description="Completed and failed jobs appear here"
              />
            ) : (
              <ScrollArea className="mt-1 max-h-64">
                <div role="table" aria-label="Recent jobs">
                  {recent.map((job) => (
                    <JobRow key={job.id} job={job} />
                  ))}
                </div>
              </ScrollArea>
            )}
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

export default ProcessorPane
