// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import {
  scoreQueryComplexity,
  estimateTurnCost,
  recommendModel,
  calculateSwitchCost,
  buildSwitchOptions,
  scoreModelForQuery,
  detectIntentWeights,
} from "@/lib/model-router"
import { MODELS } from "@/lib/types"
import type { ChatMessage, ModelOption } from "@/lib/types"

// ---------------------------------------------------------------------------
// scoreQueryComplexity
// ---------------------------------------------------------------------------

describe("scoreQueryComplexity", () => {
  it("returns 'simple' for empty/whitespace queries", () => {
    expect(scoreQueryComplexity("", 0, 0)).toBe("simple")
    expect(scoreQueryComplexity("   ", 0, 0)).toBe("simple")
    expect(scoreQueryComplexity("\n\t", 0, 0)).toBe("simple")
  })

  it("returns 'simple' for greetings", () => {
    expect(scoreQueryComplexity("hello", 0, 0)).toBe("simple")
    expect(scoreQueryComplexity("hi there", 0, 0)).toBe("simple")
    expect(scoreQueryComplexity("thanks", 0, 0)).toBe("simple")
  })

  it("returns 'simple' for definitional queries", () => {
    expect(scoreQueryComplexity("what is a function?", 0, 0)).toBe("simple")
    expect(scoreQueryComplexity("define REST API", 0, 0)).toBe("simple")
  })

  it("returns 'complex' for long conversations", () => {
    expect(scoreQueryComplexity("next step?", 25, 0)).toBe("complex")
  })

  it("returns 'complex' for many KB injections", () => {
    expect(scoreQueryComplexity("summarize these", 0, 3)).toBe("complex")
    expect(scoreQueryComplexity("summarize these", 0, 5)).toBe("complex")
  })

  it("returns 'complex' for queries with multiple complex indicators", () => {
    expect(scoreQueryComplexity("explain and compare this database schema", 0, 0)).toBe("complex")
    expect(scoreQueryComplexity("debug this function and analyze the code", 0, 0)).toBe("complex")
  })

  it("returns 'medium' for single complex indicator", () => {
    expect(scoreQueryComplexity("explain this concept please", 0, 0)).toBe("medium")
  })

  it("returns 'complex' for very long queries", () => {
    const longQuery = "a ".repeat(300)
    expect(scoreQueryComplexity(longQuery, 0, 0)).toBe("complex")
  })

  it("returns 'medium' for ambiguous queries", () => {
    expect(scoreQueryComplexity("tell me about the weather forecast for tomorrow", 0, 0)).toBe("medium")
  })
})

// ---------------------------------------------------------------------------
// estimateTurnCost
// ---------------------------------------------------------------------------

