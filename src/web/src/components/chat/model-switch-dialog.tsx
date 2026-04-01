// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { ArrowRightLeft, FileText, Trash2, Coins } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ModelBadge } from "./message-bubble"
import { cn } from "@/lib/utils"
import type { ModelSwitchOptions, SwitchStrategy } from "@/lib/types"

interface ModelSwitchDialogProps {
  options: ModelSwitchOptions
  currentModelId: string
  onSelect: (strategy: SwitchStrategy) => void
  onCancel: () => void
}

const STRATEGY_CONFIG: Record<
  SwitchStrategy,
  { label: string; description: string; icon: typeof ArrowRightLeft }
> = {
  continue: {
    label: "Continue with full history",
    description: "Replay entire conversation on the new model",
    icon: ArrowRightLeft,
  },
  summarize: {
    label: "Summarize and switch",
    description: "Compress history into a summary, then switch",
    icon: FileText,
  },
  fresh: {
    label: "Start fresh",
    description: "Clear history and start a new context",
    icon: Trash2,
  },
}

export function ModelSwitchDialog({
  options,
  currentModelId,
  onSelect,
  onCancel,
}: ModelSwitchDialogProps) {
  const { costEstimate, strategies, recommended } = options

  return (
    <div className="border-b bg-muted/30 px-4 py-3">
      {/* Header */}
      <div className="mb-2 flex items-center gap-2 text-xs">
        <ArrowRightLeft className="h-3.5 w-3.5" />
        <span>Switching from</span>
        <ModelBadge modelId={currentModelId} />
        <span>to</span>
        <ModelBadge modelId={options.targetModel.id} />
      </div>

      {/* Cost summary */}
      <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Coins className="h-3 w-3" />
        <span>History: ~{costEstimate.historyTokens.toLocaleString()} tokens</span>
        {costEstimate.exceedsTargetContext && (
          <Badge variant="destructive" className="px-1.5 py-0 text-[10px]">
            Exceeds context
          </Badge>
        )}
      </div>

      {/* Strategy buttons */}
      <div className="flex flex-col gap-1.5">
        {strategies.map((strategy) => {
          const config = STRATEGY_CONFIG[strategy]
          const Icon = config.icon
          const isRecommended = strategy === recommended
          const cost =
            strategy === "continue"
              ? costEstimate.replayCost
              : strategy === "summarize"
                ? costEstimate.summarizeCost
                : 0
          const isDisabled = costEstimate.exceedsTargetContext && strategy === "continue"

          return (
            <button
              key={strategy}
              onClick={() => onSelect(strategy)}
              disabled={isDisabled}
              className={cn(
                "flex items-center gap-3 rounded-lg border px-3 py-2 text-left text-xs transition-colors hover:bg-accent",
                isRecommended && "border-primary/50 bg-primary/5",
                isDisabled && "cursor-not-allowed opacity-50",
              )}
            >
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="flex-1">
                <div className="font-medium">
                  {config.label}
                  {isRecommended && (
                    <Badge variant="secondary" className="ml-1.5 px-1.5 py-0 text-[10px]">
                      Recommended
                    </Badge>
                  )}
                </div>
                <div className="text-muted-foreground">{config.description}</div>
              </div>
              {cost > 0 ? (
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  ~${cost.toFixed(4)}
                </span>
              ) : strategy === "fresh" ? (
                <span className="shrink-0 text-green-600 dark:text-green-400">Free</span>
              ) : null}
            </button>
          )
        })}
      </div>

      <div className="mt-2 flex justify-end">
        <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  )
}
