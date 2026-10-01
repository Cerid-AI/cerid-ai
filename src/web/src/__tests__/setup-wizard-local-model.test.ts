// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { chooseLocalModel, summaryChatModel } from "@/components/setup/setup-wizard"
import type { SystemCheckResponse } from "@/lib/types"

const check = (over: Partial<SystemCheckResponse>) =>
  ({ ollama_models: ["qwen3.5-4b-instruct", "gemma-4-26b-a4b"], ...over }) as SystemCheckResponse

describe("chooseLocalModel", () => {
  it("keeps the model the instance already names", () => {
    expect(chooseLocalModel(check({ ollama_configured_model: "gemma-4-26b-a4b" }), null)).toBe("gemma-4-26b-a4b")
  })

  it("keeps the model chosen in this session when a later check still serves it", () => {
    expect(chooseLocalModel(check({}), "gemma-4-26b-a4b")).toBe("gemma-4-26b-a4b")
  })

  it("picks nothing for the user when nothing is named or chosen", () => {
    expect(chooseLocalModel(check({}), null)).toBeNull()
  })

  it("drops a chosen model the server no longer serves, and picks no other", () => {
    expect(chooseLocalModel(check({}), "llama3.2:3b")).toBeNull()
  })

  it("has no model when the server serves none", () => {
    expect(chooseLocalModel(check({ ollama_models: [] }), "gemma-4-26b-a4b")).toBeNull()
  })
})

describe("summaryChatModel", () => {
  it("is the chosen model under the Quenchforge backend, not a fixed one", () => {
    expect(summaryChatModel("quenchforge", "gemma-4-26b-a4b")).toBe("gemma-4-26b-a4b")
  })

  it("is nothing under the Quenchforge backend when no model was chosen", () => {
    expect(summaryChatModel("quenchforge", null)).toBeNull()
  })

  it("is nothing for a cloud backend or a reranker", () => {
    expect(summaryChatModel("cloud", "gemma-4-26b-a4b")).toBeNull()
    expect(summaryChatModel("ollama", "bge-reranker-v2-m3")).toBeNull()
  })
})