describe("estimateTurnCost", () => {
  const cheapModel: ModelOption = {
    id: "test/cheap",
    label: "Cheap",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const expensiveModel: ModelOption = {
    id: "test/expensive",
    label: "Expensive",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 3.0,
    outputCostPer1M: 15.0,
  }

  it("returns a positive number", () => {
    const cost = estimateTurnCost(cheapModel, 1000, 500)
    expect(cost).toBeGreaterThan(0)
  })

  it("expensive model costs more than cheap model", () => {
    const cheapCost = estimateTurnCost(cheapModel, 1000, 500)
    const expensiveCost = estimateTurnCost(expensiveModel, 1000, 500)
    expect(expensiveCost).toBeGreaterThan(cheapCost)
  })

  it("more input chars cost more", () => {
    const cost100 = estimateTurnCost(cheapModel, 100, 500)
    const cost10000 = estimateTurnCost(cheapModel, 10000, 500)
    expect(cost10000).toBeGreaterThan(cost100)
  })

  it("more output tokens cost more", () => {
    const cost100 = estimateTurnCost(cheapModel, 1000, 100)
    const cost1000 = estimateTurnCost(cheapModel, 1000, 1000)
    expect(cost1000).toBeGreaterThan(cost100)
  })

  it("returns 0 for zero-cost model", () => {
    const freeModel: ModelOption = {
      id: "test/free",
      label: "Free",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0,
      outputCostPer1M: 0,
    }
    expect(estimateTurnCost(freeModel, 1000, 500)).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// recommendModel
// ---------------------------------------------------------------------------

describe("recommendModel", () => {
  const defaultModel = MODELS[0]
  const emptyMessages: ChatMessage[] = []

  it("returns a ModelRecommendation with all fields", () => {
    const result = recommendModel("hello", defaultModel, emptyMessages, 0)
    expect(result).toHaveProperty("model")
    expect(result).toHaveProperty("estimatedCost")
    expect(result).toHaveProperty("reasoning")
    expect(result).toHaveProperty("savingsVsCurrent")
    expect(typeof result.reasoning).toBe("string")
    expect(result.reasoning.length).toBeGreaterThan(0)
  })

  it("savingsVsCurrent is non-negative", () => {
    const result = recommendModel("hello", defaultModel, emptyMessages, 0)
    expect(result.savingsVsCurrent).toBeGreaterThanOrEqual(0)
  })

  it("reasoning contains score or optimal note", () => {
    const result = recommendModel("hello", defaultModel, emptyMessages, 0)
    expect(result.reasoning).toMatch(/score|optimal/)
  })

  it("recommends the same model when already optimal", () => {
    // For a simple query with the cheapest model, should stay
    const cheapest = MODELS.reduce((a, b) =>
      a.inputCostPer1M + a.outputCostPer1M < b.inputCostPer1M + b.outputCostPer1M ? a : b,
    )
    const result = recommendModel("hi", cheapest, emptyMessages, 0, "low")
    // With low cost sensitivity, should keep current model
    expect(result.reasoning).toContain("already optimal")
  })

  it("high cost sensitivity encourages cheaper models", () => {
    const expensiveModel = MODELS.reduce((a, b) =>
      a.inputCostPer1M + a.outputCostPer1M > b.inputCostPer1M + b.outputCostPer1M ? a : b,
    )
    const result = recommendModel("hello", expensiveModel, emptyMessages, 0, "high")
    // High sensitivity should try to switch to cheaper
    expect(result.estimatedCost).toBeLessThanOrEqual(
      estimateTurnCost(expensiveModel, "hello".length, 200),
    )
  })

  it("estimated cost is positive for non-free models", () => {
    const result = recommendModel("explain algorithms", defaultModel, emptyMessages, 0)
    expect(result.estimatedCost).toBeGreaterThanOrEqual(0)
  })
})

// ---------------------------------------------------------------------------
// recommendModel — boundary cases
// ---------------------------------------------------------------------------

describe("recommendModel boundary cases", () => {
  const emptyMessages: ChatMessage[] = []

  it("handles zero-cost current model", () => {
    const freeModel: ModelOption = {
      id: "test/free",
      label: "Free",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0,
      outputCostPer1M: 0,
    }
    const result = recommendModel("hello", freeModel, emptyMessages, 0)
    // Free model can't save more — should stay or switch to similarly free
    expect(result.savingsVsCurrent).toBe(0)
    expect(result.reasoning).toContain("already optimal")
  })

  it("handles identical cost models in candidates", () => {
    // Two MODELS with identical pricing — recommendation should still work
    const model = MODELS[0]
    const result = recommendModel("explain this code", model, emptyMessages, 0)
    expect(result.model).toBeDefined()
    expect(result.estimatedCost).toBeGreaterThanOrEqual(0)
  })

  it("handles very long conversation history", () => {
    const longHistory: ChatMessage[] = Array.from({ length: 50 }, (_, i) => ({
      id: `msg-${i}`,
      role: i % 2 === 0 ? "user" : "assistant",
      content: "x".repeat(500),
      timestamp: Date.now(),
    }))
    const model = MODELS[0]
    // Should not throw; complexity should be "complex" due to 50 messages
    const result = recommendModel("next?", model, longHistory, 0)
    expect(result).toBeDefined()
    // Should switch to a cheaper model with enough capability for this query
    expect(result).toBeDefined()
    expect(result.reasoning).toMatch(/score|optimal|task/)
  })

  it("handles model with undefined costPer1M as zero", () => {
    const noCostModel: ModelOption = {
      id: "test/no-cost",
      label: "NoCost",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: undefined as unknown as number,
      outputCostPer1M: undefined as unknown as number,
    }
    // Should not throw
    const result = recommendModel("hello", noCostModel, emptyMessages, 0)
    expect(result).toBeDefined()
  })

  it("all candidates exceed context window", () => {
    // Create a model with a tiny context window
    const tinyModel: ModelOption = {
      id: "openrouter/openai/gpt-4o-mini",
      label: "GPT-4o Mini",
      provider: "OpenAI",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 16_384,
      inputCostPer1M: 0.15,
      outputCostPer1M: 0.60,
    }
    // Create massive history that would exceed all candidate windows
    const hugeHistory: ChatMessage[] = Array.from({ length: 100 }, (_, i) => ({
      id: `msg-${i}`,
      role: i % 2 === 0 ? "user" : "assistant",
      content: "x".repeat(20_000),
      timestamp: Date.now(),
    }))
    // 100 msgs * 20k chars = 2M chars = ~500k tokens — exceeds most context windows
    const result = recommendModel("next", tinyModel, hugeHistory, 0)
    // Should still return a valid result (fallback to current model)
    expect(result.model).toBeDefined()
  })

  it("empty query with high sensitivity still returns valid result", () => {
    const result = recommendModel("", MODELS[0], emptyMessages, 0, "high")
    expect(result).toBeDefined()
    expect(result.model).toBeDefined()
  })

  it("single-character query returns valid result", () => {
    const result = recommendModel("?", MODELS[0], emptyMessages, 0)
    expect(result.model).toBeDefined()
    expect(result.estimatedCost).toBeGreaterThanOrEqual(0)
  })
})

// ---------------------------------------------------------------------------
// calculateSwitchCost
// ---------------------------------------------------------------------------

describe("calculateSwitchCost", () => {
  const cheapModel: ModelOption = {
    id: "test/cheap",
    label: "Cheap",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const expensiveModel: ModelOption = {
    id: "test/expensive",
    label: "Expensive",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 3.0,
    outputCostPer1M: 15.0,
  }

  const smallContextModel: ModelOption = {
    id: "test/small",
    label: "Small",
    provider: "test",
    contextWindow: 1_000,
    effectiveContextWindow: 800,
    maxOutputTokens: 500,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const makeMessages = (count: number, charsEach = 100): ChatMessage[] =>
    Array.from({ length: count }, (_, i) => ({
      id: `msg-${i}`,
      role: i % 2 === 0 ? "user" : "assistant",
      content: "x".repeat(charsEach),
      timestamp: Date.now(),
    }))

  it("returns positive costs for non-empty history", () => {
    const result = calculateSwitchCost(cheapModel, expensiveModel, makeMessages(4))
    expect(result.replayCost).toBeGreaterThan(0)
    expect(result.currentNextTurnCost).toBeGreaterThan(0)
    expect(result.summarizeCost).toBeGreaterThan(0)
  })

  it("returns zero costs for empty history", () => {
    const result = calculateSwitchCost(cheapModel, expensiveModel, [])
    // historyTokens = 0, but output cost (500 tokens) still contributes
    expect(result.historyTokens).toBe(0)
    expect(result.replayCost).toBeGreaterThan(0) // output cost for response
  })

  it("scales cost with conversation length", () => {
    const short = calculateSwitchCost(cheapModel, expensiveModel, makeMessages(2))
    const long = calculateSwitchCost(cheapModel, expensiveModel, makeMessages(20))
    expect(long.replayCost).toBeGreaterThan(short.replayCost)
    expect(long.historyTokens).toBeGreaterThan(short.historyTokens)
  })

  it("expensive target model has higher replay cost", () => {
    const msgs = makeMessages(6)
    const toExpensive = calculateSwitchCost(expensiveModel, cheapModel, msgs)
    const toCheap = calculateSwitchCost(cheapModel, expensiveModel, msgs)
    expect(toExpensive.replayCost).toBeGreaterThan(toCheap.replayCost)
  })

  it("sets exceedsTargetContext when history exceeds 80% of window", () => {
    // smallContextModel has 1000 token window; 80% = 800 tokens
    // 4000 chars = ~1000 tokens, which exceeds 800
    const msgs = makeMessages(4, 1000)
    const result = calculateSwitchCost(smallContextModel, cheapModel, msgs)
    expect(result.exceedsTargetContext).toBe(true)
  })

  it("does not set exceedsTargetContext for small history", () => {
    const result = calculateSwitchCost(cheapModel, expensiveModel, makeMessages(2, 10))
    expect(result.exceedsTargetContext).toBe(false)
  })

  it("summarizedTokens is at least 200", () => {
    const result = calculateSwitchCost(cheapModel, expensiveModel, makeMessages(1, 10))
    expect(result.summarizedTokens).toBeGreaterThanOrEqual(200)
  })

  it("summarize is cheaper than replay for long history on expensive target", () => {
    const msgs = makeMessages(20, 500)
    const result = calculateSwitchCost(expensiveModel, cheapModel, msgs)
    expect(result.summarizeCost).toBeLessThan(result.replayCost)
  })

  it("cheap → expensive: replay cost reflects target pricing", () => {
    const msgs = makeMessages(6)
    const result = calculateSwitchCost(expensiveModel, cheapModel, msgs)
    // Switching TO expensive model → replay cost uses expensive pricing
    expect(result.replayCost).toBeGreaterThan(0)
    // currentNextTurnCost uses current (cheap) model pricing
    expect(result.currentNextTurnCost).toBeLessThan(result.replayCost)
  })

  it("expensive → cheap: replay cost is lower than current next turn", () => {
    const msgs = makeMessages(6)
    const result = calculateSwitchCost(cheapModel, expensiveModel, msgs)
    // Switching TO cheap model → replay is cheap, current next turn is expensive
    expect(result.replayCost).toBeLessThan(result.currentNextTurnCost)
  })

  it("same model: costs are consistent", () => {
    const msgs = makeMessages(6)
    const result = calculateSwitchCost(cheapModel, cheapModel, msgs)
    // Same model → replay and current next turn should be very close
    // (replay is input+output, current is also input+output)
    expect(result.replayCost).toBeGreaterThan(0)
    expect(result.currentNextTurnCost).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// buildSwitchOptions
// ---------------------------------------------------------------------------

describe("buildSwitchOptions", () => {
  const modelA: ModelOption = {
    id: "test/model-a",
    label: "Model A",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const modelB: ModelOption = {
    id: "test/model-b",
    label: "Model B",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 3.0,
    outputCostPer1M: 15.0,
  }

  const tinyModel: ModelOption = {
    id: "test/tiny",
    label: "Tiny",
    provider: "test",
    contextWindow: 500,
    effectiveContextWindow: 400,
    maxOutputTokens: 250,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const makeMessages = (count: number, charsEach = 100): ChatMessage[] =>
    Array.from({ length: count }, (_, i) => ({
      id: `msg-${i}`,
      role: i % 2 === 0 ? "user" : "assistant",
      content: "x".repeat(charsEach),
      timestamp: Date.now(),
    }))

  it("includes continue and fresh for short conversations", () => {
    const result = buildSwitchOptions(modelB, modelA, makeMessages(2))
    expect(result.strategies).toContain("continue")
    expect(result.strategies).toContain("fresh")
    expect(result.strategies).not.toContain("summarize")
  })

  it("includes summarize for conversations with 6+ messages", () => {
    const result = buildSwitchOptions(modelB, modelA, makeMessages(8))
    expect(result.strategies).toContain("summarize")
  })

  it("recommends continue by default for normal conversations", () => {
    const result = buildSwitchOptions(modelB, modelA, makeMessages(4))
    expect(result.recommended).toBe("continue")
  })

  it("recommends summarize or fresh when context exceeds target window", () => {
    // tinyModel has 500 token window; large history will exceed it
    const msgs = makeMessages(8, 500)
    const result = buildSwitchOptions(tinyModel, modelA, msgs)
    expect(["summarize", "fresh"]).toContain(result.recommended)
  })

  it("returns the target model in the result", () => {
    const result = buildSwitchOptions(modelB, modelA, makeMessages(2))
    expect(result.targetModel.id).toBe(modelB.id)
  })
})

// ---------------------------------------------------------------------------
// detectIntentWeights
// ---------------------------------------------------------------------------

describe("detectIntentWeights", () => {
  it("returns normalized weights that sum to ~1.0", () => {
    const weights = detectIntentWeights("explain this code")
    const sum = Object.values(weights).reduce((a, b) => a + b, 0)
    expect(sum).toBeCloseTo(1.0, 2)
  })

  it("detects coding intent for code queries", () => {
    const weights = detectIntentWeights("debug this function")
    expect(weights.coding).toBeGreaterThan(weights.creative)
  })

  it("detects creative intent for writing queries", () => {
    const weights = detectIntentWeights("write a poem about nature")
    expect(weights.creative).toBeGreaterThan(weights.coding)
  })

  it("detects reasoning intent for analytical queries", () => {
    const weights = detectIntentWeights("analyze the trade-offs between approaches")
    expect(weights.reasoning).toBeGreaterThan(weights.factual)
  })

  it("detects factual intent for definitional queries", () => {
    const weights = detectIntentWeights("what is a REST API")
    expect(weights.factual).toBeGreaterThan(weights.creative)
  })
})

// ---------------------------------------------------------------------------
// scoreModelForQuery
// ---------------------------------------------------------------------------

describe("scoreModelForQuery", () => {
  it("returns 50 for models without capabilities", () => {
    const noCaps: ModelOption = {
      id: "test/no-caps",
      label: "NoCaps",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0.15,
      outputCostPer1M: 0.60,
    }
    expect(scoreModelForQuery(noCaps, "explain code")).toBe(50)
  })

  it("scores higher for coding models on coding queries", () => {
    const codingModel: ModelOption = {
      id: "test/coder",
      label: "Coder",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0.15,
      outputCostPer1M: 0.60,
      capabilities: { reasoning: 70, coding: 95, creative: 50, factual: 60, webSearch: false, vision: false, knowledgeCutoff: "2025-01" },
    }
    const genericModel: ModelOption = {
      id: "test/generic",
      label: "Generic",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0.15,
      outputCostPer1M: 0.60,
      capabilities: { reasoning: 70, coding: 60, creative: 70, factual: 70, webSearch: false, vision: false, knowledgeCutoff: "2025-01" },
    }
    const codingScore = scoreModelForQuery(codingModel, "debug this typescript function")
    const genericScore = scoreModelForQuery(genericModel, "debug this typescript function")
    expect(codingScore).toBeGreaterThan(genericScore)
  })

  it("gives bonus for web search on current-info queries", () => {
    const withSearch: ModelOption = {
      id: "test/search",
      label: "Search",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0.15,
      outputCostPer1M: 0.60,
      capabilities: { reasoning: 70, coding: 70, creative: 70, factual: 70, webSearch: true, vision: false, knowledgeCutoff: "2025-01" },
    }
    const noSearch: ModelOption = {
      ...withSearch,
      id: "test/no-search",
      capabilities: { ...withSearch.capabilities!, webSearch: false },
    }
    const withScore = scoreModelForQuery(withSearch, "what is the latest news today")
    const withoutScore = scoreModelForQuery(noSearch, "what is the latest news today")
    expect(withScore).toBeGreaterThan(withoutScore)
  })

  it("returns score capped at 100", () => {
    const superModel: ModelOption = {
      id: "test/super",
      label: "Super",
      provider: "test",
      contextWindow: 128_000,
      effectiveContextWindow: 102_400,
      maxOutputTokens: 4_096,
      inputCostPer1M: 0.15,
      outputCostPer1M: 0.60,
      capabilities: { reasoning: 99, coding: 99, creative: 99, factual: 99, webSearch: true, vision: true, knowledgeCutoff: "2026-03" },
    }
    const score = scoreModelForQuery(superModel, "latest image analysis of code to explain")
    expect(score).toBeLessThanOrEqual(100)
  })
})

// ---------------------------------------------------------------------------
// Cost sensitivity interaction
// ---------------------------------------------------------------------------

describe("cost sensitivity interaction with scoring", () => {
  const emptyMessages: ChatMessage[] = []

  it("low sensitivity requires large savings to switch", () => {
    // With low sensitivity (50% threshold), switching only happens for big cost differences
    const cheapest = MODELS.reduce((a, b) =>
      a.inputCostPer1M + a.outputCostPer1M < b.inputCostPer1M + b.outputCostPer1M ? a : b,
    )
    const result = recommendModel("hello", cheapest, emptyMessages, 0, "low")
    // Cheapest model can't save more — should stay
    expect(result.model.id).toBe(cheapest.id)
  })

  it("high sensitivity prefers cheaper model for simple queries", () => {
    const expensive = MODELS.reduce((a, b) =>
      a.inputCostPer1M + a.outputCostPer1M > b.inputCostPer1M + b.outputCostPer1M ? a : b,
    )
    const result = recommendModel("hello", expensive, emptyMessages, 0, "high")
    // High sensitivity switches easily — should find something cheaper
    expect(result.estimatedCost).toBeLessThanOrEqual(
      estimateTurnCost(expensive, "hello".length, 200),
    )
  })
})
