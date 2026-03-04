// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { VerificationStatusBar } from "@/components/audit/verification-status-bar"
import type { HallucinationReport, HallucinationClaim, StreamingClaim } from "@/lib/types"

const makeClaim = (overrides: Partial<HallucinationClaim> = {}): HallucinationClaim => ({
  claim: "Test claim",
  status: "verified",
  similarity: 0.9,
  source_artifact_id: "art-1",
  source_filename: "doc.md",
  source_domain: "general",
  ...overrides,
})

const makeReport = (overrides: Partial<HallucinationReport> = {}): HallucinationReport => ({
  conversation_id: "conv-1",
  timestamp: "2026-03-03T12:00:00Z",
  skipped: false,
  threshold: 0.75,
  claims: [
    makeClaim({ claim: "Verified claim", status: "verified", similarity: 0.95 }),
    makeClaim({ claim: "Unverified claim", status: "unverified", similarity: 0.3, verification_method: "kb" }),
  ],
  summary: { total: 2, verified: 1, unverified: 1, uncertain: 0 },
  ...overrides,
})

describe("VerificationStatusBar", () => {
  it("renders nothing when featureEnabled is false", () => {
    const { container } = render(
      <VerificationStatusBar report={null} loading={false} featureEnabled={false} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it("shows 'Verification ready' when enabled with no report", () => {
    render(
      <VerificationStatusBar report={null} loading={false} featureEnabled={true} />,
    )
    expect(screen.getByText("Verification ready")).toBeInTheDocument()
  })

  it("shows 'Analyzing response...' during non-streaming loading", () => {
    render(
      <VerificationStatusBar report={null} loading={true} featureEnabled={true} />,
    )
    expect(screen.getByText("Analyzing response...")).toBeInTheDocument()
  })

  it("shows 'Extracting claims...' during extraction phase", () => {
    render(
      <VerificationStatusBar
        report={null}
        loading={false}
        featureEnabled={true}
        streamPhase="extracting"
      />,
    )
    expect(screen.getByText("Extracting claims...")).toBeInTheDocument()
  })

  it("shows verifying progress with claim counts", () => {
    const streamingClaims: StreamingClaim[] = [
      { claim: "Claim one", index: 0, status: "verified" },
      { claim: "Claim two", index: 1, status: "pending" },
      { claim: "Claim three", index: 2, status: "pending" },
    ]
    render(
      <VerificationStatusBar
        report={null}
        loading={false}
        featureEnabled={true}
        streamPhase="verifying"
        verifiedCount={1}
        totalClaims={3}
        streamingClaims={streamingClaims}
      />,
    )
    expect(screen.getByText(/Verifying 1\/3 claims/)).toBeInTheDocument()
  })

  it("shows fallback verifying state without streaming claims", () => {
    render(
      <VerificationStatusBar
        report={null}
        loading={false}
        featureEnabled={true}
        streamPhase="verifying"
        verifiedCount={2}
        totalClaims={5}
      />,
    )
    expect(screen.getByText(/Verifying 2\/5/)).toBeInTheDocument()
  })

  it("shows 'No claims to verify' when report is skipped", () => {
    const report = makeReport({
      skipped: true,
      claims: [],
      summary: { total: 0, verified: 0, unverified: 0, uncertain: 0 },
    })
    render(
      <VerificationStatusBar report={report} loading={false} featureEnabled={true} />,
    )
    expect(screen.getByText("No claims to verify")).toBeInTheDocument()
  })

  it("renders completed report with claim summary counts", () => {
    render(
      <VerificationStatusBar report={makeReport()} loading={false} featureEnabled={true} />,
    )
    expect(screen.getByText("2 claims assessed")).toBeInTheDocument()
    expect(screen.getByText("1 verified")).toBeInTheDocument()
    expect(screen.getByText("1 unverified")).toBeInTheDocument()
  })

  it("shows accuracy percentage and coherence label", () => {
    render(
      <VerificationStatusBar report={makeReport()} loading={false} featureEnabled={true} />,
    )
    // With 1 verified, 0 refuted (the unverified is KB-only so not refuted)
    // denominator = verified + refuted = 1 + 0 = 1, accuracy = 100%
    expect(screen.getByText("100%")).toBeInTheDocument()
    expect(screen.getByText("High")).toBeInTheDocument()
  })

  it("expands to show claim details when summary row is clicked", async () => {
    const user = userEvent.setup()
    render(
      <VerificationStatusBar report={makeReport()} loading={false} featureEnabled={true} />,
    )
    // Claims not visible initially
    expect(screen.queryByText("Verified claim")).not.toBeInTheDocument()

    await user.click(screen.getByLabelText("Toggle verified claims"))
    expect(screen.getByText("Verified claim")).toBeInTheDocument()
    expect(screen.getByText("Unverified claim")).toBeInTheDocument()
  })

  it("shows session metrics when sessionClaimsChecked is positive", () => {
    render(
      <VerificationStatusBar
        report={null}
        loading={false}
        featureEnabled={true}
        sessionClaimsChecked={15}
        sessionEstCost={0.0042}
      />,
    )
    expect(screen.getByText(/Session: 15 facts/)).toBeInTheDocument()
    expect(screen.getByText(/\$0\.0042/)).toBeInTheDocument()
  })

  it("shows refuted count for cross-model unverified claims", () => {
    const report = makeReport({
      claims: [
        makeClaim({ claim: "Good claim", status: "verified", similarity: 0.95 }),
        makeClaim({ claim: "Bad claim", status: "unverified", similarity: 0.2, verification_method: "cross_model" }),
      ],
      summary: { total: 2, verified: 1, unverified: 1, uncertain: 0 },
    })
    render(
      <VerificationStatusBar report={report} loading={false} featureEnabled={true} />,
    )
    expect(screen.getByText("1 refuted")).toBeInTheDocument()
  })
})
