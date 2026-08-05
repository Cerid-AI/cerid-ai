// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { ProvenanceMarker } from "./provenance-marker"

describe("ProvenanceMarker", () => {
  it("renders auto-generated marker with default label", () => {
    render(<ProvenanceMarker kind="auto" />)
    expect(screen.getByText("Auto")).toBeInTheDocument()
  })

  it("renders user-edited marker", () => {
    render(<ProvenanceMarker kind="user-edited" />)
    expect(screen.getByText("Edited")).toBeInTheDocument()
  })

  it("renders contradicted marker", () => {
    render(<ProvenanceMarker kind="contradicted" />)
    expect(screen.getByText("Contradicted")).toBeInTheDocument()
  })

  it("renders uncertain marker", () => {
    render(<ProvenanceMarker kind="uncertain" />)
    expect(screen.getByText("Uncertain")).toBeInTheDocument()
  })

  it("supports label override", () => {
    render(<ProvenanceMarker kind="auto" label="Synthesized" />)
    expect(screen.getByText("Synthesized")).toBeInTheDocument()
  })

  it("exposes accessible description for screen readers", () => {
    render(<ProvenanceMarker kind="contradicted" />)
    const el = screen.getByLabelText(/contradicted/i)
    expect(el.getAttribute("aria-label")).toContain("unresolved contradictions")
  })
})
