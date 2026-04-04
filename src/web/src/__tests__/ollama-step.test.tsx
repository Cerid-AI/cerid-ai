// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  pullOllamaModel: vi.fn().mockResolvedValue(new Response()),
  fetchOllamaRecommendations: vi.fn().mockResolvedValue({ hardware: null, models: [] }),
}))

import { OllamaStep } from "@/components/setup/ollama-step"

const DEFAULT_STATE = {
  detected: false,
  enabled: false,
  model: null,
  pulling: false,
}

interface OllamaState {
  detected: boolean
  enabled: boolean
  model: string | null
  pulling: boolean
}

const onChange = vi.fn<(state: OllamaState) => void>()

beforeEach(() => {
  vi.restoreAllMocks()
  onChange.mockClear()
})

describe("OllamaStep", () => {
  it("shows 'Local LLM' heading", () => {
    render(
      <OllamaStep
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Local LLM")).toBeInTheDocument()
  })

  it("shows 'Connected' badge when ollamaDetected is true", () => {
    render(
      <OllamaStep
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Connected")).toBeInTheDocument()
  })

  it("shows 'Not detected' badge when ollamaDetected is false", () => {
    render(
      <OllamaStep
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Not detected")).toBeInTheDocument()
  })

  it("shows install link when not detected", () => {
    render(
      <OllamaStep
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    const installLink = screen.getByText("Install Ollama")
    expect(installLink).toBeInTheDocument()
    expect(installLink.closest("a")).toHaveAttribute("href", "https://ollama.com/download")
  })

  it("shows enable toggle when detected", () => {
    render(
      <OllamaStep
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Enable for pipeline tasks")).toBeInTheDocument()
  })

  it("shows installed models when detected with models", () => {
    render(
      <OllamaStep
        ollamaDetected={true}
        ollamaModels={["llama3.2:3b", "mistral:7b"]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Installed Models")).toBeInTheDocument()
    expect(screen.getByText("llama3.2:3b")).toBeInTheDocument()
    expect(screen.getByText("mistral:7b")).toBeInTheDocument()
  })

  it("does not show install link when detected", () => {
    render(
      <OllamaStep
        ollamaDetected={true}
        ollamaModels={["llama3.2:3b"]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    expect(screen.queryByText("Install Ollama")).not.toBeInTheDocument()
  })
})
