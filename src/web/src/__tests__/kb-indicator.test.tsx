// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { KBContextIndicator } from "@/components/chat/kb-context-indicator"
import type { SourceRef } from "@/lib/types"

function makeSource(overrides: Partial<SourceRef> = {}): SourceRef {
  return {
    artifact_id: "art-1",
    filename: "doc.pdf",
    domain: "general",
    relevance: 0.9,
    chunk_index: 0,
    ...overrides,
  }
}

describe("KBContextIndicator", () => {
  it("renders nothing when no sources", () => {
    const { container } = render(<KBContextIndicator sources={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it("renders nothing when sources undefined", () => {
    const { container } = render(<KBContextIndicator />)
    expect(container.firstChild).toBeNull()
  })

  it("renders indicator when sources present", () => {
    render(<KBContextIndicator sources={[makeSource()]} />)
    expect(screen.getByText(/Context sent to LLM/i)).toBeTruthy()
  })

  it("shows source count", () => {
    render(<KBContextIndicator sources={[
      makeSource({ artifact_id: "a1", filename: "a.pdf", relevance: 0.9 }),
      makeSource({ artifact_id: "a2", filename: "b.pdf", relevance: 0.8 }),
    ]} />)
    expect(screen.getByText(/2 sources/i)).toBeTruthy()
  })

  it("shows singular for one source", () => {
    render(<KBContextIndicator sources={[makeSource()]} />)
    expect(screen.getByText(/1 source\b/i)).toBeTruthy()
  })
})
