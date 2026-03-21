// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  fetchPlugins: vi.fn(),
  enablePlugin: vi.fn(),
  disablePlugin: vi.fn(),
  getPluginConfig: vi.fn(),
  updatePluginConfig: vi.fn(),
  scanPlugins: vi.fn(),
}))

import { fetchPlugins } from "@/lib/api"
import { PluginsSection } from "@/components/settings/plugins-section"

const mockPlugins = [
  {
    name: "ocr-plugin",
    description: "OCR text extraction from images",
    version: "0.1.0",
    status: "active" as const,
    enabled: true,
    tier_required: "community",
    capabilities: ["parser"],
    file_types: [".png", ".jpg"],
  },
  {
    name: "audio-transcribe",
    description: "Audio transcription via Whisper",
    version: "0.2.0",
    status: "disabled" as const,
    enabled: false,
    tier_required: "pro",
    capabilities: ["parser"],
    file_types: [".mp3", ".wav"],
  },
]

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("PluginsSection", () => {
  it("renders plugin cards after loading", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins })
    render(<PluginsSection />)
    expect(await screen.findByText("ocr-plugin")).toBeInTheDocument()
    expect(screen.getByText("audio-transcribe")).toBeInTheDocument()
  })

  it("shows plugin count badge", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins })
    render(<PluginsSection />)
    await screen.findByText("ocr-plugin")
    expect(screen.getByText("2")).toBeInTheDocument()
  })

  it("shows Scan for Plugins button", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins })
    render(<PluginsSection />)
    await screen.findByText("ocr-plugin")
    expect(screen.getByText("Scan for Plugins")).toBeInTheDocument()
  })

  it("shows empty state when no plugins", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: [] })
    render(<PluginsSection />)
    await waitFor(() => {
      expect(screen.getByText("No plugins installed")).toBeInTheDocument()
    })
  })
})
