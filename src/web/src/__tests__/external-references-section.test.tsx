// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for ExternalReferencesSection (Phase API.3).
 *
 * Coverage:
 * - Renders one card per reference
 * - Section hidden when refs empty
 * - Source badge text rendered
 * - Title and snippet rendered
 * - External link anchor rendered when url is present
 * - No external link when url is null
 * - axe-clean (settled and empty states)
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"
import { ExternalReferencesSection } from "@/components/wiki/external-references-section"
import type { ExternalReference } from "@/lib/types/wiki"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const FETCHED_AT = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() // 2h ago

function makeRef(overrides: Partial<ExternalReference> = {}): ExternalReference {
  return {
    source: "wikipedia",
    source_display: "Wikipedia",
    title: "Alan Turing",
    snippet: "Pioneer of computer science and artificial intelligence.",
    url: "https://en.wikipedia.org/wiki/Alan_Turing",
    fetched_at: FETCHED_AT,
    metadata: {},
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Empty state — section not rendered
// ---------------------------------------------------------------------------

describe("ExternalReferencesSection — empty state", () => {
  it("renders nothing when refs is empty", () => {
    const { container } = render(<ExternalReferencesSection refs={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it("does not render the heading when refs is empty", () => {
    render(<ExternalReferencesSection refs={[]} />)
    expect(screen.queryByText(/external references/i)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Settled state — one or more refs
// ---------------------------------------------------------------------------

describe("ExternalReferencesSection — settled state", () => {
  it("renders the section heading", () => {
    render(<ExternalReferencesSection refs={[makeRef()]} />)
    expect(screen.getByRole("region", { name: /external references/i })).toBeTruthy()
  })

  it("renders one card per reference", () => {
    const refs = [
      makeRef({ source: "wikipedia", title: "Alan Turing" }),
      makeRef({ source: "github", source_display: "GitHub", title: "owner/repo", url: "https://github.com/owner/repo" }),
    ]
    render(<ExternalReferencesSection refs={refs} />)
    const cards = screen.getAllByTestId("external-reference-card")
    expect(cards).toHaveLength(2)
  })

  it("renders source badge with display name", () => {
    render(<ExternalReferencesSection refs={[makeRef({ source_display: "Wikipedia" })]} />)
    expect(screen.getByText("Wikipedia")).toBeTruthy()
  })

  it("renders reference title", () => {
    render(<ExternalReferencesSection refs={[makeRef({ title: "Alan Turing" })]} />)
    expect(screen.getByText("Alan Turing")).toBeTruthy()
  })

  it("renders reference snippet", () => {
    render(
      <ExternalReferencesSection
        refs={[makeRef({ snippet: "Pioneer of computer science." })]}
      />,
    )
    expect(screen.getByText(/Pioneer of computer science/)).toBeTruthy()
  })

  it("renders external link anchor when url is provided", () => {
    render(
      <ExternalReferencesSection
        refs={[makeRef({ url: "https://en.wikipedia.org/wiki/Alan_Turing" })]}
      />,
    )
    const link = screen.getByRole("link")
    expect(link).toBeTruthy()
    expect(link.getAttribute("href")).toBe("https://en.wikipedia.org/wiki/Alan_Turing")
    expect(link.getAttribute("target")).toBe("_blank")
    expect(link.getAttribute("rel")).toContain("noopener")
  })

  it("does NOT render external link when url is null", () => {
    render(<ExternalReferencesSection refs={[makeRef({ url: null })]} />)
    expect(screen.queryByRole("link")).toBeNull()
  })

  it("renders fetched-at relative time", () => {
    render(<ExternalReferencesSection refs={[makeRef()]} />)
    // 2h ago → "2h ago"
    expect(screen.getByText(/ago/)).toBeTruthy()
  })

  it("renders multiple refs with distinct source badges", () => {
    const refs = [
      makeRef({ source: "wikipedia", source_display: "Wikipedia", title: "Turing" }),
      makeRef({ source: "github", source_display: "GitHub", title: "repo", url: "https://github.com/x/y" }),
    ]
    render(<ExternalReferencesSection refs={refs} />)
    expect(screen.getByText("Wikipedia")).toBeTruthy()
    expect(screen.getByText("GitHub")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// axe-clean
// ---------------------------------------------------------------------------

describe("ExternalReferencesSection — axe-clean", () => {
  it("settled state is axe-clean", async () => {
    const { container } = render(
      <ExternalReferencesSection
        refs={[
          makeRef(),
          makeRef({
            source: "github",
            source_display: "GitHub",
            title: "owner/repo",
            url: "https://github.com/owner/repo",
          }),
        ]}
      />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("empty state is axe-clean", async () => {
    const { container } = render(<ExternalReferencesSection refs={[]} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
