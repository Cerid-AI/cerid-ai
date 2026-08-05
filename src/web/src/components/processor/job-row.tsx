// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * JobRow — single row in the processor Recent list.
 *
 * Shows: state pill, job_type, priority chip, age-of-completion, cost.
 * Accessible: state conveyed by text + icon, not colour alone.
 */

import { cn } from "@/lib/utils"
import { formatCost } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  CheckCircle2,
  XCircle,
  Clock3,
  Loader2,
  PauseCircle,
} from "lucide-react"
import type { JobRecord, JobState } from "@/lib/types/processor"

// ---------------------------------------------------------------------------
// State pill helpers
// ---------------------------------------------------------------------------

interface StateMeta {
  label: string
  icon: React.ReactNode
  className: string
}

function getStateMeta(state: JobState | string): StateMeta {
  switch (state) {
    case "completed":
      return {
        label: "Done",
        icon: <CheckCircle2 className="h-3 w-3 shrink-0" aria-hidden="true" />,
        className: "border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-300",
      }
    case "failed":
      return {
        label: "Failed",
        icon: <XCircle className="h-3 w-3 shrink-0" aria-hidden="true" />,
        className: "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300",
      }
    case "running":
      return {
        label: "Running",
        icon: <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden="true" />,
        className: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300",
      }
    case "paused":
      return {
        label: "Paused",
        icon: <PauseCircle className="h-3 w-3 shrink-0" aria-hidden="true" />,
        className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
      }
    default: // pending
      return {
        label: "Pending",
        icon: <Clock3 className="h-3 w-3 shrink-0" aria-hidden="true" />,
        className: "border-muted bg-muted/40 text-muted-foreground",
      }
  }
}

// ---------------------------------------------------------------------------
// Relative time — inline, no date-fns dependency
// ---------------------------------------------------------------------------

function formatAge(iso: string | null): string {
  if (!iso) return "—"
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 0) return "just now"
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface JobRowProps {
  job: JobRecord
}

export function JobRow({ job }: JobRowProps) {
  const meta = getStateMeta(job.state)
  const ageTimestamp = job.completed_at ?? job.started_at ?? job.enqueued_at
  // Cost is approximated from actual token counts when available.
  // Backend does not include a pre-computed cost field in JobRecord.to_dict().
  const cost: number | null = null

  return (
    <div
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/50"
      role="row"
      aria-label={`Job ${job.job_type}, state ${meta.label}`}
    >
      {/* State pill */}
      <span
        className={cn(
          "inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 font-medium",
          meta.className,
        )}
        aria-label={`State: ${meta.label}`}
      >
        {meta.icon}
        <span>{meta.label}</span>
      </span>

      {/* Job type — truncated */}
      <span className="min-w-0 flex-1 truncate font-medium text-foreground" title={job.job_type}>
        {job.job_type}
      </span>

      {/* Priority chip */}
      <Badge
        variant="outline"
        className="shrink-0 text-label-xs capitalize"
        aria-label={`Priority: ${job.priority}`}
      >
        {job.priority}
      </Badge>

      {/* Age */}
      <span className="shrink-0 text-muted-foreground" title={ageTimestamp ?? undefined}>
        {formatAge(ageTimestamp)}
      </span>

      {/* Cost */}
      {cost !== null ? (
        <span className="shrink-0 tabular-nums text-muted-foreground">
          {formatCost(cost)}
        </span>
      ) : (
        <span className="shrink-0 text-muted-foreground/50">—</span>
      )}
    </div>
  )
}
