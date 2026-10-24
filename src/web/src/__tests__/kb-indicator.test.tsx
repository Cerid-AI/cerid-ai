// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { SourceAttribution } from "@/components/chat/source-attribution"
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

describe("SourceAttribution badge (the message context indicator)", () => {
  it("renders nothing when no sources", () => {
    const { container } = render(<SourceAttribution sources={[]} variant="badge" />)
    expect(container.firstChild).toBeNull()
  })

  it("renders indicator when sources present", () => {
    render(<SourceAttribution sources={[makeSource()]} variant="badge" />)
    expect(screen.getByText(/Context sent to LLM/i)).toBeTruthy()
  })

  it("shows source count", () => {
    render(<SourceAttribution sources={[
      makeSource({ artifact_id: "a1", filename: "a.pdf", relevance: 0.9 }),
      makeSource({ artifact_id: "a2", filename: "b.pdf", relevance: 0.8 }),
    ]} variant="badge" />)
    expect(screen.getByText(/2 sources/i)).toBeTruthy()
  })

  it("shows singular for one source", () => {
    render(<SourceAttribution sources={[makeSource()]} variant="badge" />)
    expect(screen.getByText(/1 source\b/i)).toBeTruthy()
  })
})
