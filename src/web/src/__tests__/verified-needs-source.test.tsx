// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * "Verified" is for a claim a source backs: a KB artifact or a web result with
 * a URL. A second model agreeing with no source reads "agreed", is drawn in a
 * neutral colour, and is left out of the verified count and the accuracy
 * figure. A second model disagreeing reads and counts as it did.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { VerificationStatusBar } from "@/components/audit/verification-status-bar"
import { HallucinationPanel } from "@/components/audit/hallucination-panel"
import { ClaimBadge } from "@/components/verification/claim-badge"
import { MessageBubble } from "@/components/chat/message-bubble"
import { deriveBand } from "@/components/verification/types"
import {
  countPositiveVerdicts,
  getClaimDisplayStatus,
  isSourceBacked,
  matchClaimsToText,
  reportPositiveCounts,
} from "@/lib/verification-utils"
import type { ClaimVerificationFE } from "@/components/verification/types"
import type { HallucinationClaim, HallucinationReport } from "@/lib/types"

const agreed = (claim: string, overrides: Partial<HallucinationClaim> = {}): HallucinationClaim => ({
  claim,
  status: "verified",
  similarity: 1,
  verification_method: "cross_model",
  source_urls: [],
  ...overrides,
})

const fromKb = (claim: string): HallucinationClaim => ({
  claim,
  status: "verified",
  similarity: 0.9,
  verification_method: "kb_nli",
  source_artifact_id: "art-1",
  source_filename: "notes.md",
})

const fromWeb = (claim: string): HallucinationClaim => ({
  claim,
  status: "verified",
  similarity: 0.8,
  verification_method: "web_search",
  source_urls: ["https://example.org/a"],
})

const contradicted = (claim: string): HallucinationClaim => ({
  claim,
  status: "unverified",
  similarity: 0.3,
  verification_method: "cross_model",
  source_urls: [],
})

const report = (
  claims: HallucinationClaim[],
  summary: HallucinationReport["summary"],
): HallucinationReport => ({
  conversation_id: "conv-1",
  timestamp: "2026-09-28T12:00:00Z",
  skipped: false,
  claims,
  summary,
})

const forBadge = (claim: HallucinationClaim): ClaimVerificationFE => ({
  claim: claim.claim,
  status: claim.status,
  confidence: claim.similarity,
  verification_method: claim.verification_method,
  source_artifact_id: claim.source_artifact_id,
  source_filename: claim.source_filename,
  source_urls: claim.source_urls,
})

const bar = (r: HallucinationReport) =>
  render(<VerificationStatusBar report={r} loading={false} featureEnabled={true} />)

describe("isSourceBacked", () => {
  it("is true for a KB verdict, a KB artifact and a web result with a URL", () => {
    expect(isSourceBacked(fromKb("a"))).toBe(true)
    expect(isSourceBacked(fromWeb("a"))).toBe(true)
    expect(isSourceBacked({ verification_method: "kb" })).toBe(true)
    expect(isSourceBacked({ verification_method: "kb_batch" })).toBe(true)
    expect(isSourceBacked({ verification_method: "cross_model", source_urls: ["https://example.org"] })).toBe(true)
  })

  it("is false for a second model's agreement and for a web search with no URL", () => {
    expect(isSourceBacked(agreed("a"))).toBe(false)
    expect(isSourceBacked({ verification_method: "cross_model_complex" })).toBe(false)
    expect(isSourceBacked({ verification_method: "web_search", source_urls: [] })).toBe(false)
    expect(isSourceBacked({ verification_method: "web_search", source_urls: [""] })).toBe(false)
    expect(isSourceBacked({})).toBe(false)
  })

  it("splits positive verdicts into verified and agreed", () => {
    expect(
      countPositiveVerdicts([agreed("a"), agreed("b"), fromKb("c"), fromWeb("d"), contradicted("e")]),
    ).toEqual({ verified: 2, agreed: 2 })
  })
})

describe("display status", () => {
  it("is agreed for a second model's agreement with no source", () => {
    expect(getClaimDisplayStatus("verified", "cross_model", undefined, undefined, { source_urls: [] })).toBe("agreed")
    expect(getClaimDisplayStatus("verified", "web_search", "ignorance", undefined, { source_urls: [] })).toBe("agreed")
  })

  it("is verified when a source backs the claim", () => {
    expect(getClaimDisplayStatus("verified", "kb_nli", undefined, undefined, { source_artifact_id: "art-1" })).toBe("verified")
    expect(getClaimDisplayStatus("verified", "web_search", "ignorance", undefined, { source_urls: ["https://example.org"] })).toBe("verified")
  })

  it("is still refuted when a second model disagrees with no source", () => {
    expect(getClaimDisplayStatus("unverified", "cross_model", undefined, undefined, { source_urls: [] })).toBe("refuted")
    expect(getClaimDisplayStatus("unverified", "web_search", undefined, undefined, { source_urls: [] })).toBe("refuted")
    expect(getClaimDisplayStatus("uncertain", "cross_model", undefined, undefined, { source_urls: [] })).toBe("uncertain")
  })

  it("marks the text of an agreed claim as agreed, not verified", () => {
    const spans = matchClaimsToText("Paris is the capital of France.", [agreed("Paris is the capital of France")])
    expect(spans[0].displayStatus).toBe("agreed")
  })
})

