// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { axe } from "jest-axe"
import type { SystemCheckResponse } from "@/lib/types"

const mockFetchSystemCheck = vi.fn<() => Promise<SystemCheckResponse>>()

vi.mock("@/lib/api", () => ({
  fetchSystemCheck: (...args: unknown[]) => mockFetchSystemCheck(...(args as [])),
}))

import { SystemCheckCard } from "@/components/setup/system-check-card"

const HEALTHY_RESULT: SystemCheckResponse = {
  ram_gb: 16,
  os: "macOS 26.4",
  cpu: "Apple M2 Max",
  cpu_cores: 12,
  gpu: "Apple M2 Max",
  gpu_acceleration: "metal",
  docker_running: true,
  env_exists: true,
  env_keys_present: ["OPENROUTER_API_KEY"],
  ollama_detected: true,
  ollama_url: "http://localhost:11434",
  ollama_models: ["llama3.2:3b"],
  lightweight_recommended: false,
  archive_path_exists: true,
  default_archive_path: "~/cerid-archive",
}

beforeEach(() => {
  vi.restoreAllMocks()
  mockFetchSystemCheck.mockResolvedValue(HEALTHY_RESULT)
})

describe("SystemCheckCard", () => {
  it("shows System Check heading", () => {
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(screen.getByText("System Check")).toBeInTheDocument()
  })

  it("shows all 4 check items", () => {
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(screen.getByText("System Memory")).toBeInTheDocument()
    expect(screen.getByText("Docker")).toBeInTheDocument()
    expect(screen.getByText("Configuration")).toBeInTheDocument()
    expect(screen.getByText("Ollama")).toBeInTheDocument()
  })

  it("shows 'Detecting...' while loading", () => {
    // Make the fetch never resolve during this test
    mockFetchSystemCheck.mockReturnValue(new Promise(() => {}))
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    const detectingElements = screen.getAllByText("Detecting...")
    expect(detectingElements).toHaveLength(4)
  })

  it("calls onCheckComplete with result after fetch resolves", async () => {
    const onCheckComplete = vi.fn()
    render(<SystemCheckCard onCheckComplete={onCheckComplete} />)
    await waitFor(() => {
      expect(onCheckComplete).toHaveBeenCalledWith(HEALTHY_RESULT)
    })
  })

  it("shows resolved check details after fetch succeeds", async () => {
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/16 GB/)).toBeInTheDocument()
    })
    expect(screen.getByText("Running")).toBeInTheDocument()
    expect(screen.getByText(/1 key configured/)).toBeInTheDocument()
    expect(screen.getByText(/Detected \(1 model\)/)).toBeInTheDocument()
  })

  it("shows error message when fetch fails", async () => {
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText("Could not reach backend — is Docker running?")).toBeInTheDocument()
    })
  })
})

describe("SystemCheckCard — desktop bridge (RA-01 / RA-02)", () => {
  afterEach(() => {
    delete (window as unknown as { cerid?: object }).cerid
  })

  it("fills the RAM row from window.cerid.system.requirements when the REST poll fails", async () => {
    // Fresh Mac, no Docker: the backend is unreachable, but the desktop
    // bridge knows the machine's RAM — the row must not sit on "Unknown".
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    ;(window as unknown as { cerid: object }).cerid = {
      system: {
        requirements: vi.fn().mockResolvedValue({
          ram_gb: 32,
          disk_free_gb: 400,
          ram_sufficient: true,
          disk_sufficient: true,
        }),
      },
    }
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(await screen.findByText(/32 GB — recommended config/)).toBeInTheDocument()
  })

  it("uses the platform-correct Docker Desktop URL from the bridge for the download link", async () => {
    mockFetchSystemCheck.mockResolvedValue({ ...HEALTHY_RESULT, docker_running: false })
    const bridgeUrl = "https://desktop.docker.com/mac/main/arm64/Docker.dmg"
    ;(window as unknown as { cerid: object }).cerid = {
      docker: { downloadUrl: vi.fn().mockResolvedValue(bridgeUrl) },
    }
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    const link = await screen.findByRole("link", { name: /download docker desktop/i })
    await waitFor(() => expect(link).toHaveAttribute("href", bridgeUrl))
  })
})

describe("SystemCheckCard — axe-clean", () => {
  it("is axe-clean while loading", async () => {
    mockFetchSystemCheck.mockReturnValue(new Promise(() => {}))
    const { container } = render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean after a successful check", async () => {
    const { container } = render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/16 GB/)).toBeInTheDocument())
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean in the error/retrying state", async () => {
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    const { container } = render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText("Could not reach backend — is Docker running?")).toBeInTheDocument()
    })
    expect(await axe(container)).toHaveNoViolations()
  })
})
