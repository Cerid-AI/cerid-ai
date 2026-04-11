// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn().mockResolvedValue({
    configured: false,
    setup_required: true,
    missing_keys: ["OPENROUTER_API_KEY"],
    optional_keys: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"],
  }),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: {} }),
  fetchProviderCredits: vi.fn().mockResolvedValue({ configured: false, balance: null }),
  fetchSystemCheck: vi.fn().mockResolvedValue({
    ram_gb: 16,
    docker_running: true,
    env_exists: true,
    env_keys_present: [],
    ollama_detected: false,
    ollama_url: null,
    ollama_models: [],
    lightweight_recommended: false,
    archive_path_exists: false,
    default_archive_path: "~/cerid-archive",
  }),
  uploadFile: vi.fn(),
  queryKB: vi.fn(),
  pullOllamaModel: vi.fn(),
}))

vi.mock("@/contexts/ui-mode-context", () => ({
  useUIMode: () => ({ mode: "simple", setMode: vi.fn() }),
}))

vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({ isDragOver: false, dragHandlers: {} }),
}))

import { SetupWizard } from "@/components/setup/setup-wizard"

const noop = () => {}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("SetupWizard", () => {
  it("renders step 0 welcome content", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/Welcome to Cerid/i)).toBeInTheDocument()
  })

  it("renders Get Started button on step 0", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText("Get Started")).toBeInTheDocument()
  })

  it("mentions RAG and verification in the product description", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText(/RAG-powered retrieval/)).toBeInTheDocument()
  })

  it("renders dialog title for accessibility", () => {
    render(<SetupWizard open={true} onComplete={noop} />)
    expect(screen.getByText("Cerid AI Setup")).toBeInTheDocument()
  })
})
