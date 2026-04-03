// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"

const DEFAULT_SUMMARY = {
  providerCount: 1,
  domainCount: 3,
  ollamaEnabled: false,
}

const onSelectMode = vi.fn<(mode: "simple" | "advanced") => void>()

beforeEach(() => {
  onSelectMode.mockClear()
})

describe("ModeSelectionStep", () => {
  it("shows 'Choose Your Mode' heading", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(screen.getByText("Choose Your Mode")).toBeInTheDocument()
  })

  it("shows Simple and Advanced buttons", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(screen.getByText("Simple")).toBeInTheDocument()
    expect(screen.getByText("Advanced")).toBeInTheDocument()
  })

  it("has Simple mode visually selected by default", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    const simpleButton = screen.getByText("Simple").closest("button")
    expect(simpleButton?.className).toContain("border-brand")

    const advancedButton = screen.getByText("Advanced").closest("button")
    expect(advancedButton?.className).toContain("border-muted")
  })

  it("calls onSelectMode('advanced') when clicking Advanced", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    const advancedButton = screen.getByText("Advanced").closest("button")
    fireEvent.click(advancedButton!)
    expect(onSelectMode).toHaveBeenCalledWith("advanced")
  })

  it("calls onSelectMode('simple') when clicking Simple", () => {
    render(
      <ModeSelectionStep
        selectedMode="advanced"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    const simpleButton = screen.getByText("Simple").closest("button")
    fireEvent.click(simpleButton!)
    expect(onSelectMode).toHaveBeenCalledWith("simple")
  })

  it("shows config summary text with provider, domain, and ollama info", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={{ providerCount: 2, domainCount: 4, ollamaEnabled: true }}
      />,
    )
    expect(screen.getByText(/2 LLM providers/)).toBeInTheDocument()
    expect(screen.getByText(/4 KB domains/)).toBeInTheDocument()
    expect(screen.getByText(/enabled/)).toBeInTheDocument()
  })

  it("shows 'disabled' when ollama is not enabled", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(screen.getByText(/disabled/)).toBeInTheDocument()
  })
})
