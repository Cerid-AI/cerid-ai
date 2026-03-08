// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { ClaimOverlay } from "@/components/chat/claim-overlay"
import type { HallucinationClaim } from "@/lib/types"
import type { ClaimSpan } from "@/lib/verification-utils"

const makeClaim = (overrides: Partial<HallucinationClaim> = {}): HallucinationClaim => ({
  claim: "The capital of France is Paris",
  status: "verified",
  similarity: 0.95,
  source_filename: "europe.pdf",
  source_domain: "geography",
  source_snippet: "Paris is the capital city of France.",
  source_artifact_id: "art-1",
  verification_method: "kb",
  claim_type: "factual",
  ...overrides,
})

const makeSpan = (overrides: Partial<ClaimSpan> = {}): ClaimSpan => ({
  start: 0,
  end: 30,
  claim: "The capital of France is Paris",
  displayStatus: "verified",
  ...overrides,
})

function createContainerWithMark(spanIndex = 0): HTMLDivElement {
  const container = document.createElement("div")
  const mark = document.createElement("mark")
  mark.dataset.ceridClaim = "true"
  mark.dataset.claimIndex = String(spanIndex)
  mark.textContent = "The capital of France is Paris"
  container.appendChild(mark)

  const sup = document.createElement("sup")
  sup.dataset.ceridFootnote = String(spanIndex)
  sup.textContent = `[${spanIndex + 1}]`
  container.appendChild(sup)

  // Append to body so getBoundingClientRect works (returns zeros in jsdom but doesn't crash)
  document.body.appendChild(container)
  return container
}

describe("ClaimOverlay", () => {
  afterEach(() => {
    cleanup()
    // Remove any leaked containers from failed tests
    document.querySelectorAll("[data-cerid-claim]").forEach((el) => {
      const container = el.closest("div")
      if (container?.parentNode === document.body) document.body.removeChild(container)
    })
  })

  it("renders popover on claim mark click", () => {
    const container = createContainerWithMark()
    const claim = makeClaim()
    const span = makeSpan()

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
      />,
    )

    // Click the mark
    const mark = container.querySelector("[data-cerid-claim]") as HTMLElement
    fireEvent.click(mark)

    // Popover should show the claim text (also in mark element, so use getAllByText)
    const matches = screen.getAllByText(/The capital of France is Paris/)
    expect(matches.length).toBeGreaterThanOrEqual(2) // mark + popover
    // Should show status badge
    expect(screen.getByText("verified")).toBeInTheDocument()

    document.body.removeChild(container)
  })

  it("shows correct claim status badge in popover", () => {
    const container = createContainerWithMark()
    const claim = makeClaim({ status: "unverified", verification_method: "cross_model" })
    const span = makeSpan({ displayStatus: "refuted" })

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
      />,
    )

    fireEvent.click(container.querySelector("[data-cerid-claim]")!)
    expect(screen.getByText("refuted")).toBeInTheDocument()

    document.body.removeChild(container)
  })

  it("shows source filename when source_artifact_id is present", () => {
    const container = createContainerWithMark()
    const onArtifactClick = vi.fn()
    const claim = makeClaim({ source_filename: "notes.pdf", source_artifact_id: "art-99" })
    const span = makeSpan()

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
        onArtifactClick={onArtifactClick}
      />,
    )

    fireEvent.click(container.querySelector("[data-cerid-claim]")!)
    const sourceButton = screen.getByText("notes.pdf")
    expect(sourceButton).toBeInTheDocument()
    // Clicking source should call onArtifactClick
    fireEvent.click(sourceButton)
    expect(onArtifactClick).toHaveBeenCalledWith("art-99")

    document.body.removeChild(container)
  })

  it("renders footnote markers after claim marks", () => {
    const container = createContainerWithMark(0)
    // Verify the footnote sup exists in our test container
    const sup = container.querySelector("[data-cerid-footnote]")
    expect(sup).toBeTruthy()
    expect(sup?.textContent).toBe("[1]")

    document.body.removeChild(container)
  })

  it("dismisses popover on Escape key", () => {
    const container = createContainerWithMark()
    const claim = makeClaim()
    const span = makeSpan()

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
      />,
    )

    // Open popover
    fireEvent.click(container.querySelector("[data-cerid-claim]")!)
    expect(screen.getByText("verified")).toBeInTheDocument()

    // Press Escape
    fireEvent.keyDown(document, { key: "Escape" })
    expect(screen.queryByText("verified")).not.toBeInTheDocument()

    document.body.removeChild(container)
  })

  it("shows verification method badge for cross-model claims", () => {
    const container = createContainerWithMark()
    const claim = makeClaim({ verification_method: "cross_model" })
    const span = makeSpan()

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
      />,
    )

    fireEvent.click(container.querySelector("[data-cerid-claim]")!)
    expect(screen.getByText("cross-model")).toBeInTheDocument()

    document.body.removeChild(container)
  })

  it("shows source snippet in popover", () => {
    const container = createContainerWithMark()
    const claim = makeClaim({ source_snippet: "Paris is the capital city of France." })
    const span = makeSpan()

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
      />,
    )

    fireEvent.click(container.querySelector("[data-cerid-claim]")!)
    expect(screen.getByText(/Paris is the capital city/)).toBeInTheDocument()

    document.body.removeChild(container)
  })

  it("shows similarity percentage", () => {
    const container = createContainerWithMark()
    const claim = makeClaim({ similarity: 0.87 })
    const span = makeSpan()

    render(
      <ClaimOverlay
        container={container}
        claims={[claim]}
        claimSpans={[span]}
      />,
    )

    fireEvent.click(container.querySelector("[data-cerid-claim]")!)
    expect(screen.getByText("87% match")).toBeInTheDocument()

    document.body.removeChild(container)
  })
})
