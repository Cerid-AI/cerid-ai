// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { VerifiedResponse } from "@/components/verification/verified-response"
import type { ClaimVerificationFE } from "@/components/verification/types"

afterEach(cleanup)

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

const makeVerified = (overrides: Partial<ClaimVerificationFE> = {}): ClaimVerificationFE => ({
  claim: "The sky is blue",
  status: "verified",
  confidence: 0.95,
  source_artifact_id: "art-123",
  source_filename: "science.pdf",
  source_domain: "general",
  source_snippet: "The sky appears blue due to Rayleigh scattering.",
  source_urls: ["https://example.com/sky"],
  verification_method: "kb_nli",
  nli_entailment: 0.92,
  ...overrides,
})

const makePartial = (overrides: Partial<ClaimVerificationFE> = {}): ClaimVerificationFE => ({
  claim: "Water boils at 100°C at sea level",
  status: "uncertain",
  confidence: 0.55,
  source_artifact_id: "",
  source_filename: "chem.pdf",
  source_urls: [],
  verification_method: "kb",
  ...overrides,
})

// "unverified" from the backend means two different things. This fixture is
// the soft one: the KB simply held no matching evidence.
const makeUnverified = (overrides: Partial<ClaimVerificationFE> = {}): ClaimVerificationFE => ({
  claim: "The moon is made of cheese",
  status: "unverified",
  confidence: 0.1,
  source_artifact_id: "",
  source_filename: "",
  source_urls: [],
  verification_method: "kb",
  ...overrides,
})

// The hard one: an independent cross-model or web-search pass actively found
// the claim to be wrong.
const makeRefuted = (overrides: Partial<ClaimVerificationFE> = {}): ClaimVerificationFE => ({
  claim: "The Eiffel Tower is in Berlin",
  status: "unverified",
  confidence: 0.05,
  source_artifact_id: "",
  source_filename: "",
  source_urls: [],
  verification_method: "cross_model",
  ...overrides,
})

// ---------------------------------------------------------------------------
// State: idle
// ---------------------------------------------------------------------------

