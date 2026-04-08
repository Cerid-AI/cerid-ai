// Copyright (c) 2026 Cerid AI. All rights reserved.
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
import { TOKEN_CHARS_RATIO, estimateTokenCount, formatCost } from "./utils"

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

/** @internal — exported for test access only. Use `recommendModel` for public API. */
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
  const inputTokens = Math.ceil(inputChars / TOKEN_CHARS_RATIO)
  return (
    (inputTokens / 1_000_000) * model.inputCostPer1M +
    (estimatedOutputTokens / 1_000_000) * model.outputCostPer1M
  )
}

// ---------------------------------------------------------------------------
// Intent detection & capability scoring
// ---------------------------------------------------------------------------

const CODING_RE = /\b(code|function|class|algorithm|debug|refactor|implement|api|schema|typescript|python|javascript|react|sql|program|variable|loop|bug|compile|syntax|regex|endpoint)\b/i
const REASONING_RE = /\b(explain|analyze|compare|evaluate|why|how does|trade-?offs?|reason|logic|math|calculate|prove|solve|think|step.by.step)\b/i
const CREATIVE_RE = /\b(write|story|brainstorm|creative|poem|essay|draft|blog|article|copy|narrative|imagine|rewrite|rephrase)\b/i
const FACTUAL_RE = /\b(what is|define|when|where|list|name|who|facts|history|date|capital|population|tell me about)\b/i
const CURRENT_INFO_RE = /\b(latest|current|today|recent|news|202[5-9]|203\d|now|right now|this week|this month|as of|trending|what's new|what's happening)\b/i
const VISION_RE = /\b(image|photo|picture|screenshot|diagram|chart|graph|visual)\b/i

/**
 * Detect query intent as weighted capability dimensions (sum ≈ 1.0).
 * @internal — exported for test access only. Used internally by `recommendModel`.
 */
export function detectIntentWeights(query: string): Record<string, number> {
  const coding = CODING_RE.test(query) ? 0.5 : 0.1
  const reasoning = REASONING_RE.test(query) ? 0.5 : 0.15
  const creative = CREATIVE_RE.test(query) ? 0.4 : 0.1
  const factual = FACTUAL_RE.test(query) ? 0.3 : 0.15

  const total = coding + reasoning + creative + factual
  return {
    coding: coding / total,
    reasoning: reasoning / total,
    creative: creative / total,
    factual: factual / total,
  }
}

/**
 * Score a model's fitness for a query based on its capabilities and the
 * detected query intent. Returns 0–100.
 * @internal — exported for test access only.
 */
export function scoreModelForQuery(
  model: ModelOption,
  query: string,
): number {
  const caps = model.capabilities
  if (!caps) return 50

  const weights = detectIntentWeights(query)
  let score =
    weights.coding * caps.coding +
    weights.reasoning * caps.reasoning +
    weights.creative * caps.creative +
    weights.factual * caps.factual

  // Bonus for special capabilities relevant to the query
  if (caps.webSearch && CURRENT_INFO_RE.test(query)) score += 25
  if (caps.vision && VISION_RE.test(query)) score += 5

  return Math.min(100, score)
}

// Minimum savings ratio to trigger model switch per sensitivity level
const SAVINGS_THRESHOLD: Record<string, number> = {
  high: 0.05,   // Switch for small savings (>5%)
  medium: 0.2,  // Switch for moderate savings (>20%)
  low: 0.5,     // Switch only for large savings (>50%)
}

// Minimum capability score by complexity tier
const MIN_SCORE: Record<Complexity, number> = {
  simple: 60,
  medium: 70,
  complex: 80,
}

// ---------------------------------------------------------------------------
// Model recommendation
// ---------------------------------------------------------------------------

export function recommendModel(
  query: string,
  currentModel: ModelOption,
  conversationMessages: ChatMessage[],
  kbInjections: number,
  costSensitivity: "low" | "medium" | "high" = "medium",
): ModelRecommendation {
  const complexity = scoreQueryComplexity(query, conversationMessages.length, kbInjections)
  const minScore = MIN_SCORE[complexity]

  // For temporal queries, filter out models with stale knowledge cutoffs
  const isTemporalQuery = CURRENT_INFO_RE.test(query)
  const CUTOFF_MAX_AGE_MONTHS = 3

  const contextChars = conversationMessages.reduce((sum, m) => sum + m.content.length, 0) + query.length
  const estimatedOutput = complexity === "simple" ? 200 : complexity === "medium" ? 500 : 1000
  const estimatedInputTokens = Math.ceil(contextChars / TOKEN_CHARS_RATIO)

  const currentCost = estimateTurnCost(currentModel, contextChars, estimatedOutput)
  const currentScore = scoreModelForQuery(currentModel, query)

  // Find the cheapest model that meets the quality threshold
  let bestModel = currentModel
  let bestCost = currentCost

  for (const candidate of MODELS) {
    // Skip if context would exceed model's effective window
    if (estimatedInputTokens > candidate.effectiveContextWindow) continue

    // For temporal queries, skip models with stale knowledge cutoff
    if (isTemporalQuery && candidate.capabilities?.knowledgeCutoff) {
      const cutoffDate = new Date(candidate.capabilities.knowledgeCutoff + "-01")
      const ageMs = Date.now() - cutoffDate.getTime()
      const ageMonths = ageMs / (30 * 24 * 60 * 60 * 1000)
      if (ageMonths > CUTOFF_MAX_AGE_MONTHS) continue
    }

    const score = scoreModelForQuery(candidate, query)
    if (score < minScore) continue

    const candidateCost = estimateTurnCost(candidate, contextChars, estimatedOutput)
    if (candidateCost < bestCost) {
      bestModel = candidate
      bestCost = candidateCost
    }
  }

  // Apply sensitivity: only switch if savings exceed threshold
  const minSavingsRatio = SAVINGS_THRESHOLD[costSensitivity] ?? SAVINGS_THRESHOLD.medium
  const savingsRatio = currentCost > 0 ? (currentCost - bestCost) / currentCost : 0

  if (savingsRatio < minSavingsRatio) {
    bestModel = currentModel
    bestCost = currentCost
  }

  const savings = currentCost - bestCost
  const bestScore = scoreModelForQuery(bestModel, query)
  const detectedIntent = detectIntentWeights(query)
  const topIntent = Object.entries(detectedIntent).sort((a, b) => b[1] - a[1])[0][0]

  let reasoning: string
  if (bestModel.id === currentModel.id) {
    reasoning = `${currentModel.label} is already optimal for this ${complexity} query (score: ${Math.round(currentScore)})`
  } else {
    reasoning = `${bestModel.label} scores ${Math.round(bestScore)} for this ${topIntent} task — saves ~${formatCost(savings)}/turn`
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
 * @internal — exported for test access only. Use `buildSwitchOptions` for public API.
 */
export function calculateSwitchCost(
  targetModel: ModelOption,
  currentModel: ModelOption,
  conversationMessages: ChatMessage[],
): SwitchCostEstimate {
  const historyText = conversationMessages.map((m) => m.content).join("")
  const historyTokens = estimateTokenCount(historyText)

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

  const exceedsTargetContext = historyTokens > targetModel.effectiveContextWindow

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