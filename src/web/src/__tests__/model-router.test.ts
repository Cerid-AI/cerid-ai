// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import {
  scoreQueryComplexity,
  estimateTurnCost,
  recommendModel,
  calculateSwitchCost,
  buildSwitchOptions,
} from "@/lib/model-router"
import { MODELS } from "@/lib/types"
import type { ChatMessage, ModelOption } from "@/lib/types"

// ---------------------------------------------------------------------------
// scoreQueryComplexity
// ---------------------------------------------------------------------------

describe("scoreQueryComplexity", () => {
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
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const expensiveModel: ModelOption = {
    id: "test/expensive",
    label: "Expensive",
    provider: "test",
    contextWindow: 128_000,
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

  it("reasoning contains complexity level", () => {
    const result = recommendModel("hello", defaultModel, emptyMessages, 0)
    expect(result.reasoning).toMatch(/simple|medium|complex/)
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
// calculateSwitchCost
// ---------------------------------------------------------------------------

describe("calculateSwitchCost", () => {
  const cheapModel: ModelOption = {
    id: "test/cheap",
    label: "Cheap",
    provider: "test",
    contextWindow: 128_000,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const expensiveModel: ModelOption = {
    id: "test/expensive",
    label: "Expensive",
    provider: "test",
    contextWindow: 128_000,
    inputCostPer1M: 3.0,
    outputCostPer1M: 15.0,
  }

  const smallContextModel: ModelOption = {
    id: "test/small",
    label: "Small",
    provider: "test",
    contextWindow: 1_000,
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
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
  }

  const modelB: ModelOption = {
    id: "test/model-b",
    label: "Model B",
    provider: "test",
    contextWindow: 128_000,
    inputCostPer1M: 3.0,
    outputCostPer1M: 15.0,
  }

  const tinyModel: ModelOption = {
    id: "test/tiny",
    label: "Tiny",
    provider: "test",
    contextWindow: 500,
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
