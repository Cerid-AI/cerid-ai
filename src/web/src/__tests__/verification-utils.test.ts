// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest"
import { getClaimDisplayStatus } from "@/lib/verification-utils"

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
