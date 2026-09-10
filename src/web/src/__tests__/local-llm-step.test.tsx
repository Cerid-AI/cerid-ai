// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"

const fetchOllamaRecommendations = vi.fn().mockResolvedValue({ hardware: null, models: [] })

vi.mock("@/lib/api", () => ({
  pullOllamaModel: vi.fn().mockResolvedValue(new Response()),
  fetchOllamaRecommendations: (...args: unknown[]) => fetchOllamaRecommendations(...args),
}))

import { LocalLLMStep } from "@/components/setup/local-llm-step"
import type { LocalThroughput } from "@/lib/types"

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

const MEASURED_THROUGHPUT: LocalThroughput = {
  prompt_tok_s: 120.5,
  gen_tok_s: 42.3,
  probe_at: 1735689600,
  expectations: {
    memory_extract: { seconds: 45.7, basis: "measured" },
    entity_extraction: { seconds: 12.3, basis: "measured" },
    wiki_summary: { seconds: 30.1, basis: "measured" },
    claim_extraction: { seconds: 20.0, basis: "measured" },
    topic_extraction: { seconds: 15.0, basis: "measured" },
    chat_turn_tail_s: 58.0,
  },
}

const UNMEASURED_THROUGHPUT: LocalThroughput = {
  prompt_tok_s: null,
  gen_tok_s: null,
  probe_at: null,
  expectations: {
    memory_extract: { basis: "unmeasured" },
    entity_extraction: { basis: "unmeasured" },
    wiki_summary: { basis: "unmeasured" },
    claim_extraction: { basis: "unmeasured" },
    topic_extraction: { basis: "unmeasured" },
    chat_turn_tail_s: null,
  },
}

const ONE_RATE_THROUGHPUT: LocalThroughput = {
  prompt_tok_s: null,
  gen_tok_s: 9.5,
  probe_at: 1735689600,
  expectations: {
    memory_extract: { basis: "unmeasured" },
    entity_extraction: { basis: "unmeasured" },
    wiki_summary: { basis: "unmeasured" },
    claim_extraction: { basis: "unmeasured" },
    topic_extraction: { basis: "unmeasured" },
    chat_turn_tail_s: null,
  },
}

const UNACCELERATED_HARDWARE = { ram_gb: 16, cpu: "Intel i7-9700", gpu: "Intel UHD 630" }
const ACCELERATED_HARDWARE = { ram_gb: 32, cpu: "Intel Core i9", gpu: "AMD Radeon Pro Vega II" }

beforeEach(() => {
  onChange.mockClear()
  fetchOllamaRecommendations.mockReset()
  fetchOllamaRecommendations.mockResolvedValue({ hardware: null, models: [] })
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

  it("marks a recommended colon-tag model as Installed when the dash-alias is present (SW2)", async () => {
    // Bug SW2: recommendation ids use Ollama colon tags (`llama3.2:3b`)
    // while local Quenchforge aliases use dashes (`llama3.2-3b`). The
    // cross-match must normalize `:`<->`-` so the installed recommended
    // model shows its "Installed" badge instead of an orphaned "Pull".
    fetchOllamaRecommendations.mockResolvedValue({
      hardware: null,
      models: [
        {
          id: "llama3.2:3b",
          name: "Llama 3.2 3B",
          origin: "Meta",
          size_gb: 2.0,
          description: "Balanced pipeline model",
          strengths: "speed",
          compatible: true,
          recommended: true,
        },
      ],
    })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={["llama3.2-3b"]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    await waitFor(() => expect(screen.getByText("Llama 3.2 3B")).toBeInTheDocument())
    expect(screen.getByText("Installed")).toBeInTheDocument()
    // And no Pull button for an already-installed model.
    expect(screen.queryByText("Pull")).not.toBeInTheDocument()
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

// ---- Measured local-model expectations (Task 3) ----

describe("LocalLLMStep — measured local-model expectations", () => {
  it("renders the measured line with the fixture numbers instead of the CPU-only sentence", async () => {
    fetchOllamaRecommendations.mockResolvedValue({ hardware: UNACCELERATED_HARDWARE, models: [] })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        localThroughput={MEASURED_THROUGHPUT}
        suggestedProfile="hybrid"
      />,
    )
    await waitFor(() => expect(screen.getByText(/Your Hardware/i)).toBeInTheDocument())
    expect(screen.getByText(/42 tok\/s/)).toBeInTheDocument()
    expect(screen.getByText(/12s per document/)).toBeInTheDocument()
    expect(screen.getByText(/45\.7s per chat turn/)).toBeInTheDocument()
    expect(screen.getByText(/hybrid/)).toBeInTheDocument()
    expect(screen.getByText(/interactive on cloud, background on a small local model/)).toBeInTheDocument()
    expect(screen.queryByText(/CPU-only detected/i)).not.toBeInTheDocument()
  })

  it("renders the measured line even when hardware reports GPU acceleration (Fix round 1)", async () => {
    // Regression test: showCpuOnlyWarning is false here (GPU string looks
    // accelerated), but quenchforge measured the model running on CPU on
    // this exact host class — the line must not be gated on that heuristic.
    fetchOllamaRecommendations.mockResolvedValue({ hardware: ACCELERATED_HARDWARE, models: [] })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        localThroughput={MEASURED_THROUGHPUT}
        suggestedProfile="hybrid"
      />,
    )
    await waitFor(() => expect(screen.getByText(/Your Hardware/i)).toBeInTheDocument())
    expect(screen.getByText(/42 tok\/s/)).toBeInTheDocument()
    expect(screen.queryByText(/CPU-only detected/i)).not.toBeInTheDocument()
  })

  it("keeps the existing CPU-only sentence when local_throughput is null", async () => {
    fetchOllamaRecommendations.mockResolvedValue({ hardware: UNACCELERATED_HARDWARE, models: [] })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        localThroughput={null}
      />,
    )
    await waitFor(() => expect(screen.getByText(/Your Hardware/i)).toBeInTheDocument())
    expect(
      screen.getByText(
        /CPU-only detected — inference will be slower\. GPU acceleration available with Apple Silicon, NVIDIA, or AMD via Quenchforge\./,
      ),
    ).toBeInTheDocument()
  })

  it("keeps the existing CPU-only sentence when local_throughput is unmeasured", async () => {
    fetchOllamaRecommendations.mockResolvedValue({ hardware: UNACCELERATED_HARDWARE, models: [] })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        localThroughput={UNMEASURED_THROUGHPUT}
      />,
    )
    await waitFor(() => expect(screen.getByText(/Your Hardware/i)).toBeInTheDocument())
    expect(screen.getByText(/CPU-only detected/i)).toBeInTheDocument()
  })

  it("keeps the existing CPU-only sentence when only one rate was measured", async () => {
    fetchOllamaRecommendations.mockResolvedValue({ hardware: UNACCELERATED_HARDWARE, models: [] })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
        localThroughput={ONE_RATE_THROUGHPUT}
      />,
    )
    await waitFor(() => expect(screen.getByText(/Your Hardware/i)).toBeInTheDocument())
    expect(screen.getByText(/CPU-only detected/i)).toBeInTheDocument()
    expect(screen.queryByText(/9\.5 tok\/s/)).not.toBeInTheDocument()
  })
})

