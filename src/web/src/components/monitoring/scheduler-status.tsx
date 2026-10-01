// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/ui/empty-state"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Clock, Loader2, RefreshCw } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { humanizeTrigger } from "@/lib/humanize-trigger"
import { triggerSchedulerJob } from "@/lib/api/kb"
import type { SchedulerRun, SchedulerStatus as SchedulerStatusType } from "@/lib/types"

interface SchedulerStatusProps {
  scheduler: SchedulerStatusType | undefined
  runs?: SchedulerRun[]
  runsError?: boolean
}

const RUN_STATUS_STYLES: Record<string, string> = {
  success: "bg-green-500/10 text-green-700 dark:text-green-400",
  partial: "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400",
  error: "bg-red-500/10 text-red-700 dark:text-red-400",
}

// Query keys to refetch after a job runs, so the surfaces it feeds update
// immediately ("a refresh gets a refresh"). The scheduler card itself always
// refreshes; viz-producing jobs also bust their downstream query.
const JOB_AFFECTED_QUERIES: Record<string, string[]> = {
  compute_umap_3d: ["constellation-embeddings-3d"],
  community_refresh: ["constellation-embeddings-3d"],
}

export function SchedulerStatus({ scheduler, runs, runsError }: SchedulerStatusProps) {
  const queryClient = useQueryClient()

  const runJob = useMutation({
    mutationFn: (jobId: string) => triggerSchedulerJob(jobId),
    onSuccess: (result) => {
      if (result.status === "collapsed_into_pending") {
        // Nothing new ran — saying "Running" here would repeat the false
        // success SF-2 diagnosed. No cache busting either: the data is
        // unchanged until the already-queued run completes.
        toast.info(`“${result.name}” is already queued`, {
          description: "An equivalent run is pending or running — no new run was started.",
        })
        return
      }
      toast.success(`Running “${result.name}”`, {
        description: "Refreshing in the background — surfaces update when it finishes.",
      })
      queryClient.invalidateQueries({ queryKey: ["scheduler"] })
      for (const key of JOB_AFFECTED_QUERIES[result.id] ?? []) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
    },
    onError: (err: unknown) => {
      toast.error("Couldn’t run job", {
        description: err instanceof Error ? err.message : String(err),
      })
    },
  })

  if (!scheduler) return <EmptyState icon={Clock} title="No scheduler data" description="Scheduler status appears when the service is running" />

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 space-y-0 p-3 pb-0">
        <Clock className="h-4 w-4 text-muted-foreground" />
        <CardTitle className="text-sm">Scheduled Jobs</CardTitle>
        <Badge variant={scheduler.status === "running" ? "default" : "outline"} className="ml-auto text-xs">
          {scheduler.status}
        </Badge>
      </CardHeader>
      <CardContent className="p-3">
        {scheduler.jobs.length === 0 ? (
          <p className="text-xs text-muted-foreground">No scheduled jobs</p>
        ) : (
          <div className="max-h-48 space-y-1.5 overflow-y-auto">
            {scheduler.jobs.map((job) => {
              const isRunning = runJob.isPending && runJob.variables === job.id
              return (
                <div key={job.id} className="flex items-center gap-2 text-xs">
                  <span className="min-w-0 flex-1 truncate font-medium">{job.name}</span>
                  <span className="shrink-0 text-muted-foreground" title={job.trigger}>
                    {humanizeTrigger(job.trigger)}
                  </span>
                  {job.next_run && (
                    <span className="shrink-0 text-muted-foreground" title={job.next_run}>
                      Next: {new Date(job.next_run).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    </span>
                  )}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 shrink-0"
                        disabled={runJob.isPending}
                        onClick={() => runJob.mutate(job.id)}
                        aria-label={`Run ${job.name} now`}
                      >
                        {isRunning ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="h-3.5 w-3.5" />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Run now</TooltipContent>
                  </Tooltip>
                </div>
              )
            })}
          </div>
        )}
        {runsError ? (
          <p className="mt-3 text-xs text-muted-foreground">Recent runs could not be loaded.</p>
        ) : runs && runs.length > 0 ? (
          <div className="mt-3 border-t pt-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">Recent runs</p>
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {runs.map((run, i) => (
                <div key={`${run.timestamp}-${i}`} className="flex items-center gap-2 text-xs">
                  <Badge variant="outline" className={`text-label-xs ${RUN_STATUS_STYLES[run.status] ?? ""}`}>
                    {run.status}
                  </Badge>
                  <span className="shrink-0 font-medium">{run.job}</span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground" title={run.detail}>
                    {run.detail}
                  </span>
                  <span className="shrink-0 text-muted-foreground" title={run.timestamp}>
                    {new Date(run.timestamp).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
