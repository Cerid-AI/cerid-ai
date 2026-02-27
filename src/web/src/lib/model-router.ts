// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Model Router — client-side intelligence for model selection.
 *
 * Evaluates query complexity, conversation depth, and cost sensitivity
 * to recommend the most cost-effective model for each message.
 */

import { MODELS, type ModelOption, type ChatMessage, type ModelRecommendation } from "./types"

// ---------------------------------------------------------------------------
// Complexity scoring
// ---------------------------------------------------------------------------

type Complexity = "simple" | "medium" | "complex"

const COMPLEX_INDICATORS = [
  /\b(explain|analyze|compare|evaluate|implement|architect|design|debug|refactor)\b/i,
  /\b(code|function|class|algorithm|database|api|schema)\b/i,
  /\b(why|how does|what causes|trade-?offs?)\b/i,
]

const SIMPLE_INDICATORS = [
  /^(hi|hello|hey|thanks|ok|yes|no|sure)\b/i,
  /\b(what is|define|list|name|when)\b/i,
]

export function scoreQueryComplexity(
  query: string,
  messageCount: number,
  kbInjectionCount: number,
): Complexity {
  // Long conversations tend to be more complex
  if (messageCount > 20) return "complex"

  // KB-injected context suggests research queries
  if (kbInjectionCount >= 3) return "complex"

  const complexScore = COMPLEX_INDICATORS.filter((r) => r.test(query)).length
  const simpleScore = SIMPLE_INDICATORS.filter((r) => r.test(query)).length

  // Very short queries are usually simple
  if (query.length < 30 && simpleScore > 0) return "simple"

  if (complexScore >= 2 || query.length > 500) return "complex"
  if (complexScore >= 1) return "medium"
  if (simpleScore > 0) return "simple"

  return "medium"
}

// ---------------------------------------------------------------------------
// Cost estimation
// ---------------------------------------------------------------------------

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4)
}

export function estimateTurnCost(
  model: ModelOption,
  inputChars: number,
  estimatedOutputTokens = 500,
): number {
  const inputTokens = Math.ceil(inputChars / 4)
  return (
    (inputTokens / 1_000_000) * model.inputCostPer1M +
    (estimatedOutputTokens / 1_000_000) * model.outputCostPer1M
  )
}

// ---------------------------------------------------------------------------
// Model recommendation
// ---------------------------------------------------------------------------

const TIER_MODELS: Record<Complexity, string[]> = {
  simple: [
    "openrouter/google/gemini-2.5-flash",
    "openrouter/openai/gpt-4o-mini",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/deepseek/deepseek-chat-v3-0324",
  ],
  medium: [
    "openrouter/openai/gpt-4o-mini",
    "openrouter/deepseek/deepseek-chat-v3-0324",
    "openrouter/openai/gpt-4o",
    "openrouter/anthropic/claude-sonnet-4",
  ],
  complex: [
    "openrouter/anthropic/claude-sonnet-4",
    "openrouter/openai/gpt-4o",
    "openrouter/x-ai/grok-4-fast",
  ],
}

export function recommendModel(
  query: string,
  currentModel: ModelOption,
  conversationMessages: ChatMessage[],
  kbInjections: number,
  costSensitivity: "low" | "medium" | "high" = "medium",
): ModelRecommendation {
  const complexity = scoreQueryComplexity(query, conversationMessages.length, kbInjections)

  const candidateIds = TIER_MODELS[complexity]
  const sensitivityMultiplier = costSensitivity === "high" ? 0.3 : costSensitivity === "low" ? 3.0 : 1.0

  const contextChars = conversationMessages.reduce((sum, m) => sum + m.content.length, 0) + query.length
  const estimatedOutput = complexity === "simple" ? 200 : complexity === "medium" ? 500 : 1000

  let bestModel = currentModel
  let bestCost = estimateTurnCost(currentModel, contextChars, estimatedOutput)

  for (const candidateId of candidateIds) {
    const candidate = MODELS.find((m) => m.id === candidateId)
    if (!candidate) continue

    // Skip if context would exceed model's window
    const estimatedInputTokens = estimateTokens(
      conversationMessages.map((m) => m.content).join("") + query,
    )
    if (estimatedInputTokens > candidate.contextWindow * 0.8) continue

    const candidateCost = estimateTurnCost(candidate, contextChars, estimatedOutput)

    // High sensitivity (0.3): bestCost * 0.3 = low threshold, eagerly switch to cheaper
    // Low sensitivity (3.0): bestCost * 3.0 = high threshold, reluctant to switch
    if (candidateCost < bestCost * sensitivityMultiplier) {
      bestModel = candidate
      bestCost = candidateCost
    }
  }

  const currentCost = estimateTurnCost(currentModel, contextChars, estimatedOutput)
  const savings = currentCost - bestCost

  let reasoning: string
  if (bestModel.id === currentModel.id) {
    reasoning = `${currentModel.label} is already optimal for this ${complexity} query`
  } else {
    reasoning = `${bestModel.label} is recommended for ${complexity} queries — saves ~$${savings.toFixed(4)}/turn`
  }

  return {
    model: bestModel,
    estimatedCost: bestCost,
    reasoning,
    savingsVsCurrent: Math.max(0, savings),
  }
}