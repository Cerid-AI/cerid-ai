// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
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
  it("renders claim text for a settled verified claim", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    expect(screen.getByText(/GPT-4o was released/)).toBeTruthy()
  })

  it("settled verified claim uses canonical ClaimBadge (shows band label)", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    // The new ClaimBadge renders "No source" / "Partial source" / "Verified by N sources"
    // status=verified + source_urls has 1 entry → "verified" band
    const badge = screen.getByRole("button")
    expect(badge).toBeTruthy()
    // deriveBand: status=verified + source_urls.length=1 → "verified"
    expect(badge.getAttribute("data-verification-band")).toBe("verified")
  })

  it("shows spinner for pending claims", () => {
    render(<StreamingClaimBadge claim={makeClaim({ status: "pending" })} />)
    expect(screen.getByText("verifying")).toBeTruthy()
  })

  it("pending claim shows claim text", () => {
    render(<StreamingClaimBadge claim={makeClaim({ status: "pending" })} />)
    expect(screen.getByText(/GPT-4o was released/)).toBeTruthy()
  })

  it("is not expandable when pending (no ClaimBadge rendered)", () => {
    render(<StreamingClaimBadge claim={makeClaim({ status: "pending" })} />)
    // No hover-card button in pending state
    expect(screen.queryByRole("button")).toBeNull()
  })

  it("settled claim renders ClaimBadge button", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    // ClaimBadge renders as a <button>
    expect(screen.getByRole("button")).toBeTruthy()
  })

  it("shows claim type badge for non-factual claims", () => {
    render(<StreamingClaimBadge claim={makeClaim({ claim_type: "evasion", status: "verified" })} />)
    expect(screen.getByText("evasion")).toBeTruthy()
  })

  it("shows source domain when present", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    expect(screen.getByText("technology")).toBeTruthy()
  })

  it("shows similarity match percentage", () => {
    render(<StreamingClaimBadge claim={makeClaim()} />)
    expect(screen.getByText(/92% match/)).toBeTruthy()
  })

  it("renders verified claim with KB artifact as 'verified' band", () => {
    render(
      <StreamingClaimBadge
        claim={makeClaim({
          status: "verified",
          source_artifact_id: "art-123",
          source_urls: ["https://example.com"],
        })}
      />,
    )
    const btn = screen.getByRole("button")
    expect(btn.getAttribute("data-verification-band")).toBe("verified")
  })
})
