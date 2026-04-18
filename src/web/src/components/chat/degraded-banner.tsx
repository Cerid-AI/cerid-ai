// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"

interface Props {
  reason: string
  className?: string
}

/**
 * Per-message banner shown at the top of an assistant response when the
 * retrieval pipeline exceeded its time budget and had to return an
 * ungrounded answer (backend sets `degraded_reason` on the query envelope).
 *
 * Distinct from {@link ./degradation-banner} which reflects system-wide
 * service tier degradation. This one is per-query and is dismissed
 * automatically by re-sending.
 */
export function DegradedBanner({ reason, className }: Props) {
  if (!reason) return null
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-start gap-2 rounded-md border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-200",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0">
        <div className="font-medium">Retrieval budget exceeded — answer is ungrounded.</div>
        <div className="opacity-80">{reason}</div>
      </div>
    </div>
  )
}
