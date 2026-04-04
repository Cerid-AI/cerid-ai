// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"

const DEFAULT_SUMMARY = {
  providerCount: 1,
  providerNames: ["Openrouter"],
  domainCount: 3,
  ollamaEnabled: false,
  ollamaModel: null,
  documentCount: 0,
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

  it("shows Clean & Simple and Advanced buttons", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(screen.getByText("Clean & Simple")).toBeInTheDocument()
    expect(screen.getByText("Advanced")).toBeInTheDocument()
  })

  it("has Clean & Simple mode visually selected by default", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    const simpleButton = screen.getByText("Clean & Simple").closest("button")
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

  it("calls onSelectMode('simple') when clicking Clean & Simple", () => {
    render(
      <ModeSelectionStep
        selectedMode="advanced"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    const simpleButton = screen.getByText("Clean & Simple").closest("button")
    fireEvent.click(simpleButton!)
    expect(onSelectMode).toHaveBeenCalledWith("simple")
  })

  it("shows config summary with provider names and document count", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={{
          providerCount: 2,
          providerNames: ["Openrouter", "Anthropic"],
          domainCount: 4,
          ollamaEnabled: true,
          ollamaModel: "llama3.2:3b",
          documentCount: 3,
        }}
      />,
    )
    expect(screen.getByText(/Openrouter \+ Anthropic configured/)).toBeInTheDocument()
    expect(screen.getByText(/3 documents ingested/)).toBeInTheDocument()
    expect(screen.getByText(/llama3\.2:3b/)).toBeInTheDocument()
  })

  it("shows 'not configured' when ollama is disabled", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(screen.getByText(/not configured/)).toBeInTheDocument()
  })
})
