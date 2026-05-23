// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { KnowledgeSourceSelector } from "@/components/chat/knowledge-source-selector"

describe("KnowledgeSourceSelector", () => {
  it("renders the active mode's short label on the trigger", () => {
    const { rerender } = render(
      <KnowledgeSourceSelector value="kb" onChange={() => {}} />,
    )
    expect(screen.getByRole("button", { name: /local kb only/i })).toBeInTheDocument()

    rerender(<KnowledgeSourceSelector value="kb_web" onChange={() => {}} />)
    expect(screen.getByRole("button", { name: /local kb \+ web/i })).toBeInTheDocument()

    rerender(<KnowledgeSourceSelector value="llm_kb" onChange={() => {}} />)
    expect(screen.getByRole("button", { name: /llm \+ kb grounding/i })).toBeInTheDocument()
  })

  it("opens a listbox with all 3 modes when clicked", () => {
    render(<KnowledgeSourceSelector value="llm_kb" onChange={() => {}} />)
    fireEvent.click(screen.getByRole("button", { name: /knowledge source/i }))
    const options = screen.getAllByRole("option")
    expect(options).toHaveLength(3)
    expect(screen.getByText("Local KB only")).toBeInTheDocument()
    expect(screen.getByText("Local KB + Web")).toBeInTheDocument()
    expect(screen.getByText("LLM + KB grounding")).toBeInTheDocument()
  })

  it("marks the current value as aria-selected and shows 'Active'", () => {
    render(<KnowledgeSourceSelector value="kb_web" onChange={() => {}} />)
    fireEvent.click(screen.getByRole("button", { name: /knowledge source/i }))
    const options = screen.getAllByRole("option")
    const active = options.find((o) => o.getAttribute("aria-selected") === "true")
    expect(active).toBeDefined()
    expect(active?.textContent).toMatch(/Local KB \+ Web/i)
    expect(screen.getByText(/Active/i)).toBeInTheDocument()
  })

  it("fires onChange with the picked id and closes the popover", () => {
    const onChange = vi.fn()
    render(<KnowledgeSourceSelector value="llm_kb" onChange={onChange} />)
    fireEvent.click(screen.getByRole("button", { name: /knowledge source/i }))
    fireEvent.click(screen.getByText("Local KB only"))
    expect(onChange).toHaveBeenCalledWith("kb")
  })
})
