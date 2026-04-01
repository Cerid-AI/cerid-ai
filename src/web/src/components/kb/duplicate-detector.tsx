// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  Loader2,
  AlertCircle,
  Copy,
  Merge,
  XCircle,
  Layers,
  CheckCircle2,
} from "lucide-react"
import { DomainBadge } from "@/components/ui/domain-badge"
import { QualityDot } from "./quality-dot"
import { fetchDuplicates, mergeDuplicates, dismissDuplicate } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { DuplicateGroup } from "@/lib/types"

interface DuplicateDetectorProps {
  open: boolean
  onClose: () => void
}

function DuplicateGroupCard({
  group,
  onMerge,
  onDismiss,
}: {
  group: DuplicateGroup
  onMerge: (keepId: string, removeIds: string[]) => Promise<void>
  onDismiss: (ids: string[]) => Promise<void>
}) {
  const [merging, setMerging] = useState(false)
  const [dismissing, setDismissing] = useState(false)

  // Pick the best artifact (highest quality score)
  const sorted = [...group.artifacts].sort(
    (a, b) => (b.quality_score ?? 0) - (a.quality_score ?? 0),
  )
  const best = sorted[0]
  const others = sorted.slice(1)

  const handleMerge = async () => {
    if (!best) return
    setMerging(true)
    try {
      await onMerge(
        best.id,
        others.map((a) => a.id),
      )
    } finally {
      setMerging(false)
    }
  }

  const handleDismiss = async () => {
    setDismissing(true)
    try {
      await onDismiss(group.artifacts.map((a) => a.id))
    } finally {
      setDismissing(false)
    }
  }

  return (
    <Card className="py-2 gap-1">
      <CardContent className="px-3 py-2">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Copy className="h-3.5 w-3.5 text-amber-500" />
            <span className="text-xs font-medium">
              {group.artifacts.length} duplicates
            </span>
            <Badge
              variant="outline"
              className="text-[9px] px-1.5 py-0"
            >
              {Math.round(group.similarity * 100)}% similar
            </Badge>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="xs"
              className="h-6 text-[10px] gap-1"
              disabled={merging || dismissing}
              onClick={handleMerge}
            >
              {merging ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Merge className="h-3 w-3" />
              )}
              Merge (keep best)
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className="h-6 text-[10px] gap-1"
              disabled={merging || dismissing}
              onClick={handleDismiss}
            >
              {dismissing ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <XCircle className="h-3 w-3" />
              )}
              Dismiss
            </Button>
          </div>
        </div>

        <div className="space-y-1.5">
          {sorted.map((artifact, idx) => (
            <div
              key={artifact.id}
              className={cn(
                "flex items-start gap-2 rounded-md border px-2.5 py-1.5",
                idx === 0 && "border-green-500/40 bg-green-500/5",
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  {idx === 0 && (
                    <CheckCircle2 className="h-3 w-3 shrink-0 text-green-500" />
                  )}
                  <p className="truncate text-[11px] font-medium">
                    {artifact.filename}
                  </p>
                  {idx === 0 && (
                    <Badge
                      variant="secondary"
                      className="text-[8px] px-1 py-0 bg-green-500/10 text-green-600 dark:text-green-400"
                    >
                      KEEP
                    </Badge>
                  )}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
                  <DomainBadge domain={artifact.domain} />
                  <span className="flex items-center gap-0.5">
                    <Layers className="h-2.5 w-2.5" />
                    {artifact.chunk_count} chunks
                  </span>
                  <QualityDot score={artifact.quality_score} />
                  {artifact.quality_score != null && (
                    <span>Q{Math.round(artifact.quality_score * 100)}</span>
                  )}
                </div>
                {artifact.summary && (
                  <p className="mt-1 text-[10px] text-muted-foreground line-clamp-2">
                    {artifact.summary}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function DuplicateDetector({ open, onClose }: DuplicateDetectorProps) {
  const queryClient = useQueryClient()

  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ["kb-duplicates"],
    queryFn: () => fetchDuplicates(0.85),
    enabled: open,
    staleTime: 30_000,
  })

  const handleMerge = async (keepId: string, removeIds: string[]) => {
    await mergeDuplicates(keepId, removeIds)
    queryClient.invalidateQueries({ queryKey: ["kb-duplicates"] })
    queryClient.invalidateQueries({ queryKey: ["artifacts"] })
  }

  const handleDismiss = async (ids: string[]) => {
    await dismissDuplicate(ids)
    queryClient.invalidateQueries({ queryKey: ["kb-duplicates"] })
  }

  const groups = data?.groups ?? []

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) onClose()
      }}
    >
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Copy className="h-4 w-4 text-amber-500" />
            Near-Duplicate Detection
          </DialogTitle>
          <DialogDescription>
            {groups.length > 0
              ? `Found ${groups.length} duplicate group${groups.length !== 1 ? "s" : ""}. Merge to keep the best quality artifact.`
              : "Scanning for duplicate content in your knowledge base."}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 p-1">
            {isLoading && (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                Scanning for duplicates...
              </div>
            )}

            {error && (
              <div className="flex flex-col items-center gap-2 py-16 text-center">
                <AlertCircle className="h-8 w-8 text-destructive" />
                <p className="text-sm text-destructive">
                  {error instanceof Error ? error.message : "Failed to scan"}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => refetch()}
                >
                  Retry
                </Button>
              </div>
            )}

            {!isLoading && !error && groups.length === 0 && (
              <div className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
                <CheckCircle2 className="h-8 w-8 text-green-500" />
                <p className="text-sm font-medium">No duplicates found</p>
                <p className="text-xs">Your knowledge base is clean.</p>
              </div>
            )}

            {groups.map((group, idx) => (
              <DuplicateGroupCard
                key={`${group.content_hash_prefix}-${idx}`}
                group={group}
                onMerge={handleMerge}
                onDismiss={handleDismiss}
              />
            ))}
          </div>
        </ScrollArea>

        <Separator />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
