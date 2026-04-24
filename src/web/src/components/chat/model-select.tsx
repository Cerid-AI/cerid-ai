// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import { MODELS } from "@/lib/types"
import type { ModelCapabilities } from "@/lib/types"
import { formatCost } from "@/lib/utils"
import { estimateTurnCost } from "@/lib/model-router"

interface ModelSelectProps {
  value: string
  onChange: (model: string) => void
}

function topCapability(caps: ModelCapabilities): string {
  const entries: [string, number][] = [
    ["code", caps.coding],
    ["reason", caps.reasoning],
    ["create", caps.creative],
    ["facts", caps.factual],
  ]
  entries.sort((a, b) => b[1] - a[1])
  return entries[0][0]
}

export function ModelSelect({ value, onChange }: ModelSelectProps) {
  const selectedModel = MODELS.find((m) => m.id === value)
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-48">
        <span className="truncate">{selectedModel?.label ?? "Select model"}</span>
      </SelectTrigger>
      <SelectContent position="popper" className="min-w-[20rem]">
        {MODELS.map((m) => {
          const cost = estimateTurnCost(m, 2000, 500)
          const top = m.capabilities ? topCapability(m.capabilities) : null
          return (
            <SelectItem key={m.id} value={m.id}>
              <span className="truncate">{m.label}</span>
              <span className="ml-2 shrink-0 text-xs text-muted-foreground">{m.provider}</span>
              {top && (
                <span className="ml-1.5 shrink-0 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                  {top}
                </span>
              )}
              <span className="ml-1.5 shrink-0 text-[10px] text-muted-foreground">
                ~{formatCost(cost)}
              </span>
            </SelectItem>
          )
        })}
        <div className="border-t px-2 py-1.5 text-[9px] text-muted-foreground/80">
          All models via OpenRouter. Non-US models accessible but not bundled by default.
        </div>
      </SelectContent>
    </Select>
  )
}
