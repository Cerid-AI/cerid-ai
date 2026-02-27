// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useMemo } from "react"
import { recommendModel } from "@/lib/model-router"
import { MODELS, type ModelOption, type ChatMessage, type ModelRecommendation } from "@/lib/types"

interface UseModelRouterOptions {
  enabled: boolean
  costSensitivity: "low" | "medium" | "high"
  currentModel: ModelOption
  messages: ChatMessage[]
  kbInjections: number
}

export function useModelRouter({
  enabled,
  costSensitivity,
  currentModel,
  messages,
  kbInjections,
}: UseModelRouterOptions) {
  const [dismissed, setDismissed] = useState(false)

  const recommendation = useMemo<ModelRecommendation | null>(() => {
    if (!enabled || dismissed) return null
    // Only recommend on the next message (use last user message as proxy)
    const lastUserMsg = messages.findLast((m) => m.role === "user")
    if (!lastUserMsg) return null

    const rec = recommendModel(
      lastUserMsg.content,
      currentModel,
      messages,
      kbInjections,
      costSensitivity,
    )

    // Only show if recommending a different model with meaningful savings
    if (rec.model.id === currentModel.id) return null
    if (rec.savingsVsCurrent < 0.0001) return null
    return rec
  }, [enabled, dismissed, messages, currentModel, kbInjections, costSensitivity])

  const dismiss = useCallback(() => setDismissed(true), [])

  const resetDismiss = useCallback(() => setDismissed(false), [])

  const getModelById = useCallback(
    (id: string) => MODELS.find((m) => m.id === id) ?? MODELS[0],
    [],
  )

  return { recommendation, dismiss, resetDismiss, getModelById }
}