// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { EmptyState } from "@/components/ui/empty-state"
import { Clock } from "lucide-react"
import { humanizeTrigger } from "@/lib/humanize-trigger"
import type { SchedulerStatus as SchedulerStatusType } from "@/lib/types"

interface SchedulerStatusProps {
  scheduler: SchedulerStatusType | undefined
}

export function SchedulerStatus({ scheduler }: SchedulerStatusProps) {
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
            {scheduler.jobs.map((job) => (
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
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}