// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { ModeSelectionStep } from "@/components/setup/mode-selection-step"

const SUMMARY = {
  providerCount: 0,
  providerNames: [],
  domainCount: 4,
  ollamaEnabled: true,
  ollamaModel: "gemma-4-26b-a4b",
  documentCount: 0,
  inferenceBackend: "ollama" as const,
}

describe("ModeSelectionStep — GPU line", () => {
  it("reports the GPU without promising what it will speed up", () => {
    render(
      <ModeSelectionStep
        selectedMode="simple"
        onSelectMode={vi.fn()}
        configSummary={SUMMARY}
        hardware={{ ram_gb: 128, cpu: "Apple M4 Max", gpu: "Apple M4 Max", gpu_acceleration: "metal" }}
      />,
    )
    expect(screen.getByText(/GPU detected \(metal\)/)).toBeInTheDocument()
    expect(screen.queryByText(/reranking will be faster/)).not.toBeInTheDocument()
    expect(screen.queryByText(/will be faster/)).not.toBeInTheDocument()
  })
})
