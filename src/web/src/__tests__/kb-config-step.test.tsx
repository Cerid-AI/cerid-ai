// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { KBConfigStep } from "@/components/setup/kb-config-step"

const DEFAULT_CONFIG = {
  archivePath: "~/cerid-archive",
  domains: ["general"],
  lightweightMode: false,
  watchFolder: false,
}

interface KBConfigState {
  archivePath: string
  domains: string[]
  lightweightMode: boolean
  watchFolder: boolean
}

const onChange = vi.fn<(config: KBConfigState) => void>()

beforeEach(() => {
  onChange.mockClear()
})

describe("KBConfigStep", () => {
  it("renders 'Knowledge Base' heading", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    expect(screen.getByText("Knowledge Base")).toBeInTheDocument()
  })

  it("shows archive path input with current value", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    const input = screen.getByDisplayValue("~/cerid-archive")
    expect(input).toBeInTheDocument()
  })

  it("shows all 5 domain checkboxes", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    expect(screen.getByText("Coding")).toBeInTheDocument()
    expect(screen.getByText("Finance")).toBeInTheDocument()
    expect(screen.getByText("General")).toBeInTheDocument()
    expect(screen.getByText("Personal")).toBeInTheDocument()
    expect(screen.getByText("Projects")).toBeInTheDocument()
  })

  it("has General domain checked by default", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    // The General domain card should have the selected border style
    const generalLabel = screen.getByText("General").closest("label")
    expect(generalLabel?.className).toContain("border-brand/40")
  })

  it("calls onChange when toggling a domain", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    // Click the Coding domain label to toggle it on
    const codingLabel = screen.getByText("Coding").closest("label")
    fireEvent.click(codingLabel!)
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_CONFIG,
      domains: ["general", "coding"],
    })
  })

  it("shows Watch for new files toggle", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    expect(screen.getByText("Watch for new files")).toBeInTheDocument()
  })

  it("shows lightweight mode warning when lightweightRecommended is true", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={true}
        ramGb={8}
      />,
    )
    expect(screen.getByText("8 GB RAM detected")).toBeInTheDocument()
    expect(screen.getByText("Enable lightweight mode")).toBeInTheDocument()
  })

  it("hides lightweight mode warning when lightweightRecommended is false", () => {
    render(
      <KBConfigStep
        config={DEFAULT_CONFIG}
        onChange={onChange}
        lightweightRecommended={false}
        ramGb={16}
      />,
    )
    expect(screen.queryByText("Enable lightweight mode")).not.toBeInTheDocument()
  })
})
