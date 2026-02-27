// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { ArrowRightLeft } from "lucide-react"
import { ModelBadge } from "./message-bubble"

interface ModelSwitchDividerProps {
  fromModelId: string
  toModelId: string
}

export function ModelSwitchDivider({ fromModelId, toModelId }: ModelSwitchDividerProps) {
  return (
    <div className="flex items-center gap-3 py-3">
      <div className="h-px flex-1 bg-border" />
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <ArrowRightLeft className="h-3 w-3" />
        <span>Switched from</span>
        <ModelBadge modelId={fromModelId} />
        <span>to</span>
        <ModelBadge modelId={toModelId} />
      </div>
      <div className="h-px flex-1 bg-border" />
    </div>
  )
}
