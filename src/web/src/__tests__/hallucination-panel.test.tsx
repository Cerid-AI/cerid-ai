// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { HallucinationPanel } from "@/components/audit/hallucination-panel"
import type { HallucinationReport, HallucinationClaim, StreamingClaim } from "@/lib/types"

const makeClaim = (overrides: Partial<HallucinationClaim> = {}): HallucinationClaim => ({
  claim: "The sky is blue",
  status: "verified",
  similarity: 0.95,
  source_artifact_id: "art-1",
  source_filename: "science.md",
  source_domain: "general",
  ...overrides,
})

const makeReport = (overrides: Partial<HallucinationReport> = {}): HallucinationReport => ({
  conversation_id: "conv-1",
  timestamp: "2026-03-03T12:00:00Z",
  skipped: false,
  threshold: 0.75,
  claims: [
    makeClaim({ claim: "Claim A is verified", status: "verified", similarity: 0.95 }),
    makeClaim({ claim: "Claim B is unverified", status: "unverified", similarity: 0.3 }),
    makeClaim({ claim: "Claim C is uncertain", status: "uncertain", similarity: 0.6 }),
  ],
  summary: { total: 3, verified: 1, unverified: 1, uncertain: 1 },
  ...overrides,
})

describe("HallucinationPanel", () => {
  it("shows feature disabled message when not enabled", () => {
    render(
      <HallucinationPanel report={null} loading={false} featureEnabled={false} />,
    )
    // Component shows "Response verification is off — enable in Settings or toolbar"
    expect(screen.getByText(/verification is off/i)).toBeInTheDocument()
  })

  it("shows loading state", () => {
    render(
      <HallucinationPanel report={null} loading={true} featureEnabled={true} />,
    )
    // Loading shows "Analyzing response..."
    expect(screen.getByText(/analyzing/i)).toBeInTheDocument()
  })

  it("renders claim list from report", () => {
    render(
      <HallucinationPanel report={makeReport()} loading={false} featureEnabled={true} />,
    )
    expect(screen.getByText("Claim A is verified")).toBeInTheDocument()
    expect(screen.getByText("Claim B is unverified")).toBeInTheDocument()
    expect(screen.getByText("Claim C is uncertain")).toBeInTheDocument()
  })

  it("shows summary counts", () => {
    render(
      <HallucinationPanel report={makeReport()} loading={false} featureEnabled={true} />,
    )
    // Summary renders counts like "1 verified", "1 unverified", "1 unassessed"
    expect(screen.getByText(/1 verified/)).toBeInTheDocument()
  })

  it("shows no-claims message when report is skipped", () => {
    const report = makeReport({
      skipped: true,
      reason: "Response too short",
      claims: [],
      summary: { total: 0, verified: 0, unverified: 0, uncertain: 0 },
    })
    render(
      <HallucinationPanel report={report} loading={false} featureEnabled={true} />,
    )
    // Skipped reports show "No factual claims to verify"
    expect(screen.getByText(/no factual claims/i)).toBeInTheDocument()
  })

  it("shows verification method badge for cross-model", () => {
    const report = makeReport({
      claims: [
        makeClaim({ verification_method: "cross_model", verification_model: "gpt-4o-mini" }),
      ],
      summary: { total: 1, verified: 1, unverified: 0, uncertain: 0 },
    })
    render(
      <HallucinationPanel report={report} loading={false} featureEnabled={true} />,
    )
    // VerificationMethodBadge renders "cross-model"
    expect(screen.getByText(/cross-model/i)).toBeInTheDocument()
  })

  it("shows web search badge for web-verified claims", () => {
    const report = makeReport({
      claims: [
        makeClaim({ verification_method: "web_search", source_urls: ["https://example.com"] }),
      ],
      summary: { total: 1, verified: 1, unverified: 0, uncertain: 0 },
    })
    render(
      <HallucinationPanel report={report} loading={false} featureEnabled={true} />,
    )
    // VerificationMethodBadge renders "web search"
    expect(screen.getByText(/web search/i)).toBeInTheDocument()
  })

  it("renders streaming claims when provided", () => {
    const streamingClaims: StreamingClaim[] = [
      { claim: "Streaming claim 1", index: 0, status: "verified", confidence: 0.9 },
      { claim: "Streaming claim 2", index: 1, status: "pending" },
    ]
    render(
      <HallucinationPanel
        report={null}
        loading={false}
        featureEnabled={true}
        streamingClaims={streamingClaims}
      />,
    )
    expect(screen.getByText("Streaming claim 1")).toBeInTheDocument()
    expect(screen.getByText("Streaming claim 2")).toBeInTheDocument()
  })

  it("shows feedback buttons for claims", () => {
    render(
      <HallucinationPanel
        report={makeReport()}
        loading={false}
        featureEnabled={true}
        conversationId="conv-1"
      />,
    )
    // Each claim has "Mark as correct" and "Mark as incorrect" buttons
    const correctBtns = screen.getAllByLabelText("Mark as correct")
    const incorrectBtns = screen.getAllByLabelText("Mark as incorrect")
    expect(correctBtns.length).toBe(3)
    expect(incorrectBtns.length).toBe(3)
  })
})
