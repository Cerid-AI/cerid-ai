// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"

const fetchBacklinks = vi.fn()
vi.mock("@/lib/api/wiki", () => ({
  fetchBacklinks: (...args: unknown[]) => fetchBacklinks(...args),
}))

import { WhatLinksHere } from "../what-links-here"

const MOCK_BACKLINKS = {
  backlinks: [
    { slug: "org:tesla", name: "Tesla", entity_type: "ORG", via: "wikilink" as const },
    { slug: "org:spacex", name: "SpaceX", entity_type: "ORG", via: "mention" as const },
    { slug: "person:bezos", name: "Jeff Bezos", entity_type: "PERSON", via: "related" as const },
  ],
}

describe("WhatLinksHere", () => {
  const onSelectEntity = vi.fn()

  beforeEach(() => {
    fetchBacklinks.mockReset()
    onSelectEntity.mockReset()
  })

  // -----------------------------------------------------------------------
  // Collapsed state (initial)
  // -----------------------------------------------------------------------

  it("renders the collapsible trigger", () => {
    fetchBacklinks.mockResolvedValue(MOCK_BACKLINKS)
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    expect(screen.getByRole("button", { name: /what links here/i })).toBeTruthy()
  })

  it("does not fetch when collapsed (idle state)", () => {
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    expect(fetchBacklinks).not.toHaveBeenCalled()
  })

  // -----------------------------------------------------------------------
  // Loading state
  // -----------------------------------------------------------------------

  it("shows a loading skeleton while fetching", async () => {
    // Never resolves immediately — stays in loading state
    fetchBacklinks.mockReturnValue(new Promise(() => {}))
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    // Loading skeleton has role=status + aria-busy
    await waitFor(() =>
      expect(screen.getByRole("status", { name: /loading backlinks/i })).toBeTruthy(),
    )
  })

  // -----------------------------------------------------------------------
  // Loaded state
  // -----------------------------------------------------------------------

  it("renders backlink items after loading", async () => {
    fetchBacklinks.mockResolvedValue(MOCK_BACKLINKS)
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() => expect(screen.getByText("Tesla")).toBeTruthy())
    expect(screen.getByText("SpaceX")).toBeTruthy()
    expect(screen.getByText("Jeff Bezos")).toBeTruthy()
  })

  it("groups items by via-source label", async () => {
    fetchBacklinks.mockResolvedValue(MOCK_BACKLINKS)
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() => expect(screen.getByText("Wikilinks")).toBeTruthy())
    expect(screen.getByText("Co-mentioned in sources")).toBeTruthy()
    expect(screen.getByText(/related/i)).toBeTruthy()
  })

  it("calls onSelectEntity with the correct slug when a row is clicked", async () => {
    fetchBacklinks.mockResolvedValue(MOCK_BACKLINKS)
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() => expect(screen.getByText("Tesla")).toBeTruthy())
    fireEvent.click(screen.getByRole("button", { name: /view entity: tesla/i }))
    expect(onSelectEntity).toHaveBeenCalledWith("org:tesla")
  })

  it("shows the count in the trigger label after load", async () => {
    fetchBacklinks.mockResolvedValue(MOCK_BACKLINKS)
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() => expect(screen.getByText("(3)")).toBeTruthy())
  })

  // -----------------------------------------------------------------------
  // Empty state
  // -----------------------------------------------------------------------

  it("shows empty message when no backlinks exist", async () => {
    fetchBacklinks.mockResolvedValue({ backlinks: [] })
    render(<WhatLinksHere entitySlug="person:nobody" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() =>
      expect(screen.getByText(/no other entities link to this one yet/i)).toBeTruthy(),
    )
  })

  // -----------------------------------------------------------------------
  // Error state
  // -----------------------------------------------------------------------

  it("shows error UI when fetch fails", async () => {
    fetchBacklinks.mockRejectedValue(new Error("Network error"))
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() =>
      expect(screen.getByText(/could not load backlinks/i)).toBeTruthy(),
    )
  })

  it("retries fetch when retry button is clicked after error", async () => {
    fetchBacklinks.mockRejectedValueOnce(new Error("fail"))
    fetchBacklinks.mockResolvedValue({ backlinks: [] })
    render(<WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />)
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() => expect(screen.getByText(/could not load backlinks/i)).toBeTruthy())
    // Click the retry button inside PaneError
    const retryBtn = screen.getByRole("button", { name: /retry/i })
    fireEvent.click(retryBtn)
    await waitFor(() =>
      expect(screen.getByText(/no other entities link to this one yet/i)).toBeTruthy(),
    )
  })

  // -----------------------------------------------------------------------
  // Accessibility
  // -----------------------------------------------------------------------

  it("is axe-clean in collapsed (idle) state", async () => {
    const { container } = render(
      <WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />,
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("is axe-clean in loaded state", async () => {
    fetchBacklinks.mockResolvedValue(MOCK_BACKLINKS)
    const { container } = render(
      <WhatLinksHere entitySlug="person:elon-musk" onSelectEntity={onSelectEntity} />,
    )
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() => expect(screen.getByText("Tesla")).toBeTruthy())
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("is axe-clean in empty state", async () => {
    fetchBacklinks.mockResolvedValue({ backlinks: [] })
    const { container } = render(
      <WhatLinksHere entitySlug="person:nobody" onSelectEntity={onSelectEntity} />,
    )
    fireEvent.click(screen.getByRole("button", { name: /what links here/i }))
    await waitFor(() =>
      expect(screen.getByText(/no other entities link to this one yet/i)).toBeTruthy(),
    )
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