// ---- Hardware-aware model recommendation (Task B6) ----

describe("LocalLLMStep — hardware-aware model recommendation", () => {
  it("recommends llama3.1:8b with a reason on amd-mac hardware", async () => {
    fetchOllamaRecommendations.mockResolvedValue({
      hardware: { ram_gb: 64, cpu: "Xeon W-3245", gpu: "AMD Radeon Pro Vega II", gpu_type: "amd-mac" },
      models: [],
    })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    await waitFor(() => expect(screen.getByText("llama3.1:8b")).toBeInTheDocument())
    expect(screen.queryByText("llama3.2:3b")).not.toBeInTheDocument()
    expect(screen.getByText(/GGML_ASSERT/)).toBeInTheDocument()
  })

  it("keeps llama3.2:3b with no extra note on metal hardware", async () => {
    fetchOllamaRecommendations.mockResolvedValue({
      hardware: { ram_gb: 32, cpu: "Apple M3", gpu: "Apple M3", gpu_type: "metal" },
      models: [],
    })
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    await waitFor(() => expect(screen.getByText("llama3.2:3b")).toBeInTheDocument())
    expect(screen.queryByText("llama3.1:8b")).not.toBeInTheDocument()
    expect(screen.queryByText(/GGML_ASSERT/)).not.toBeInTheDocument()
  })

  it("shows a not-detected sentence instead of silently omitting hardware info when the fetch fails", async () => {
    fetchOllamaRecommendations.mockRejectedValue(new Error("network error"))
    render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={[]}
        state={{ ...DEFAULT_STATE, detected: true }}
        onChange={onChange}
      />,
    )
    await waitFor(() =>
      expect(
        screen.getByText("Hardware not detected — start the server to see recommendations"),
      ).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Your Hardware/i)).not.toBeInTheDocument()
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

// ---- axe-clean, one state per backend ----

describe("LocalLLMStep — axe-clean", () => {
  it("is axe-clean: Ollama backend, not detected", async () => {
    const { container } = render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean: Ollama backend, detected with installed models", async () => {
    const { container } = render(
      <LocalLLMStep
        inferenceBackend="ollama"
        ollamaDetected={true}
        ollamaModels={["llama3.2:3b", "mistral:7b"]}
        state={{ ...DEFAULT_STATE, detected: true, enabled: true, model: "llama3.2:3b" }}
        onChange={onChange}
      />,
    )
    await waitFor(() => expect(fetchOllamaRecommendations).toHaveBeenCalled())
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean: Quenchforge backend", async () => {
    const { container } = render(
      <LocalLLMStep
        inferenceBackend="quenchforge"
        ollamaDetected={true}
        ollamaModels={["bge-reranker-v2-m3"]}
        state={{ ...DEFAULT_STATE, detected: true, enabled: true }}
        onChange={onChange}
        hardwareGpu="AMD Radeon Pro Vega II"
        hardwareGpuAcceleration="metal"
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean: Cloud backend", async () => {
    const { container } = render(
      <LocalLLMStep
        inferenceBackend="cloud"
        ollamaDetected={false}
        ollamaModels={[]}
        state={DEFAULT_STATE}
        onChange={onChange}
      />,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
