// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  pullOllamaModel: vi.fn().mockResolvedValue(new Response()),
  fetchOllamaRecommendations: vi.fn().mockResolvedValue({ hardware: null, models: [] }),
}))

import { LocalLLMStep } from "@/components/setup/local-llm-step"

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

// ---- Default (Ollama) backend ----

describe("LocalLLMStep — Ollama backend (default)", () => {
  it("shows 'Local LLM (Ollama)' heading when backend is ollama or null", () => {
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Local LLM (Ollama)")).toBeInTheDocument()
  })

  it("shows 'Connected' badge when ollamaDetected is true", () => {
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
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
      <LocalLLMStep
        inferenceBackend="ollama"
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
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    const installLink = screen.getByText("All platforms")
    expect(installLink).toBeInTheDocument()
    expect(installLink.closest("a")).toHaveAttribute("href", "https://ollama.com/download")
  })

  it("shows enable toggle when detected", () => {
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
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
      <LocalLLMStep
        inferenceBackend="ollama"
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
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={["llama3.2:3b"]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    expect(screen.queryByText("All platforms")).not.toBeInTheDocument()
  })
})

// ---- Quenchforge backend (F-04-03, F-04-04) ----

describe("LocalLLMStep — Quenchforge backend", () => {
  it("shows Quenchforge heading and slot list, not Ollama UX", () => {
    render(
      <LocalLLMStep
        inferenceBackend="quenchforge"
        ollamaDetected={true}
        ollamaModels={["bge-reranker-v2-m3"]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText("Local LLM (Quenchforge)")).toBeInTheDocument()
    expect(screen.getByText("Quenchforge connected")).toBeInTheDocument()
    // Slot list mentions the four canonical Quenchforge slot aliases
    expect(screen.getByText("llama3.1-8b")).toBeInTheDocument()
    expect(screen.getByText("bge-reranker-v2-m3")).toBeInTheDocument()
    // Critically: Quenchforge UX does NOT render the Ollama "Installed Models"
    // section or per-model "Pull" buttons (F-04-03 mislabel fix).
    expect(screen.queryByText("Installed Models")).not.toBeInTheDocument()
    expect(screen.queryByText("Pull")).not.toBeInTheDocument()
  })

  it("does NOT render the Ollama CPU-only warning even when GPU is AMD", () => {
    // F-04-02: AMD Radeon Pro Vega II + Quenchforge should NOT trigger
    // "CPU-only detected" — Quenchforge IS the GPU acceleration path on AMD-Mac.
    render(
      <LocalLLMStep
        inferenceBackend="quenchforge"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        hardwareGpu="AMD Radeon Pro Vega II"
        hardwareGpuAcceleration="metal"
      />,
    )
    expect(screen.queryByText(/CPU-only detected/i)).not.toBeInTheDocument()
  })

  it("shows GPU acceleration indicator when GPU is accelerated", () => {
    render(
      <LocalLLMStep
        inferenceBackend="quenchforge"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        hardwareGpu="AMD Radeon Pro Vega II"
        hardwareGpuAcceleration="metal"
      />,
    )
    expect(screen.getByText(/GPU acceleration available/)).toBeInTheDocument()
  })
})

// ---- Cloud backend ----

describe("LocalLLMStep — Cloud backend", () => {
  it("renders skip-style message and no setup UI", () => {
    render(
      <LocalLLMStep
        inferenceBackend="cloud"
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    expect(screen.getByText(/Not required for cloud setup/i)).toBeInTheDocument()
    // No ollama install instructions
    expect(screen.queryByText("All platforms")).not.toBeInTheDocument()
    // No quenchforge slot list
    expect(screen.queryByText("Default Quenchforge slots")).not.toBeInTheDocument()
  })
})
