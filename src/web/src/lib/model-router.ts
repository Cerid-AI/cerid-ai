// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Model Router — client-side intelligence for model selection.
 *
 * Evaluates query complexity, conversation depth, and cost sensitivity
 * to recommend the most cost-effective model for each message.
 */

import {
  MODELS,
  type ModelOption,
  type ChatMessage,
  type ModelRecommendation,
  type SwitchCostEstimate,
  type SwitchStrategy,
  type ModelSwitchOptions,
} from "./types"

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
  // Empty or whitespace-only queries are simple
  if (!query.trim()) return "simple"

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
    "openrouter/x-ai/grok-4.1-fast",           // $0.20/$0.50 — best value, 2M ctx
    "openrouter/openai/gpt-4o-mini",            // $0.15/$0.60 — cheapest input
    "openrouter/meta-llama/llama-3.3-70b-instruct", // $0.10/$0.32 — cheapest overall
    "openrouter/deepseek/deepseek-chat-v3-0324", // $0.20/$0.77 — strong coding
  ],
  medium: [
    "openrouter/google/gemini-3-flash-preview",  // $0.50/$3.00 — good balance
    "openrouter/google/gemini-2.5-flash",        // $0.30/$2.50 — 1M context
    "openrouter/deepseek/deepseek-chat-v3-0324", // $0.20/$0.77 — strong coding
    "openrouter/anthropic/claude-sonnet-4.6",    // $3.00/$15.00 — frontier
  ],
  complex: [
    "openrouter/anthropic/claude-sonnet-4.6",    // $3.00/$15.00 — best coding
    "openrouter/anthropic/claude-opus-4.6",      // $5.00/$25.00 — deepest reasoning
    "openrouter/openai/o3-mini",                 // $1.10/$4.40 — reasoning specialist
    "openrouter/x-ai/grok-4.1-fast",            // $0.20/$0.50 — web search + huge ctx
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

  const contextChars = conversationMessages.reduce((sum, m) => sum + m.content.length, 0) + query.length
  const estimatedOutput = complexity === "simple" ? 200 : complexity === "medium" ? 500 : 1000

  const currentCost = estimateTurnCost(currentModel, contextChars, estimatedOutput)

  // Find the cheapest viable candidate from the tier
  let bestModel = currentModel
  let bestCost = currentCost

  for (const candidateId of candidateIds) {
    const candidate = MODELS.find((m) => m.id === candidateId)
    if (!candidate) continue

    // Skip if context would exceed model's window
    const estimatedInputTokens = Math.ceil(
      (conversationMessages.map((m) => m.content).join("") + query).length / 4,
    )
    if (estimatedInputTokens > candidate.contextWindow * 0.8) continue

    const candidateCost = estimateTurnCost(candidate, contextChars, estimatedOutput)

    if (candidateCost < bestCost) {
      bestModel = candidate
      bestCost = candidateCost
    }
  }

  // Apply sensitivity: only switch if savings exceed threshold
  // High: switch for small savings (>5%), Medium: moderate (>20%), Low: large (>50%)
  const minSavingsRatio = costSensitivity === "high" ? 0.05 : costSensitivity === "low" ? 0.5 : 0.2
  const savingsRatio = currentCost > 0 ? (currentCost - bestCost) / currentCost : 0

  if (savingsRatio < minSavingsRatio) {
    bestModel = currentModel
    bestCost = currentCost
  }

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

// ---------------------------------------------------------------------------
// Model switch cost estimation
// ---------------------------------------------------------------------------

/**
 * Calculate costs of switching to a different model, accounting for
 * full history replay, summarization, and context window fit.
 */
export function calculateSwitchCost(
  targetModel: ModelOption,
  currentModel: ModelOption,
  conversationMessages: ChatMessage[],
): SwitchCostEstimate {
  const historyText = conversationMessages.map((m) => m.content).join("")
  const historyTokens = Math.ceil(historyText.length / 4)

  // Cost to replay full history on target model + one response
  const replayCost =
    (historyTokens / 1_000_000) * targetModel.inputCostPer1M +
    (500 / 1_000_000) * targetModel.outputCostPer1M

  // Cost of next turn staying on current model
  const currentNextTurnCost =
    (historyTokens / 1_000_000) * currentModel.inputCostPer1M +
    (500 / 1_000_000) * currentModel.outputCostPer1M

  // Summarization: ~10% of original tokens (min 200)
  const summarizedTokens = Math.max(200, Math.ceil(historyTokens * 0.1))
  const summarizeCost =
    // Step 1: current model reads history → produces summary
    (historyTokens / 1_000_000) * currentModel.inputCostPer1M +
    (summarizedTokens / 1_000_000) * currentModel.outputCostPer1M +
    // Step 2: target model reads summary → produces response
    (summarizedTokens / 1_000_000) * targetModel.inputCostPer1M +
    (500 / 1_000_000) * targetModel.outputCostPer1M

  const exceedsTargetContext = historyTokens > targetModel.contextWindow * 0.8

  return {
    replayCost,
    currentNextTurnCost,
    summarizeCost,
    historyTokens,
    summarizedTokens,
    exceedsTargetContext,
  }
}

/**
 * Build the set of switch strategies available for a model switch,
 * with a recommended default.
 */
export function buildSwitchOptions(
  targetModel: ModelOption,
  currentModel: ModelOption,
  messages: ChatMessage[],
): ModelSwitchOptions {
  const costEstimate = calculateSwitchCost(targetModel, currentModel, messages)

  const strategies: SwitchStrategy[] = ["continue", "fresh"]

  // Only offer summarize if there's enough conversation to justify it
  if (messages.length >= 6) {
    strategies.splice(1, 0, "summarize")
  }

  let recommended: SwitchStrategy = "continue"
  if (costEstimate.exceedsTargetContext) {
    recommended = messages.length >= 6 ? "summarize" : "fresh"
  } else if (
    costEstimate.replayCost > costEstimate.summarizeCost * 1.5 &&
    messages.length >= 6
  ) {
    recommended = "summarize"
  }

  return { targetModel, costEstimate, strategies, recommended }
}