describe("ClaimBadge", () => {
  it("reads Agreed in a neutral colour for a second model's agreement", () => {
    const claim = forBadge(agreed("a"))
    expect(deriveBand(claim)).toBe("agreed")
    render(<ClaimBadge claim={claim} />)
    const button = screen.getByRole("button", { name: "A second model agrees with this claim; no source" })
    expect(button).toHaveAttribute("data-verification-band", "agreed")
    expect(button).toHaveTextContent("Agreed")
    expect(button).not.toHaveTextContent(/verified/i)
    expect(button.innerHTML).not.toMatch(/green/)
    expect(button.innerHTML).toMatch(/text-muted-foreground/)
  })

  it("reads Verified in green for a claim a source backs", () => {
    render(<ClaimBadge claim={forBadge(fromKb("a"))} />)
    const button = screen.getByRole("button", { name: "Claim verified by 1 source" })
    expect(button).toHaveAttribute("data-verification-band", "verified")
    expect(button).toHaveTextContent("Verified by 1 source")
    expect(button.innerHTML).toMatch(/green/)
  })

  it("reads Refuted in red when a second model disagrees, as before", () => {
    const claim = forBadge(contradicted("a"))
    expect(deriveBand(claim)).toBe("refuted")
    render(<ClaimBadge claim={claim} />)
    const button = screen.getByRole("button", { name: "Claim refuted by an independent check" })
    expect(button).toHaveTextContent("Refuted")
    expect(button.innerHTML).toMatch(/red/)
  })
})

describe("VerificationStatusBar", () => {
  it("all source-backed: every claim is verified and the accuracy is 100%", () => {
    bar(report([fromKb("a"), fromWeb("b")], { total: 2, verified: 2, unverified: 0, uncertain: 0 }))
    expect(screen.getByText("2 verified")).toBeInTheDocument()
    expect(screen.queryByText(/agreed by a second model/)).not.toBeInTheDocument()
    expect(screen.getByText("100%")).toBeInTheDocument()
  })

  it("mixed: agreed claims are named apart and are not in the accuracy figure", () => {
    bar(
      report([fromKb("a"), agreed("b"), agreed("c"), contradicted("d")], {
        total: 4, verified: 1, unverified: 1, uncertain: 0,
      }),
    )
    expect(screen.getByText("1 verified")).toBeInTheDocument()
    expect(screen.getByText("2 agreed by a second model")).toBeInTheDocument()
    expect(screen.getByText("1 refuted")).toBeInTheDocument()
    // 1 verified of 1 verified + 1 refuted. Counting the agreed pair it read 75%.
    expect(screen.getByText("50%")).toBeInTheDocument()
    expect(screen.queryByText("75%")).not.toBeInTheDocument()
  })

  it("none source-backed: no claim is verified and no percentage is shown", () => {
    bar(report([agreed("a"), agreed("b")], { total: 2, verified: 0, unverified: 0, uncertain: 0 }))
    expect(screen.getByText("0 verified")).toBeInTheDocument()
    expect(screen.getByText("2 agreed by a second model")).toBeInTheDocument()
    expect(screen.queryByText("100%")).not.toBeInTheDocument()
    expect(screen.getByText("—")).toBeInTheDocument()
    expect(screen.getByText("Not assessed")).toBeInTheDocument()
    expect(screen.getByText("2 claims assessed")).toBeInTheDocument()
  })

  it("a report stored before the rule does not show agreement as verified", () => {
    bar(report([agreed("a"), agreed("b")], { total: 2, verified: 2, unverified: 0, uncertain: 0 }))
    expect(screen.getByText("0 verified")).toBeInTheDocument()
    expect(screen.queryByText("2 verified")).not.toBeInTheDocument()
    expect(screen.queryByText("100%")).not.toBeInTheDocument()
  })

  it("the agreed count is not drawn in the verified green", () => {
    bar(report([agreed("a")], { total: 1, verified: 0, unverified: 0, uncertain: 0 }))
    const label = screen.getByText("1 agreed by a second model")
    expect(label.className).toMatch(/text-muted-foreground/)
    expect(label.className).not.toMatch(/green/)
    expect(screen.getByText("0 verified").className).not.toMatch(/green/)
  })

  it("a contradiction from a second model reads and counts as before", () => {
    bar(report([fromKb("a"), contradicted("b")], { total: 2, verified: 1, unverified: 1, uncertain: 0 }))
    expect(screen.getByText("1 verified")).toBeInTheDocument()
    const refuted = screen.getByText("1 refuted")
    expect(refuted.className).toMatch(/red/)
    expect(screen.getByText("50%")).toBeInTheDocument()
  })
})

