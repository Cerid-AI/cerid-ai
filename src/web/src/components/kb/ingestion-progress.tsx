// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  FileText,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Clock,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { fetchIngestionProgress } from "@/lib/api"
import type { IngestionFileProgress, IngestionStep } from "@/lib/types"

const STEP_LABELS: Record<IngestionStep, string> = {
  parsing: "Parsing",
  chunking: "Chunking",
  embedding: "Embedding",
  indexing: "Indexing",
}

const STEP_ORDER: IngestionStep[] = ["parsing", "chunking", "embedding", "indexing"]

function StepIndicator({ currentStep }: { currentStep: IngestionStep }) {
  const currentIdx = STEP_ORDER.indexOf(currentStep)

  return (
    <div className="flex items-center gap-0.5">
      {STEP_ORDER.map((step, idx) => {
        const isActive = idx === currentIdx
        const isDone = idx < currentIdx
        return (
          <div key={step} className="flex items-center gap-0.5">
            <span
              className={cn(
                "text-[8px] px-1 py-0 rounded",
                isActive && "bg-primary/20 text-primary font-medium",
                isDone && "text-green-600 dark:text-green-400 line-through",
                !isActive && !isDone && "text-muted-foreground",
              )}
            >
              {STEP_LABELS[step]}
            </span>
            {idx < STEP_ORDER.length - 1 && (
              <span className="text-muted-foreground text-[8px]">&middot;</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function FileProgressItem({ file }: { file: IngestionFileProgress }) {
  const StatusIcon = {
    pending: Clock,
    processing: Loader2,
    done: CheckCircle2,
    error: AlertCircle,
  }[file.status]

  const statusColor = {
    pending: "text-muted-foreground",
    processing: "text-primary animate-spin",
    done: "text-green-500",
    error: "text-destructive",
  }[file.status]

  return (
    <div className="flex items-start gap-2 rounded-md border px-2.5 py-2">
      <StatusIcon className={cn("h-3.5 w-3.5 shrink-0 mt-0.5", statusColor)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="truncate text-[11px] font-medium">{file.filename}</p>
          {file.status === "processing" && (
            <span className="shrink-0 text-[10px] tabular-nums text-primary font-medium">
              {Math.round(file.progress)}%
            </span>
          )}
          {file.status === "done" && (
            <Badge variant="secondary" className="text-[9px] px-1.5 py-0 bg-green-500/10 text-green-600 dark:text-green-400">
              Done
            </Badge>
          )}
        </div>

        {file.status === "processing" && (
          <>
            <StepIndicator currentStep={file.step} />
            {/* Progress bar */}
            <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
                style={{ width: `${file.progress}%` }}
              />
            </div>
          </>
        )}

        {file.status === "error" && file.error && (
          <p className="mt-0.5 text-[10px] text-destructive">{file.error}</p>
        )}
      </div>
    </div>
  )
}

interface IngestionProgressProps {
  className?: string
}

export function IngestionProgress({ className }: IngestionProgressProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["ingestion-progress"],
    queryFn: fetchIngestionProgress,
    refetchInterval: (query) => {
      const d = query.state.data
      if (!d || d.total_files === 0) return 10_000 // idle: slow poll
      const hasActive = d.files.some((f) => f.status === "processing" || f.status === "pending")
      return hasActive ? 2_000 : 10_000 // active: fast poll
    },
    staleTime: 1000,
  })

  // Don't render anything if there's no active ingestion
  if (isLoading || !data || data.total_files === 0) return null

  const hasActive = data.files.some((f) => f.status === "processing" || f.status === "pending")
  if (!hasActive) return null

  return (
    <div className={cn("border-b", className)}>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <Loader2 className="h-3 w-3 animate-spin text-primary" />
        <span className="flex-1 text-[11px] font-medium">
          Ingesting {data.completed_files}/{data.total_files} files
        </span>
        <Badge variant="outline" className="text-[9px] px-1.5 py-0">
          <FileText className="mr-0.5 h-2.5 w-2.5" />
          {data.total_files - data.completed_files} remaining
        </Badge>
      </div>

      {/* Overall progress bar */}
      <div className="px-3 pb-1.5">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
            style={{ width: `${data.total_files > 0 ? (data.completed_files / data.total_files) * 100 : 0}%` }}
          />
        </div>
      </div>

      {/* Per-file queue */}
      <ScrollArea className="max-h-48 px-3 pb-2">
        <div className="space-y-1">
          {data.files.map((file, idx) => (
            <FileProgressItem key={`${file.filename}-${idx}`} file={file} />
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
