// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { MODELS, DOMAINS, PROVIDER_COLORS, findModel } from "@/lib/types"
import type { ChatMessage, SourceRef, Conversation, ModelOption } from "@/lib/types"

describe("MODELS constant", () => {
  it("contains at least one model", () => {
    expect(MODELS.length).toBeGreaterThan(0)
  })

  it("each model has required fields", () => {
    for (const m of MODELS) {
      expect(m.id).toBeTruthy()
      expect(m.label).toBeTruthy()
      expect(m.provider).toBeTruthy()
      expect(m.contextWindow).toBeGreaterThan(0)
      expect(m.inputCostPer1M).toBeGreaterThanOrEqual(0)
      expect(m.outputCostPer1M).toBeGreaterThanOrEqual(0)
    }
  })

  it("all model IDs start with openrouter/", () => {
    for (const m of MODELS) {
      expect(m.id).toMatch(/^openrouter\//)
    }
  })

  it("has unique model IDs", () => {
    const ids = MODELS.map((m) => m.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe("DOMAINS constant", () => {
  it("includes core domains", () => {
    expect(DOMAINS).toContain("coding")
    expect(DOMAINS).toContain("finance")
    expect(DOMAINS).toContain("projects")
    expect(DOMAINS).toContain("personal")
    expect(DOMAINS).toContain("general")
  })
})

describe("SourceRef type compatibility", () => {
  it("can be serialized and deserialized via JSON", () => {
    const ref: SourceRef = {
      artifact_id: "abc-123",
      filename: "test.py",
      domain: "coding",
      sub_category: "python",
      relevance: 0.85,
      chunk_index: 2,
      tags: ["fastapi", "api"],
    }

    const serialized = JSON.stringify(ref)
    const deserialized: SourceRef = JSON.parse(serialized)

    expect(deserialized.artifact_id).toBe("abc-123")
    expect(deserialized.filename).toBe("test.py")
    expect(deserialized.domain).toBe("coding")
    expect(deserialized.sub_category).toBe("python")
    expect(deserialized.relevance).toBe(0.85)
    expect(deserialized.chunk_index).toBe(2)
    expect(deserialized.tags).toEqual(["fastapi", "api"])
  })

  it("optional fields can be omitted", () => {
    const ref: SourceRef = {
      artifact_id: "abc-123",
      filename: "test.py",
      domain: "coding",
      relevance: 0.5,
      chunk_index: 0,
    }

    const serialized = JSON.stringify(ref)
    const deserialized: SourceRef = JSON.parse(serialized)

    expect(deserialized.sub_category).toBeUndefined()
    expect(deserialized.tags).toBeUndefined()
  })
})

describe("ChatMessage with sourcesUsed", () => {
  it("serializes assistant message with sources for localStorage persistence", () => {
    const msg: ChatMessage = {
      id: "msg-1",
      role: "assistant",
      content: "Here is the answer based on your knowledge base.",
      model: "openrouter/anthropic/claude-sonnet-4",
      timestamp: 1708900000000,
      sourcesUsed: [
        {
          artifact_id: "art-1",
          filename: "notes.md",
          domain: "personal",
          relevance: 0.92,
          chunk_index: 0,
        },
        {
          artifact_id: "art-2",
          filename: "budget.xlsx",
          domain: "finance",
          sub_category: "taxes",
          relevance: 0.78,
          chunk_index: 1,
          tags: ["2025", "deductions"],
        },
      ],
    }

    const json = JSON.stringify(msg)
    const restored: ChatMessage = JSON.parse(json)

    expect(restored.sourcesUsed).toHaveLength(2)
    expect(restored.sourcesUsed![0].filename).toBe("notes.md")
    expect(restored.sourcesUsed![1].tags).toEqual(["2025", "deductions"])
  })

  it("old messages without sourcesUsed remain valid", () => {
    const oldMsg: ChatMessage = {
      id: "msg-old",
      role: "assistant",
      content: "Response without sources",
      model: "openrouter/openai/gpt-4o",
      timestamp: 1708800000000,
    }

    expect(oldMsg.sourcesUsed).toBeUndefined()
  })
})

describe("Conversation type", () => {
  it("round-trips through JSON", () => {
    const convo: Conversation = {
      id: "conv-1",
      title: "Test conversation",
      messages: [
        { id: "m1", role: "user", content: "Hello", timestamp: Date.now() },
        {
          id: "m2",
          role: "assistant",
          content: "Hi there!",
          model: "openrouter/anthropic/claude-sonnet-4",
          timestamp: Date.now(),
          sourcesUsed: [
            { artifact_id: "a1", filename: "f.txt", domain: "general", relevance: 0.9, chunk_index: 0 },
          ],
        },
      ],
      model: "openrouter/anthropic/claude-sonnet-4",
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }

    const restored: Conversation = JSON.parse(JSON.stringify(convo))
    expect(restored.messages).toHaveLength(2)
    expect(restored.messages[1].sourcesUsed).toHaveLength(1)
  })
})

describe("PROVIDER_COLORS", () => {
  it("has a color for every provider in MODELS", () => {
    const providers = new Set(MODELS.map((m) => m.provider))
    for (const provider of providers) {
      expect(PROVIDER_COLORS[provider]).toBeTruthy()
    }
  })

  it("each color string contains bg- and text- classes", () => {
    for (const color of Object.values(PROVIDER_COLORS)) {
      expect(color).toMatch(/bg-/)
      expect(color).toMatch(/text-/)
    }
  })
})

describe("findModel", () => {
  it("finds a model by its ID", () => {
    const model = findModel(MODELS[0].id)
    expect(model).toBeDefined()
    expect(model?.label).toBe(MODELS[0].label)
  })

  it("returns undefined for unknown ID", () => {
    expect(findModel("unknown/model")).toBeUndefined()
  })
})

describe("ModelOption cost math", () => {
  it("can calculate cost for a given token count", () => {
    const model: ModelOption = MODELS[0]
    const inputTokens = 1_000_000
    const outputTokens = 500_000
    const cost = (inputTokens / 1_000_000) * model.inputCostPer1M + (outputTokens / 1_000_000) * model.outputCostPer1M
    expect(cost).toBeGreaterThan(0)
    expect(typeof cost).toBe("number")
  })
})
