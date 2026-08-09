// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import ReactMarkdown from "react-markdown"
import { buildLinkifyComponents } from "../linkify"
import type { LinkifyEntity } from "../linkify"

const ENTITIES: LinkifyEntity[] = [
  {
    slug: "python",
    name: "Python",
    entity_type: "OTHER",
    has_summary: true,
    one_liner: "High-level programming language.",
  },
  {
    slug: "cpython",
    name: "CPython",
    entity_type: "ORG",
    has_summary: false,
    one_liner: null,
  },
]

function renderMarkdown(content: string, onSelect: (slug: string) => void = vi.fn()) {
  const components = buildLinkifyComponents({ entities: ENTITIES, onSelect })
  return render(
    <ReactMarkdown components={components}>{content}</ReactMarkdown>,
  )
}

describe("linkify — three-state link semantics", () => {
  it("renders a normal link for entity with has_summary true", () => {
    renderMarkdown("Python is great.")
    const btn = screen.getByRole("button", { name: "Navigate to Python" })
    expect(btn).toBeTruthy()
  })

  it("renders a stub link for entity with has_summary false", () => {
    renderMarkdown("CPython is the reference implementation.")
    const btn = screen.getByRole("button", { name: /CPython.*summary pending/ })
    expect(btn).toBeTruthy()
  })

  it("stub link has dashed underline class", () => {
    renderMarkdown("CPython is the reference implementation.")
    const btn = screen.getByRole("button", { name: /CPython.*summary pending/ })
    expect(btn.className).toContain("decoration-dashed")
  })

  it("normal link does not have dashed underline class", () => {
    renderMarkdown("Python is great.")
    const btn = screen.getByRole("button", { name: "Navigate to Python" })
    expect(btn.className).not.toContain("decoration-dashed")
  })

  it("calls onSelect with slug when normal link is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderMarkdown("Python is great.", onSelect)
    await user.click(screen.getByRole("button", { name: "Navigate to Python" }))
    expect(onSelect).toHaveBeenCalledWith("python")
  })

  it("calls onSelect with stub slug when stub link is clicked", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    renderMarkdown("CPython is the reference implementation.", onSelect)
    await user.click(screen.getByRole("button", { name: /CPython.*summary pending/ }))
    expect(onSelect).toHaveBeenCalledWith("cpython")
  })
})

describe("linkify — link-once-per-paragraph rule", () => {
  it("links an entity name only once per paragraph", () => {
    renderMarkdown("Python and Python are great.")
    const buttons = screen.getAllByRole("button", { name: /Python/ })
    expect(buttons).toHaveLength(1)
  })
})

describe("linkify — word boundary matching", () => {
  it("does NOT link partial word matches (e.g. 'Pythons')", () => {
    const { container } = renderMarkdown("Pythons are snakes.")
    const buttons = container.querySelectorAll("button")
    expect(buttons).toHaveLength(0)
  })
})

describe("linkify — accessibility", () => {
  it("is axe-clean with linked text", async () => {
    const { container } = renderMarkdown("Python and CPython are widely used.")
    const results = await axe(container)
    expect(results).toHaveNoViolations()
  })
})
