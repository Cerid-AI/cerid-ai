// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import {
  CheckCircle,
  AlertCircle,
  XCircle,
  MinusCircle,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  type LucideIcon,
} from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import type { TrustComponent, TrustScore, ComponentStatus } from "@/lib/types/trust-score"
import { getBandDisplay, COMPONENT_META } from "@/lib/types/trust-score"

interface TrustScoreModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  data: TrustScore
}

function StatusPill({ status }: { status: ComponentStatus }) {
  const configs: Record<ComponentStatus, { label: string; className: string; Icon: LucideIcon }> = {
    ok: { label: "OK", className: "bg-green-500/15 text-green-700 dark:text-green-400", Icon: CheckCircle },
    warn: { label: "Warning", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400", Icon: AlertCircle },
    fail: { label: "Failing", className: "bg-red-500/15 text-red-700 dark:text-red-400", Icon: XCircle },
    not_available: { label: "Not available", className: "bg-muted text-muted-foreground", Icon: MinusCircle },
  }
  const { label, className, Icon } = configs[status]
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", className)}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label}
    </span>
  )
}

function formatComponentValue(comp: TrustComponent): string {
  if (comp.value === null) return "—"
  if (comp.note && /^\d+\/\d+$/.test(comp.note)) return comp.note
  const pctIds = ["verification_coverage", "memory_recall", "faithfulness", "retrieval_ndcg10", "user_agreement"]
  if (pctIds.includes(comp.id)) return `${Math.round(comp.value * 100)}%`
  return comp.value.toFixed(3)
}

function formatComponentTarget(comp: TrustComponent): string {
  if (comp.target === null) return "—"
  const pctIds = ["verification_coverage", "memory_recall", "faithfulness", "retrieval_ndcg10", "user_agreement"]
  if (pctIds.includes(comp.id)) return `${Math.round(comp.target * 100)}%`
  if (comp.id === "preservation_health") return "100%"
  return comp.target.toFixed(2)
}

function ComponentTab({ comp }: { comp: TrustComponent }) {
  const [calcOpen, setCalcOpen] = useState(false)
  const meta = COMPONENT_META[comp.id]

  return (
    <div className="space-y-4">
      {/* Value row */}
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <p className="text-label-xs uppercase tracking-wider text-muted-foreground">Current</p>
          <p className="text-2xl font-semibold tabular-nums">{formatComponentValue(comp)}</p>
        </div>
        <div>
          <p className="text-label-xs uppercase tracking-wider text-muted-foreground">Target</p>
          <p className="text-2xl font-semibold tabular-nums text-muted-foreground">
            {formatComponentTarget(comp)}
          </p>
        </div>
        <div className="ml-auto">
          <StatusPill status={comp.status} />
        </div>
      </div>

      {/* Sparkline — V-P2.2: the entire trend section is hidden until the
          backend ships per-component history. Re-introduce when the
          TrustComponent type gains a `history` field; render <Sparkline> with
          that data. The dashed "Insufficient history" placeholder was just
          permanent visual noise on every tab. */}
      {/* history?: SparkPoint[] — wire up when API lands. */}

      {/* Source note */}
      {comp.note && (
        <p className="text-xs text-muted-foreground">Note: {comp.note}</p>
      )}

      {/* Docs link */}
      {meta && (
        <a
          href={meta.docsHref}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Source documentation
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      )}

      {/* Collapsible: How is this calculated? */}
      {meta && (
        <div className="rounded-md border border-border">
          <button
            type="button"
            onClick={() => setCalcOpen((o) => !o)}
            className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-foreground/80 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-expanded={calcOpen}
          >
            How is this calculated?
            {calcOpen ? (
              <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            )}
          </button>
          {calcOpen && (
            <div className="border-t border-border px-3 py-2">
              <p className="text-xs text-muted-foreground">{meta.calculation}</p>
            </div>
          )}
        </div>
      )}

      {/* When this drops */}
      {meta && (
        <div className="rounded-md bg-muted/50 px-3 py-2">
          <p className="text-label-xs font-semibold uppercase tracking-wider text-muted-foreground">
            When this drops
          </p>
          <p className="mt-0.5 text-xs text-foreground/80">{meta.whenDrops}</p>
        </div>
      )}
    </div>
  )
}

export function TrustScoreModal({ open, onOpenChange, data }: TrustScoreModalProps) {
  const display = getBandDisplay(data.band)

  // Filter to components that are plausibly renderable (all, including not_available)
  const visibleComponents = data.components

  const defaultTab = visibleComponents[0]?.id ?? "faithfulness"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          {/* V-P1.5: keep DialogTitle a plain text node so screen readers
              announce the dialog as "Cerid Trust Score". The numeric score
              and band live in their own sub-row beneath the title. */}
          <DialogTitle>Cerid Trust Score</DialogTitle>
          <DialogDescription>
            {data.note ??
              "Score is the straight mean of normalized component values. No learned weights. Components with 'not_available' status are excluded from the mean."}
          </DialogDescription>
        </DialogHeader>

        {/* Score + band sub-row */}
        <div className="flex items-center gap-3">
          <span className="text-3xl font-bold tabular-nums" aria-label={`Current score: ${data.score ?? "unavailable"}`}>
            {data.score !== null ? data.score : "—"}
          </span>
          <span
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-sm font-semibold",
              display.bgClass,
              display.borderClass,
              display.textClass,
            )}
          >
            {display.label}
          </span>
        </div>

        {visibleComponents.length > 0 ? (
          <Tabs defaultValue={defaultTab} className="mt-2 min-w-0">
            {/* min-w-0 (Tabs) + w-full (TabsList): DialogContent is a CSS grid;
                without min-w-0 the grid item's default min-width:auto lets the
                non-wrapping TabsList force the track to its content width
                (~800px), overflowing the dialog and stretching every sibling.
                These cap the track at the dialog width so the overflow-x-auto
                below actually scrolls.
                V-P2.3: horizontally scroll the tab strip when 6+ components
                appear (flex-wrap produced a second un-separated row). */}
            <TabsList className="flex h-auto w-full flex-nowrap gap-1 overflow-x-auto bg-muted/50 p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {visibleComponents.map((comp) => (
                <TabsTrigger key={comp.id} value={comp.id} className="shrink-0 text-xs">
                  {comp.label}
                </TabsTrigger>
              ))}
            </TabsList>
            {visibleComponents.map((comp) => (
              <TabsContent key={comp.id} value={comp.id} className="mt-4">
                <ComponentTab comp={comp} />
              </TabsContent>
            ))}
          </Tabs>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No component data available.
          </p>
        )}

        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  )
}
