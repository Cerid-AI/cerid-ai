// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Pulling a model is something Ollama does. A server that answers on the same
 * port under another name is not offered Ollama's catalogue, its pulls or its
 * speed estimates.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"

const CATALOGUE = {
  hardware: { ram_gb: 128, cpu: "Apple M4 Max", gpu: "Apple M4 Max", gpu_type: "metal" },
  models: [
    {
      id: "llama3.3:70b",
      name: "Llama 3.3 70B",
      origin: "meta",
      size_gb: 40,
      description: "Flagship model for maximum quality",
      strengths: "Near-frontier reasoning and knowledge",
      compatible: true,
      recommended: true,
      expected_tokens_per_sec: 9.1,
    },
  ],
}

const fetchOllamaRecommendations = vi.fn()

vi.mock("@/lib/api", () => ({
  pullOllamaModel: vi.fn().mockResolvedValue(new Response()),
  fetchOllamaRecommendations: (...args: unknown[]) => fetchOllamaRecommendations(...args),
}))

import { LocalLLMStep } from "@/components/setup/local-llm-step"

const STATE: { detected: boolean; enabled: boolean; model: string | null; pulling: boolean } = {
  detected: true,
  enabled: true,
  model: "gemma-4-26b-a4b",
  pulling: false,
}
const SERVED = ["qwen3.5-4b-instruct", "gemma-4-26b-a4b"]

const onChange = vi.fn()

function renderStep(localServerName?: string, cloudChat = false, state = STATE) {
  return render(
    <LocalLLMStep
      cloudChat={cloudChat}
      inferenceBackend="ollama"
      ollamaDetected
      ollamaModels={SERVED}
      state={state}
      onChange={onChange}
      hardwareGpuAcceleration="metal"
      localServerName={localServerName}
    />,
  )
}

beforeEach(() => {
  onChange.mockClear()
  fetchOllamaRecommendations.mockReset()
  fetchOllamaRecommendations.mockResolvedValue(CATALOGUE)
})

describe("LocalLLMStep on a server that is not Ollama", () => {
  it("is headed with the server's name", () => {
    renderStep("MLX server")
    expect(screen.getByText("Local LLM (MLX server)")).toBeInTheDocument()
    expect(screen.queryByText(/Ollama/)).not.toBeInTheDocument()
  })

  it("offers no pulls and no catalogue speed estimates", async () => {
    renderStep("MLX server")
    await waitFor(() => expect(screen.getByText("Your Hardware")).toBeInTheDocument())
    expect(screen.queryByRole("button", { name: /Pull/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Llama 3\.3 70B/)).not.toBeInTheDocument()
    expect(screen.queryByText(/9\.1 tok\/s/)).not.toBeInTheDocument()
  })

  it("lists what the server serves", () => {
    renderStep("MLX server")
    expect(screen.getByText("qwen3.5-4b-instruct")).toBeInTheDocument()
    expect(screen.getByText("gemma-4-26b-a4b")).toBeInTheDocument()
  })

  it("offers no pull on a server that does not say what it is", async () => {
    renderStep("Local model server")
    await waitFor(() => expect(screen.getByText("Your Hardware")).toBeInTheDocument())
    expect(screen.getByText("Local LLM (Local model server)")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Pull/ })).not.toBeInTheDocument()
  })
})

describe("LocalLLMStep on Ollama", () => {
  it("still offers the catalogue and its pulls", async () => {
    renderStep("Ollama")
    expect(screen.getByText("Local LLM (Ollama)")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText("Llama 3.3 70B")).toBeInTheDocument())
    expect(screen.getByRole("button", { name: /Pull/ })).toBeInTheDocument()
  })
})

describe("LocalLLMStep and where chat runs", () => {
  it("does not say chat uses OpenRouter on an instance without it", () => {
    renderStep("MLX server")
    expect(screen.queryByText(/OpenRouter/)).not.toBeInTheDocument()
  })

  it("says so when OpenRouter is configured", () => {
    renderStep("MLX server", true)
    expect(screen.getByText(/your main chat still uses OpenRouter/)).toBeInTheDocument()
  })
})

describe("LocalLLMStep and which model is used", () => {
  it("lets the user choose among the models the server serves", () => {
    renderStep("MLX server")
    expect(screen.getByRole("button", { name: "gemma-4-26b-a4b" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(screen.getByRole("button", { name: "qwen3.5-4b-instruct" }))
    expect(onChange).toHaveBeenCalledWith({ ...STATE, model: "qwen3.5-4b-instruct" })
  })

  it("asks for a choice when no model is named, and marks none as chosen", () => {
    renderStep("MLX server", false, { ...STATE, model: null })
    expect(screen.getByText(/Choose the model Cerid should use/)).toBeInTheDocument()
    for (const name of SERVED) {
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "false")
    }
  })
})

describe("LocalLLMStep with the Quenchforge backend chosen", () => {
  function renderQuenchforge(localServerName: string) {
    return render(
      <LocalLLMStep
        inferenceBackend="quenchforge"
        ollamaDetected
        ollamaModels={SERVED}
        state={STATE}
        onChange={onChange}
        localServerName={localServerName}
      />,
    )
  }

  it("does not call another server Quenchforge", () => {
    renderQuenchforge("MLX server")
    expect(screen.getByText("Local LLM (MLX server)")).toBeInTheDocument()
    expect(screen.queryByText("Quenchforge connected")).not.toBeInTheDocument()
    expect(screen.queryByText("Default Quenchforge slots")).not.toBeInTheDocument()
  })

  it("says Quenchforge is connected when Quenchforge answers", () => {
    renderQuenchforge("Quenchforge")
    expect(screen.getByText("Quenchforge connected")).toBeInTheDocument()
  })
})
