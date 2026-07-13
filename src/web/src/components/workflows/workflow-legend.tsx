// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * WorkflowLegend — compact node-type key rendered under the builder canvas.
 * Each entry mirrors the canvas colour coding and explains the category on
 * hover/focus, so the canvas is legible without prior knowledge.
 */
import type { WorkflowNodeCatalog } from "@/lib/api/workflows"
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { FALLBACK_NODE_CATALOG } from "./node-catalog"

// Mirrors NODE_COLORS in workflow-canvas.tsx (fill/stroke classes there, bg/border here).
const LEGEND_SWATCHES: Record<string, string> = {
  agent: "bg-teal-500/15 border-teal-500",
  parser: "bg-blue-500/15 border-blue-500",
  tool: "bg-purple-500/15 border-purple-500",
  condition: "bg-amber-500/15 border-amber-500",
}

interface WorkflowLegendProps {
  catalog?: WorkflowNodeCatalog
  className?: string
}

export default function WorkflowLegend({ catalog = FALLBACK_NODE_CATALOG, className }: WorkflowLegendProps) {
  return (
    <TooltipProvider delayDuration={200}>
      <div
        role="group"
        aria-label="Node type legend"
        className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 text-label-xs text-muted-foreground", className)}
      >
        <span className="font-medium">Node types:</span>
        {catalog.node_types.map((t) => (
          <Tooltip key={t.type}>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded px-1 py-0.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-2.5 w-2.5 rounded-sm border",
                    LEGEND_SWATCHES[t.type] ?? LEGEND_SWATCHES.agent,
                    t.type === "condition" && "border-dashed",
                  )}
                />
                {t.label}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
              <p className="font-medium">{t.label}</p>
              <p className="mt-1 text-muted-foreground dark:text-muted-foreground/90">{t.description}</p>
              {t.config_schema_summary && (
                <p className="mt-1 text-muted-foreground dark:text-muted-foreground/90">Config: {t.config_schema_summary}</p>
              )}
            </TooltipContent>
          </Tooltip>
        ))}
        <span className="text-muted-foreground/70">
          Hover a node for its purpose · click a node to inspect it
        </span>
      </div>
    </TooltipProvider>
  )
}