describe("VerifiedResponse — idle state", () => {
  it("renders nothing when no claims, no streaming, no error", () => {
    const { container } = render(
      <VerifiedResponse claims={[]} streaming={false} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it("snapshot matches idle state", () => {
    const { container } = render(<VerifiedResponse claims={[]} />)
    expect(container).toMatchSnapshot()
  })
})

// ---------------------------------------------------------------------------
// State: streaming
// ---------------------------------------------------------------------------

describe("VerifiedResponse — streaming state", () => {
  it("renders skeleton placeholders while streaming", () => {
    render(<VerifiedResponse claims={[]} streaming={true} />)
    // Skeletons are animate-pulse divs
    const skeletons = document.querySelectorAll("[class*=animate-pulse]")
    expect(skeletons.length).toBeGreaterThan(0)
  })

  it("has sr-only Verifying text", () => {
    render(<VerifiedResponse claims={[]} streaming={true} />)
    expect(screen.getByText("Verifying…")).toBeTruthy()
  })

  it("snapshot matches streaming state", () => {
    const { container } = render(<VerifiedResponse claims={[]} streaming={true} />)
    expect(container).toMatchSnapshot()
  })

  it("is axe-clean", async () => {
    const { container } = render(<VerifiedResponse claims={[]} streaming={true} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// State: settled — verified band
// ---------------------------------------------------------------------------

describe("VerifiedResponse — settled state, verified claim", () => {
  it("renders one badge for a verified claim", () => {
    render(<VerifiedResponse claims={[makeVerified()]} />)
    // Badge shows "Verified by 1 source"
    expect(screen.getByText(/Verified by 1 source/)).toBeTruthy()
  })

  it("badge has correct aria-label for verified band", () => {
    render(<VerifiedResponse claims={[makeVerified()]} />)
    const btn = screen.getByRole("button", { name: /Claim verified by 1 source/ })
    expect(btn).toBeTruthy()
  })

  it("snapshot matches verified settled state", () => {
    const { container } = render(<VerifiedResponse claims={[makeVerified()]} />)
    expect(container).toMatchSnapshot()
  })

  it("is axe-clean", async () => {
    const { container } = render(<VerifiedResponse claims={[makeVerified()]} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// State: settled — multiple bands
// ---------------------------------------------------------------------------

describe("VerifiedResponse — settled state, multiple bands", () => {
  it("renders partial and unverified badges", () => {
    render(
      <VerifiedResponse claims={[makePartial(), makeUnverified()]} />,
    )
    expect(screen.getByText("Partial source")).toBeTruthy()
    expect(screen.getByText("No source")).toBeTruthy()
  })

  it("renders two badges for two claims", () => {
    render(
      <VerifiedResponse claims={[makeVerified(), makeUnverified()]} />,
    )
    const buttons = screen.getAllByRole("button")
    expect(buttons.length).toBe(2)
  })

  it("partial badge has correct aria-label", () => {
    render(<VerifiedResponse claims={[makePartial()]} />)
    expect(
      screen.getByRole("button", { name: /Claim has partial source/ }),
    ).toBeTruthy()
  })

  it("unverified badge has correct aria-label", () => {
    render(<VerifiedResponse claims={[makeUnverified()]} />)
    expect(
      screen.getByRole("button", { name: /Claim has no source/ }),
    ).toBeTruthy()
  })

  // F236 — deriveBand collapsed every non-verified, non-uncertain claim into
  // one "unverified" band labelled "No source", so a claim a cross-model or
  // web-search pass actively found wrong rendered identically to one the KB
  // simply had no evidence for. getClaimDisplayStatus already drew that
  // distinction for the audit surface; the badge did not.
  it("labels a refuted claim as refuted, not as missing a source", () => {
    render(<VerifiedResponse claims={[makeRefuted()]} />)
    expect(screen.getByText("Refuted")).toBeTruthy()
    expect(screen.queryByText("No source")).toBeNull()
  })

  it("keeps refuted and no-evidence claims distinguishable side by side", () => {
    render(<VerifiedResponse claims={[makeRefuted(), makeUnverified()]} />)
    const bands = screen
      .getAllByRole("button")
      .map((b) => b.getAttribute("data-verification-band"))
    expect(bands).toEqual(["refuted", "unverified"])
    expect(screen.getByText("Refuted")).toBeTruthy()
    expect(screen.getByText("No source")).toBeTruthy()
  })

  it("refuted badge names the verdict in its aria-label", () => {
    render(<VerifiedResponse claims={[makeRefuted()]} />)
    expect(screen.getByRole("button", { name: /refuted/i })).toBeTruthy()
  })

  it("treats a web-search refutation the same as a cross-model one", () => {
    render(<VerifiedResponse claims={[makeRefuted({ verification_method: "web_search" })]} />)
    expect(
      screen.getByRole("button")?.getAttribute("data-verification-band"),
    ).toBe("refuted")
  })

  it("is axe-clean with a refuted claim", async () => {
    const { container } = render(<VerifiedResponse claims={[makeRefuted(), makeUnverified()]} />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("snapshot matches multi-band settled state", () => {
    const { container } = render(
      <VerifiedResponse claims={[makeVerified(), makePartial(), makeUnverified()]} />,
    )
    expect(container).toMatchSnapshot()
  })

  it("is axe-clean with multiple bands", async () => {
    const { container } = render(
      <VerifiedResponse claims={[makeVerified(), makePartial(), makeUnverified()]} />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// State: error
// ---------------------------------------------------------------------------

describe("VerifiedResponse — error state", () => {
  it("renders alert with source unreachable message", () => {
    render(
      <VerifiedResponse
        claims={[]}
        error={new Error("network timeout")}
      />,
    )
    expect(screen.getByRole("alert")).toBeTruthy()
    expect(screen.getByText(/Source unreachable/)).toBeTruthy()
  })

  it("includes error message in alert", () => {
    render(
      <VerifiedResponse claims={[]} error={new Error("403 Forbidden")} />,
    )
    expect(screen.getByText(/403 Forbidden/)).toBeTruthy()
  })

  it("snapshot matches error state", () => {
    const { container } = render(
      <VerifiedResponse claims={[]} error={new Error("test error")} />,
    )
    expect(container).toMatchSnapshot()
  })

  it("is axe-clean", async () => {
    const { container } = render(
      <VerifiedResponse claims={[]} error={new Error("test")} />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})

// ---------------------------------------------------------------------------
// Keyboard navigation
// ---------------------------------------------------------------------------

describe("VerifiedResponse — keyboard navigation", () => {
  it("badge buttons are focusable via Tab", async () => {
    const user = userEvent.setup()
    render(
      <VerifiedResponse claims={[makeVerified(), makeUnverified()]} />,
    )
    const buttons = screen.getAllByRole("button")
    expect(buttons.length).toBe(2)
    // Tab to first badge
    await user.tab()
    expect(document.activeElement).toBe(buttons[0])
    // Tab to second badge
    await user.tab()
    expect(document.activeElement).toBe(buttons[1])
  })
})
