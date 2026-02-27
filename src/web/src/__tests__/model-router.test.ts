// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { scoreQueryComplexity, estimateTurnCost, recommendModel } from "@/lib/model-router"
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
