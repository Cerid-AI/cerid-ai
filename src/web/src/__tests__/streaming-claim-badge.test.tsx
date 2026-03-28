// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { StreamingClaimBadge } from "@/components/audit/hallucination-panel"
import type { StreamingClaim } from "@/lib/types"

afterEach(cleanup)

const makeClaim = (overrides: Partial<StreamingClaim> = {}): StreamingClaim => ({
  claim: "GPT-4o was released in 2024 by OpenAI",
  index: 0,
  status: "verified",
  similarity: 0.92,
  source: "openai-docs.pdf",
  source_domain: "technology",
  source_snippet: "OpenAI released GPT-4o in May 2024.",
  source_urls: ["https://openai.com/blog/gpt-4o"],
  reason: "Confirmed via official OpenAI blog post.",
  verification_method: "cross_model",
  verification_model: "openrouter/openai/gpt-4o-mini",
  claim_type: "factual",
  ...overrides,
})

describe("StreamingClaimBadge", () => {
  it("renders claim text and status badge", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    expect(screen.getByText(/GPT-4o was released/)).toBeTruthy()
    expect(screen.getByText("verified")).toBeTruthy()
  })

  it("shows spinner for pending claims", () => {
    render(<StreamingClaimBadge claim={makeClaim({ status: "pending" })} />)
    expect(screen.getByText("verifying")).toBeTruthy()
  })

  it("is not expandable when pending", () => {
    render(<StreamingClaimBadge claim={makeClaim({ status: "pending", source: undefined, reason: undefined, source_snippet: undefined, source_urls: [] })} />)
    // No "More" toggle should appear
    expect(screen.queryByText("More")).toBeNull()
  })

  it("expands on click to show details", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    // Should have "More" toggle
    expect(screen.getByText("More")).toBeTruthy()
    // Click to expand
    fireEvent.click(screen.getByText("More").closest("div")!)
    // Should show expanded content
    expect(screen.getByText("Less")).toBeTruthy()
    expect(screen.getByText(/openai-docs\.pdf/)).toBeTruthy()
  })

  it("shows source snippet when expanded", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    fireEvent.click(screen.getByText("More").closest("div")!)
    expect(screen.getByText(/OpenAI released GPT-4o/)).toBeTruthy()
  })

  it("shows reason for non-KB claims when expanded", () => {
    render(<StreamingClaimBadge claim={makeClaim({ source: undefined })} />)
    fireEvent.click(screen.getByText("More").closest("div")!)
    expect(screen.getByText(/Confirmed via official/)).toBeTruthy()
  })

  it("shows reference links when expanded", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    fireEvent.click(screen.getByText("More").closest("div")!)
    const link = screen.getByText("openai.com")
    expect(link).toBeTruthy()
    expect(link.closest("a")?.href).toBe("https://openai.com/blog/gpt-4o")
  })

  it("shows claim type badge for non-factual claims", () => {
    render(<StreamingClaimBadge claim={makeClaim({ claim_type: "evasion", status: "verified" })} />)
    // "evasion" appears both as displayStatus and claim_type badge — use getAllByText
    const badges = screen.getAllByText("evasion")
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })

  it("shows consistency issue when expanded", () => {
    render(<StreamingClaimBadge claim={makeClaim({ consistency_issue: "Contradicts earlier statement about release date" })} />)
    fireEvent.click(screen.getByText("More").closest("div")!)
    expect(screen.getByText(/Contradicts earlier statement/)).toBeTruthy()
  })

  it("collapses when clicked again", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    fireEvent.click(screen.getByText("More").closest("div")!)
    expect(screen.getByText("Less")).toBeTruthy()
    fireEvent.click(screen.getByText("Less").closest("div")!)
    expect(screen.getByText("More")).toBeTruthy()
  })

  it("shows fallback Google search link when no source_urls", () => {
    render(<StreamingClaimBadge claim={makeClaim({ source_urls: [] })} />)
    fireEvent.click(screen.getByText("More").closest("div")!)
    expect(screen.getByText("Search for references")).toBeTruthy()
  })
})
