// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

vi.mock("@/lib/api", () => ({
  fetchPlugins: vi.fn(),
  enablePlugin: vi.fn(),
  disablePlugin: vi.fn(),
  getPluginConfig: vi.fn(),
  updatePluginConfig: vi.fn(),
  scanPlugins: vi.fn(),
}))

import {
  fetchPlugins,
  enablePlugin,
  disablePlugin,
  getPluginConfig,
  scanPlugins,
} from "@/lib/api"
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
    config_schema: null,
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
    config_schema: null,
  },
]

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("PluginsSection", () => {
  it("renders plugin cards after loading", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    render(<PluginsSection />)
    expect(await screen.findByText("ocr-plugin")).toBeInTheDocument()
    expect(screen.getByText("audio-transcribe")).toBeInTheDocument()
  })

  it("shows plugin count badge", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    render(<PluginsSection />)
    await screen.findByText("ocr-plugin")
    expect(screen.getByText("2")).toBeInTheDocument()
  })

  it("shows Scan for Plugins button", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    render(<PluginsSection />)
    await screen.findByText("ocr-plugin")
    expect(screen.getByText("Scan for Plugins")).toBeInTheDocument()
  })

  it("shows empty state when no plugins", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: [], total: 0 })
    render(<PluginsSection />)
    await waitFor(() => {
      expect(screen.getByText("No plugins installed")).toBeInTheDocument()
    })
  })

  it("calls disablePlugin when toggling an enabled plugin off", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    vi.mocked(disablePlugin).mockResolvedValue({ ...mockPlugins[0], enabled: false, status: "disabled" })
    const user = userEvent.setup()
    render(<PluginsSection />)
    await screen.findByText("ocr-plugin")

    // The enabled plugin's switch is role="switch" checked=true; toggle it.
    const switches = screen.getAllByRole("switch")
    const ocrSwitch = switches[0]
    expect(ocrSwitch).toHaveAttribute("data-state", "checked")
    await user.click(ocrSwitch)

    await waitFor(() => {
      expect(disablePlugin).toHaveBeenCalledWith("ocr-plugin")
    })
  })

  it("calls enablePlugin when toggling a disabled plugin on", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    vi.mocked(enablePlugin).mockResolvedValue({ ...mockPlugins[1], enabled: true, status: "active" })
    const user = userEvent.setup()
    render(<PluginsSection />)
    await screen.findByText("audio-transcribe")

    // Pro-tier plugin: the UI disables the switch for requires_pro status
    // specifically. In this fixture status=disabled + tier_required=pro,
    // which the card renders as interactable (toggle attempt allowed).
    // Target the 2nd switch (audio-transcribe).
    const switches = screen.getAllByRole("switch")
    await user.click(switches[1])

    await waitFor(() => {
      expect(enablePlugin).toHaveBeenCalledWith("audio-transcribe")
    })
  })

  it("loads config lazily when a plugin card is expanded", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    vi.mocked(getPluginConfig).mockResolvedValue({ values: { api_key: "xxx", verbose: true } })  // pragma: allowlist secret
    const user = userEvent.setup()
    render(<PluginsSection />)
    await screen.findByText("ocr-plugin")

    // Expand by clicking the header area. The config fetch is lazy —
    // must NOT fire on initial render, only on expand.
    expect(getPluginConfig).not.toHaveBeenCalled()
    await user.click(screen.getByText("ocr-plugin"))

    await waitFor(() => {
      expect(getPluginConfig).toHaveBeenCalledWith("ocr-plugin")
    })
  })

  it("re-invokes scanPlugins when the Scan button is clicked", async () => {
    vi.mocked(fetchPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    vi.mocked(scanPlugins).mockResolvedValue({ plugins: mockPlugins, total: mockPlugins.length })
    const user = userEvent.setup()
    render(<PluginsSection />)
    await screen.findByText("Scan for Plugins")

    expect(scanPlugins).not.toHaveBeenCalled()
    await user.click(screen.getByText("Scan for Plugins"))

    await waitFor(() => {
      expect(scanPlugins).toHaveBeenCalled()
    })
  })
})