describe("HallucinationPanel", () => {
  it("names agreed claims apart from verified ones", () => {
    render(
      <HallucinationPanel
        report={report([fromKb("Backed claim"), agreed("Agreed claim one"), agreed("Agreed claim two")], {
          total: 3, verified: 1, unverified: 0, uncertain: 0,
        })}
        loading={false}
        featureEnabled={true}
      />,
    )
    expect(screen.getByRole("button", { name: "1 verified" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "2 agreed by a second model" })).toBeInTheDocument()
    expect(screen.getAllByText("agreed")).toHaveLength(2)
    expect(screen.getAllByText("verified")).toHaveLength(1)
  })

  it("still names a contradicted claim refuted", () => {
    render(
      <HallucinationPanel
        report={report([contradicted("Wrong claim")], { total: 1, verified: 0, unverified: 1, uncertain: 0 })}
        loading={false}
        featureEnabled={true}
      />,
    )
    expect(screen.getByRole("button", { name: "1 refuted" })).toBeInTheDocument()
    expect(screen.getByText("refuted")).toBeInTheDocument()
  })
})

describe("message verification badge", () => {
  it("reads 0/2 verified with the agreed count, not in green", () => {
    render(
      <MessageBubble
        message={{ id: "m1", role: "assistant", content: "Hello", timestamp: 1 }}
        verificationStatus={{ state: "done", verified: 0, agreed: 2, unverified: 0, uncertain: 0, total: 2 }}
      />,
    )
    const badge = screen.getByText("0/2 verified · 2 agreed")
    expect(badge.className).not.toMatch(/green/)
    expect(badge.className).toMatch(/text-muted-foreground/)
  })

  it("reads as before when every claim is source-backed", () => {
    render(
      <MessageBubble
        message={{ id: "m1", role: "assistant", content: "Hello", timestamp: 1 }}
        verificationStatus={{ state: "done", verified: 2, agreed: 0, unverified: 0, uncertain: 0, total: 2 }}
      />,
    )
    expect(screen.getByText("2/2 verified").className).toMatch(/green/)
  })
})

describe("agreed count sent by the server", () => {
  const claims = [fromKb("Backed claim"), agreed("Agreed claim one"), agreed("Agreed claim two"), contradicted("Wrong claim")]
  const counts = { total: 4, verified: 1, unverified: 1, uncertain: 0 }
  const withServerCount = report(claims, { ...counts, agreed: 2 })
  const storedBefore = report(claims, counts)

  it("is used when the summary carries it", () => {
    expect(reportPositiveCounts({ agreed: 2 }, claims)).toEqual({ verified: 1, agreed: 2 })
    // The server's number, not a recount: a recount of these claims is 2.
    expect(reportPositiveCounts({ agreed: 3 }, claims).agreed).toBe(3)
  })

  it("is used when it is zero", () => {
    expect(reportPositiveCounts({ agreed: 0 }, [fromKb("a")])).toEqual({ verified: 1, agreed: 0 })
    expect(reportPositiveCounts({ agreed: 0 }, claims).agreed).toBe(0)
  })

  it("is counted from the claims for a report stored without it", () => {
    expect(reportPositiveCounts({}, claims)).toEqual({ verified: 1, agreed: 2 })
    expect(reportPositiveCounts(undefined, claims)).toEqual({ verified: 1, agreed: 2 })
    expect(reportPositiveCounts(null, claims)).toEqual({ verified: 1, agreed: 2 })
  })

  it.each([
    ["with the server's count", withServerCount],
    ["stored before the server sent it", storedBefore],
  ])("status bar shows the same numbers for a report %s", (_name, r) => {
    bar(r)
    expect(screen.getByText("1 verified")).toBeInTheDocument()
    expect(screen.getByText("2 agreed by a second model")).toBeInTheDocument()
    expect(screen.getByText("1 refuted")).toBeInTheDocument()
    expect(screen.getByText("50%")).toBeInTheDocument()
  })

  it("status bar shows the server's count", () => {
    bar(report(claims, { ...counts, agreed: 3 }))
    expect(screen.getByText("3 agreed by a second model")).toBeInTheDocument()
  })

  it.each([
    ["with the server's count", withServerCount],
    ["stored before the server sent it", storedBefore],
  ])("panel shows the same numbers for a report %s", (_name, r) => {
    render(<HallucinationPanel report={r} loading={false} featureEnabled={true} />)
    expect(screen.getByRole("button", { name: "1 verified" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "2 agreed by a second model" })).toBeInTheDocument()
  })

  it("panel shows the server's count", () => {
    render(
      <HallucinationPanel report={report(claims, { ...counts, agreed: 3 })} loading={false} featureEnabled={true} />,
    )
    expect(screen.getByRole("button", { name: "3 agreed by a second model" })).toBeInTheDocument()
  })
})
