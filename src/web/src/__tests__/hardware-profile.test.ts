// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Tests for the client-side hardware-profile helpers. The recommendation
 * truth table mirrors:
 *   - scripts/detect-gpu.sh (authoritative)
 *   - src/mcp/app/routers/setup.py:_recommend_backend_from_hw (Python fallback)
 * so all three layers stay in lockstep.
 */
import { describe, it, expect } from "vitest"
import {
  backendOptionsForHardware,
  backendSummary,
  deriveRecommendation,
} from "@/lib/hardware-profile"
import type { SystemCheckResponse } from "@/lib/types"

function sys(overrides: Partial<SystemCheckResponse> = {}): SystemCheckResponse {
  return {
    ram_gb: 32,
    docker_running: true,
    env_exists: true,
    env_keys_present: [],
    ollama_detected: false,
    ollama_url: null,
    ollama_models: [],
    lightweight_recommended: false,
    archive_path_exists: true,
    default_archive_path: "~/cerid-archive",
    os: "macOS 14.5",
    cpu: "Intel Xeon W",
    cpu_cores: 8,
    gpu: "AMD Radeon Pro Vega II",
    gpu_acceleration: "metal",
    ...overrides,
  }
}

describe("deriveRecommendation", () => {
  it("returns 'cloud' when no system check is available", () => {
    expect(deriveRecommendation(null)).toBe("cloud")
  })

  it("respects gpu_type=amd-mac as the authoritative signal", () => {
    expect(
      deriveRecommendation(sys({ gpu_type: "amd-mac" })),
    ).toBe("quenchforge")
  })

  it("recommends quenchforge for Intel Mac + AMD discrete (string heuristic)", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "macOS 14.5",
          gpu: "AMD Radeon Pro Vega II Duo",
          gpu_type: "",
        }),
      ),
    ).toBe("quenchforge")
  })

  it("recommends ollama for Apple Silicon (Metal)", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "macOS 14.5",
          cpu: "Apple M2 Max",
          gpu: "Apple M2 Max",
          gpu_acceleration: "metal",
          gpu_type: "metal",
        }),
      ),
    ).toBe("ollama")
  })

  it("recommends ollama for Linux + CUDA", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "Linux 6.6",
          gpu: "NVIDIA RTX 4090",
          gpu_acceleration: "cuda",
          gpu_type: "nvidia",
        }),
      ),
    ).toBe("ollama")
  })

  it("recommends ollama for Linux + ROCm (AMD discrete on Linux)", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "Linux 6.6",
          gpu: "AMD Radeon RX 7900 XTX",
          gpu_acceleration: "rocm",
          gpu_type: "amd",
        }),
      ),
    ).toBe("ollama")
  })

  it("does not misclassify Intel Mac iGPU as amd-mac", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "macOS 13.6",
          gpu: "Intel Iris Plus Graphics",
          gpu_acceleration: "metal",
          gpu_type: "metal",
        }),
      ),
    ).toBe("ollama")
  })

  it("recommends cloud when no accelerator is detected and ollama isn't running", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "Linux 6.6",
          gpu: "",
          gpu_acceleration: "",
          gpu_type: "cpu",
          ollama_detected: false,
        }),
      ),
    ).toBe("cloud")
  })

  it("prefers ollama over cloud when ollama is detected even without GPU accel", () => {
    expect(
      deriveRecommendation(
        sys({
          os: "Linux 6.6",
          gpu: "",
          gpu_acceleration: "",
          gpu_type: "cpu",
          ollama_detected: true,
        }),
      ),
    ).toBe("ollama")
  })
})

describe("backendOptionsForHardware", () => {
  it("returns three options in a stable order regardless of recommendation", () => {
    const { options } = backendOptionsForHardware(sys({ gpu_type: "amd-mac" }))
    expect(options.map((o) => o.id)).toEqual(["ollama", "quenchforge", "cloud"])
  })

  it("badges the recommended option", () => {
    const { options } = backendOptionsForHardware(sys({ gpu_type: "amd-mac" }))
    const quench = options.find((o) => o.id === "quenchforge")!
    expect(quench.badge).toBe("Recommended")
  })

  it("badges ollama as 'Detected' when it's already running and not the recommendation", () => {
    const { options } = backendOptionsForHardware(
      sys({
        gpu_type: "amd-mac",
        ollama_detected: true,
      }),
    )
    const ollama = options.find((o) => o.id === "ollama")!
    expect(ollama.badge).toBe("Detected")
  })

  it("uses the server recommendation when present", () => {
    const { defaultId } = backendOptionsForHardware(
      sys({ recommended_local_backend: "cloud", gpu_type: "amd-mac" }),
    )
    // Server-side recommendation wins even when the client heuristic would
    // pick quenchforge — the server has the authoritative view.
    expect(defaultId).toBe("cloud")
  })

  it("falls back to client heuristic when server omits the recommendation", () => {
    const { defaultId } = backendOptionsForHardware(sys({ gpu_type: "amd-mac" }))
    expect(defaultId).toBe("quenchforge")
  })
})

describe("backendSummary", () => {
  it("returns local tone for quenchforge", () => {
    expect(backendSummary("quenchforge")).toEqual({ label: "Quenchforge", tone: "local" })
  })

  it("returns local tone for ollama", () => {
    expect(backendSummary("ollama")).toEqual({ label: "Ollama", tone: "local" })
  })

  it("returns cloud tone for cloud", () => {
    expect(backendSummary("cloud")).toEqual({ label: "Cloud", tone: "cloud" })
  })
})
