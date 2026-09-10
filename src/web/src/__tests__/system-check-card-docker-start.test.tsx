// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * B5: inside the desktop app the wizard can start Docker Desktop and the
 * Cerid stack itself instead of only telling the user to do it by hand.
 * The plain browser build has no bridge and keeps the manual instructions.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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

/** Verbatim text main/docker.ts returns when it cannot find docker-compose.yml. */
const REPO_ROOT_ERROR =
  "Could not locate docker-compose.yml. Set CERID_REPO_ROOT to your cerid-ai checkout."

function installBridge(docker: Record<string, unknown>) {
  ;(window as unknown as { cerid: object }).cerid = { docker }
}

beforeEach(() => {
  vi.restoreAllMocks()
  mockFetchSystemCheck.mockResolvedValue(HEALTHY_RESULT)
})

afterEach(() => {
  delete (window as unknown as { cerid?: object }).cerid
})

describe("SystemCheckCard — starting Docker from the wizard", () => {
  it("offers to start Docker Desktop when the bridge reports Docker stopped", async () => {
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    installBridge({
      status: vi.fn().mockResolvedValue({ installed: true, running: false, containers: [] }),
      startDesktop: vi.fn().mockResolvedValue({ success: true }),
    })
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(await screen.findByRole("button", { name: /start docker desktop/i })).toBeInTheDocument()
  })

  it("calls startDesktop when the button is clicked", async () => {
    const startDesktop = vi.fn().mockResolvedValue({ success: true })
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    installBridge({
      status: vi.fn().mockResolvedValue({ installed: true, running: false, containers: [] }),
      startDesktop,
    })
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await userEvent.click(await screen.findByRole("button", { name: /start docker desktop/i }))
    await waitFor(() => expect(startDesktop).toHaveBeenCalledTimes(1))
  })

  it("shows the bridge's error verbatim so CERID_REPO_ROOT is discoverable", async () => {
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    installBridge({
      status: vi.fn().mockResolvedValue({ installed: true, running: true, containers: [] }),
      start: vi.fn().mockResolvedValue({ success: false, error: REPO_ROOT_ERROR }),
    })
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await userEvent.click(await screen.findByRole("button", { name: /start cerid services/i }))
    expect(await screen.findByText(REPO_ROOT_ERROR)).toBeInTheDocument()
  })

  it("offers to start the stack when Docker runs but no Cerid container does", async () => {
    mockFetchSystemCheck.mockRejectedValue(new Error("Network error"))
    const start = vi.fn().mockResolvedValue({ success: true })
    installBridge({
      status: vi.fn().mockResolvedValue({ installed: true, running: true, containers: [] }),
      start,
    })
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    await userEvent.click(await screen.findByRole("button", { name: /start cerid services/i }))
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole("button", { name: /start docker desktop/i })).not.toBeInTheDocument()
  })

  it("keeps the manual instructions and shows no start button without the bridge", async () => {
    mockFetchSystemCheck.mockResolvedValue({ ...HEALTHY_RESULT, docker_running: false })
    render(<SystemCheckCard onCheckComplete={vi.fn()} />)
    expect(await screen.findByRole("link", { name: /download docker desktop/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /start docker desktop/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /start cerid services/i })).not.toBeInTheDocument()
  })
})
