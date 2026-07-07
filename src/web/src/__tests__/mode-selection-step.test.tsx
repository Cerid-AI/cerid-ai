// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { axe } from "jest-axe"
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
          providerNames: ["OpenRouter", "Anthropic"],
          domainCount: 4,
          ollamaEnabled: true,
          ollamaModel: "llama3.2:3b",
          documentCount: 3,
          inferenceBackend: "ollama",
        }}
      />,
    )
    expect(screen.getByText(/OpenRouter \+ Anthropic configured/)).toBeInTheDocument()
    expect(screen.getByText(/3 documents ingested/)).toBeInTheDocument()
    expect(screen.getByText(/llama3\.2:3b/)).toBeInTheDocument()
  })

  it("shows inference backend in summary (F-04-05)", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={{
          ...DEFAULT_SUMMARY,
          inferenceBackend: "quenchforge",
        }}
      />,
    )
    expect(screen.getByText(/Backend: Quenchforge/)).toBeInTheDocument()
  })

  it("hides chat-model line when ollama disabled (F-04-08: no reranker mislabel)", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    // Previously rendered "Local LLM: not configured" — the line is now
    // omitted entirely so a reranker model can't be mislabelled as a chat LLM.
    expect(screen.queryByText(/not configured/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Local LLM:/)).not.toBeInTheDocument()
  })
})

describe("ModeSelectionStep — axe-clean", () => {
  it("is axe-clean with Clean & Simple selected (default)", async () => {
    const { container } = render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with Advanced selected", async () => {
    const { container } = render(
      <ModeSelectionStep
        selectedMode="advanced"
        onSelectMode={onSelectMode}
        configSummary={DEFAULT_SUMMARY}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with a populated config summary and hardware recommendation", async () => {
    const { container } = render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={onSelectMode}
        configSummary={{
          providerCount: 2,
          providerNames: ["OpenRouter", "Anthropic"],
          domainCount: 4,
          ollamaEnabled: true,
          ollamaModel: "llama3.2:3b",
          documentCount: 3,
          inferenceBackend: "ollama",
        }}
        hardware={{ ram_gb: 32, cpu: "Apple M2 Max", gpu: "Apple M2 Max", gpu_acceleration: "metal" }}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
