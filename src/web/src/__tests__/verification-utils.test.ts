// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest"
import { getClaimDisplayStatus, matchClaimsToText } from "@/lib/verification-utils"

describe("getClaimDisplayStatus", () => {
  it("returns verified for verified status", () => {
    expect(getClaimDisplayStatus("verified")).toBe("verified")
  })

  it("returns refuted for unverified + cross_model", () => {
    expect(getClaimDisplayStatus("unverified", "cross_model")).toBe("refuted")
  })

  it("returns refuted for unverified + web_search", () => {
    expect(getClaimDisplayStatus("unverified", "web_search")).toBe("refuted")
  })

  it("returns unverified for unverified + kb", () => {
    expect(getClaimDisplayStatus("unverified", "kb")).toBe("unverified")
  })

  it("returns uncertain for uncertain status", () => {
    expect(getClaimDisplayStatus("uncertain")).toBe("uncertain")
  })

  it("returns pending for pending status", () => {
    expect(getClaimDisplayStatus("pending")).toBe("pending")
  })

  it("returns evasion for evasion claim_type regardless of status", () => {
    expect(getClaimDisplayStatus("verified", "web_search", "evasion")).toBe("evasion")
    expect(getClaimDisplayStatus("unverified", "web_search", "evasion")).toBe("evasion")
    expect(getClaimDisplayStatus("uncertain", undefined, "evasion")).toBe("evasion")
    expect(getClaimDisplayStatus("pending", undefined, "evasion")).toBe("evasion")
  })

  it("returns normal status for factual claim_type", () => {
    expect(getClaimDisplayStatus("verified", "kb", "factual")).toBe("verified")
    expect(getClaimDisplayStatus("unverified", "cross_model", "factual")).toBe("refuted")
  })

  it("returns normal status for ignorance claim_type", () => {
    expect(getClaimDisplayStatus("unverified", "web_search", "ignorance")).toBe("refuted")
  })

  it("returns citation for citation claim_type regardless of status", () => {
    expect(getClaimDisplayStatus("verified", "web_search", "citation")).toBe("citation")
    expect(getClaimDisplayStatus("unverified", "web_search", "citation")).toBe("citation")
    expect(getClaimDisplayStatus("uncertain", undefined, "citation")).toBe("citation")
    expect(getClaimDisplayStatus("pending", undefined, "citation")).toBe("citation")
  })
})

describe("matchClaimsToText", () => {
  it("returns empty for empty inputs", () => {
    expect(matchClaimsToText("", [])).toEqual([])
    expect(matchClaimsToText("hello", [])).toEqual([])
    expect(matchClaimsToText("", [{ claim: "foo", status: "verified" }])).toEqual([])
  })

  it("matches exact substring", () => {
    const text = "The capital of France is Paris. It is a beautiful city."
    const claims = [{ claim: "The capital of France is Paris", status: "verified" }]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(1)
    expect(spans[0].start).toBe(0)
    expect(spans[0].end).toBe(30)
    expect(spans[0].displayStatus).toBe("verified")
  })

  it("matches case-insensitively", () => {
    const text = "THE EARTH IS ROUND."
    const claims = [{ claim: "the earth is round", status: "verified" }]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(1)
  })

  it("falls back to 5-word prefix match", () => {
    const text = "Machine learning models require large datasets for effective training."
    const claims = [{ claim: "Machine learning models require large amounts of computation", status: "unverified" }]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(1)
    expect(spans[0].displayStatus).toBe("unverified")
  })

  it("returns no match when claim is not in text", () => {
    const text = "The sky is blue on clear days."
    const claims = [{ claim: "Water freezes at zero degrees", status: "verified" }]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(0)
  })

  it("de-overlaps spans keeping first", () => {
    const text = "The quick brown fox jumps over the lazy dog."
    const claims = [
      { claim: "quick brown fox", status: "verified" },
      { claim: "brown fox jumps", status: "unverified" },
    ]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(1)
    expect(spans[0].claim).toBe("quick brown fox")
  })

  it("maps evasion claim_type correctly", () => {
    const text = "I cannot access real-time data."
    const claims = [{ claim: "I cannot access real-time data", status: "unverified", claim_type: "evasion" }]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(1)
    expect(spans[0].displayStatus).toBe("evasion")
  })

  it("matches with whitespace-optional (DOM block boundaries)", () => {
    // DOM textContent often has no spaces between block elements
    const domText = "First paragraph.Second paragraph with important claim here."
    const claims = [{ claim: "important claim here", status: "verified" }]
    const spans = matchClaimsToText("", claims, domText)
    expect(spans).toHaveLength(1)
  })

  it("matches via longest contiguous word sequence", () => {
    const text = "Python was created by Guido van Rossum in the Netherlands in 1991."
    // LLM paraphrases but 4+ words match verbatim
    const claims = [{ claim: "Python was initially designed by Guido van Rossum in 1991", status: "verified" }]
    const spans = matchClaimsToText(text, claims)
    expect(spans).toHaveLength(1)
  })

  it("uses domTextContent for position coordinates when provided", () => {
    const rawMd = "**Bold claim** is important."
    const domText = "Bold claim is important."
    const claims = [{ claim: "Bold claim is important", status: "verified" }]
    const spans = matchClaimsToText(rawMd, claims, domText)
    expect(spans).toHaveLength(1)
    expect(spans[0].start).toBe(0)
    expect(spans[0].end).toBe(23)
  })
})
