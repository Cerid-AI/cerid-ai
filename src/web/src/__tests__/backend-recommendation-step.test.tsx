// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { BackendRecommendationStep } from "@/components/setup/backend-recommendation-step"
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

const onSelect = vi.fn<(b: "ollama" | "quenchforge" | "cloud") => void>()

beforeEach(() => {
  onSelect.mockClear()
})

describe("BackendRecommendationStep", () => {
  it("renders all three backend options", () => {
    render(
      <BackendRecommendationStep
        systemCheck={sys({ gpu_type: "amd-mac" })}
        selected={null}
        onSelect={onSelect}
      />,
    )
    expect(screen.getByText(/Ollama \(Local\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Quenchforge \(Local, Mac \+ AMD\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Cloud \(OpenRouter\)/i)).toBeInTheDocument()
  })

  it("badges quenchforge as Recommended on Intel Mac + AMD", () => {
    render(
      <BackendRecommendationStep
        systemCheck={sys({ gpu_type: "amd-mac" })}
        selected={null}
        onSelect={onSelect}
      />,
    )
    // The recommended option is also marked aria-pressed when selected matches it
    const quenchforgeBtn = screen.getByText(/Quenchforge/i).closest("button")
    expect(quenchforgeBtn).not.toBeNull()
    expect(quenchforgeBtn?.getAttribute("aria-pressed")).toBe("true")
  })

  it("calls onSelect with the clicked backend id", () => {
    render(
      <BackendRecommendationStep
        systemCheck={sys({ gpu_type: "amd-mac" })}
        selected={null}
        onSelect={onSelect}
      />,
    )
    fireEvent.click(screen.getByText(/Cloud \(OpenRouter\)/i).closest("button")!)
    expect(onSelect).toHaveBeenCalledWith("cloud")
  })

  it("reflects user override when 'selected' differs from recommendation", () => {
    render(
      <BackendRecommendationStep
        systemCheck={sys({ gpu_type: "amd-mac" })}
        selected="cloud"
        onSelect={onSelect}
      />,
    )
    const cloudBtn = screen.getByText(/Cloud \(OpenRouter\)/i).closest("button")
    const quenchforgeBtn = screen.getByText(/Quenchforge/i).closest("button")
    expect(cloudBtn?.getAttribute("aria-pressed")).toBe("true")
    expect(quenchforgeBtn?.getAttribute("aria-pressed")).toBe("false")
  })

  it("renders the detected hardware summary", () => {
    render(
      <BackendRecommendationStep
        systemCheck={sys({
          os: "macOS 14.5",
          cpu: "Intel Xeon W",
          gpu: "AMD Radeon Pro Vega II",
          gpu_type: "amd-mac",
        })}
        selected={null}
        onSelect={onSelect}
      />,
    )
    expect(screen.getByText(/Detected:/i)).toBeInTheDocument()
    expect(screen.getByText(/macOS 14\.5/)).toBeInTheDocument()
  })

  it("handles null systemCheck without crashing", () => {
    render(
      <BackendRecommendationStep
        systemCheck={null}
        selected={null}
        onSelect={onSelect}
      />,
    )
    // With no system check, cloud is the default. All three options render.
    expect(screen.getByText(/Ollama \(Local\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Quenchforge \(Local, Mac \+ AMD\)/i)).toBeInTheDocument()
    expect(screen.getByText(/Cloud \(OpenRouter\)/i)).toBeInTheDocument()
  })
})
