// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { isLocalProvider } from "@/lib/types"

describe("isLocalProvider — E1 R5 / CR-024", () => {
  it("treats ollama and quenchforge as local", () => {
    expect(isLocalProvider("ollama")).toBe(true)
    expect(isLocalProvider("quenchforge")).toBe(true)
    expect(isLocalProvider("OLLAMA")).toBe(true)
  })

  it("treats cloud providers as non-local", () => {
    expect(isLocalProvider("openrouter")).toBe(false)
    expect(isLocalProvider("bifrost")).toBe(false)
    expect(isLocalProvider(undefined)).toBe(false)
    expect(isLocalProvider("")).toBe(false)
  })
})
