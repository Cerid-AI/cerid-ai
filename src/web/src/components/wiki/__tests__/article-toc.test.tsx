// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { axe } from "jest-axe"
import { ArticleToc } from "../article-toc"

const ENTRIES = [
  { id: "wiki-section-activity", label: "Activity & graph" },
  { id: "references", label: "References" },
  { id: "wiki-section-history", label: "Page history" },
]

describe("ArticleToc — rendering", () => {
  it("renders a nav landmark with accessible name", () => {
    render(<ArticleToc entries={ENTRIES} />)
    const nav = screen.getByRole("navigation", { name: "Contents" })
    expect(nav).toBeTruthy()
  })

  it("renders all entries as links", () => {
    render(<ArticleToc entries={ENTRIES} />)
    expect(screen.getAllByRole("link", { name: /Activity/ }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole("link", { name: /References/ }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole("link", { name: /Page history/ }).length).toBeGreaterThan(0)
  })

  it("links point to anchors", () => {
    const { container } = render(<ArticleToc entries={ENTRIES} />)
    const firstLink = container.querySelector('a[href="#wiki-section-activity"]')
    expect(firstLink).not.toBeNull()
  })

  it("renders nothing when entries array is empty", () => {
    const { container } = render(<ArticleToc entries={[]} />)
    expect(container.firstChild).toBeNull()
  })
})

describe("ArticleToc — accessibility", () => {
  it("is axe-clean with data", async () => {
    const { container } = render(<ArticleToc entries={ENTRIES} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("is axe-clean when empty (null render)", async () => {
    const { container } = render(<ArticleToc entries={[]} />)
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })

  it("accepts custom ariaLabel to disambiguate multiple instances", () => {
    render(<ArticleToc entries={ENTRIES} ariaLabel="Contents (mobile)" />)
    expect(screen.getByRole("navigation", { name: "Contents (mobile)" })).toBeTruthy()
  })
})
