// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useWizardPackInstall — the wizard's async pack-install contract:
 *   POST 202 {job_id, status:"queued"}  → poll registry flags until settled
 *   POST 200 {status:"already_installed"} → immediate success
 *   POST 200 legacy synchronous body     → immediate success
 * plus the defensive flag parsing for registries from older backends.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const mockStartPackInstall = vi.fn()
const mockFetchRegistry = vi.fn()

vi.mock("@/lib/api/setup", () => ({
  startPackInstall: (...args: unknown[]) => mockStartPackInstall(...args),
}))

vi.mock("@/lib/api/knowledge-packs", () => ({
  fetchKnowledgePackRegistry: (...args: unknown[]) => mockFetchRegistry(...args),
}))

vi.mock("@/lib/log-swallowed", () => ({
  logSwallowedError: vi.fn(),
}))

import {
  packInstallFlags,
  registryHasInstalling,
  useWizardPackInstall,
} from "@/components/setup/use-wizard-pack-install"
import type { KnowledgePackSummary } from "@/lib/api/knowledge-packs"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makePack(overrides: Record<string, unknown> = {}): KnowledgePackSummary {
  return {
    id: "python-stdlib-docs",
    name: "Python Standard Library Documentation",
    version: "1.0.0",
    description: "Authoritative Python stdlib reference.",
    domain: "coding",
    sub_category: "python",
    tags: ["python"],
    license: "PSF-2.0",
    size_bytes: 167128,
    artifact_count: 208,
    download_url: "https://example.com/pystd.tar.gz",
    sha256: "abc123",
    provenance: { status: "built" },
    ...overrides,
  } as KnowledgePackSummary
}

function registryWith(pack: KnowledgePackSummary) {
  return { schema_version: 1, packs_by_domain: { coding: [pack] } }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

// Fast polling so the queued-path tests run in milliseconds.
const FAST = { pollIntervalMs: 5, maxPolls: 10 }

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// Flag parsing (defensive against older backends)
// ---------------------------------------------------------------------------

describe("packInstallFlags", () => {
  it("treats missing flags as not installed / not installing", () => {
    expect(packInstallFlags(makePack())).toEqual({ installed: false, installing: false })
  })

  it("reads explicit booleans", () => {
    expect(packInstallFlags(makePack({ installed: true, installing: false }))).toEqual({
      installed: true,
      installing: false,
    })
  })

  it("ignores non-boolean junk values", () => {
    expect(packInstallFlags(makePack({ installed: "yes", installing: 1 }))).toEqual({
      installed: false,
      installing: false,
    })
  })
})

describe("registryHasInstalling", () => {
  it("is false for undefined and for registries without flags", () => {
    expect(registryHasInstalling(undefined)).toBe(false)
    expect(registryHasInstalling(registryWith(makePack()))).toBe(false)
  })

  it("is true when any entry reports installing", () => {
    expect(registryHasInstalling(registryWith(makePack({ installing: true })))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Hook behavior
// ---------------------------------------------------------------------------

describe("useWizardPackInstall", () => {
  it("resolves immediately on a legacy synchronous 200", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "installed", jobId: null })
    const { result } = renderHook(() => useWizardPackInstall(FAST), { wrapper })

    await act(async () => {
      const outcome = await result.current.install("python-stdlib-docs")
      expect(outcome).toEqual({ alreadyInstalled: false })
    })

    expect(result.current.isSuccess).toBe(true)
    expect(result.current.installedPackId).toBe("python-stdlib-docs")
    expect(result.current.error).toBeNull()
    expect(mockFetchRegistry).not.toHaveBeenCalled()
  })

  it("resolves with alreadyInstalled=true on an already_installed response", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "already_installed", jobId: null })
    const { result } = renderHook(() => useWizardPackInstall(FAST), { wrapper })

    await act(async () => {
      const outcome = await result.current.install("python-stdlib-docs")
      expect(outcome).toEqual({ alreadyInstalled: true })
    })

    expect(result.current.isSuccess).toBe(true)
  })

  it("polls the registry after a 202 until the pack reports installed", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "queued", jobId: "job-42" })
    mockFetchRegistry
      .mockResolvedValueOnce(registryWith(makePack({ installed: false, installing: true })))
      .mockResolvedValueOnce(registryWith(makePack({ installed: false, installing: true })))
      .mockResolvedValue(registryWith(makePack({ installed: true, installing: false })))

    const { result } = renderHook(() => useWizardPackInstall(FAST), { wrapper })

    await act(async () => {
      const outcome = await result.current.install("python-stdlib-docs")
      expect(outcome).toEqual({ alreadyInstalled: false })
    })

    expect(result.current.isSuccess).toBe(true)
    expect(mockFetchRegistry.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it("fails when the job settles without the pack becoming installed", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "queued", jobId: "job-42" })
    mockFetchRegistry
      .mockResolvedValueOnce(registryWith(makePack({ installed: false, installing: true })))
      .mockResolvedValue(registryWith(makePack({ installed: false, installing: false })))

    const { result } = renderHook(() => useWizardPackInstall(FAST), { wrapper })

    await act(async () => {
      await expect(result.current.install("python-stdlib-docs")).rejects.toThrow(/install failed/i)
    })

    expect(result.current.isSuccess).toBe(false)
    expect(result.current.error?.message).toMatch(/install failed/i)
  })

  it("times out when the registry never reports the install settling", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "queued", jobId: "job-42" })
    // Never flips installing on — simulates the enqueue/flag race persisting.
    mockFetchRegistry.mockResolvedValue(registryWith(makePack()))

    const { result } = renderHook(
      () => useWizardPackInstall({ pollIntervalMs: 1, maxPolls: 3 }),
      { wrapper },
    )

    await act(async () => {
      await expect(result.current.install("python-stdlib-docs")).rejects.toThrow(/timed out/i)
    })

    expect(result.current.error?.message).toMatch(/timed out/i)
  })

  it("keeps polling through transient registry fetch failures", async () => {
    mockStartPackInstall.mockResolvedValue({ status: "queued", jobId: "job-42" })
    mockFetchRegistry
      .mockRejectedValueOnce(new Error("blip"))
      .mockResolvedValue(registryWith(makePack({ installed: true, installing: false })))

    const { result } = renderHook(() => useWizardPackInstall(FAST), { wrapper })

    await act(async () => {
      const outcome = await result.current.install("python-stdlib-docs")
      expect(outcome).toEqual({ alreadyInstalled: false })
    })

    expect(result.current.isSuccess).toBe(true)
  })

  it("surfaces POST failures and resets pending state", async () => {
    mockStartPackInstall.mockRejectedValue(new Error("HTTP 500"))
    const { result } = renderHook(() => useWizardPackInstall(FAST), { wrapper })

    await act(async () => {
      await expect(result.current.install("python-stdlib-docs")).rejects.toThrow("HTTP 500")
    })

    expect(result.current.isPending).toBe(false)
    expect(result.current.error?.message).toBe("HTTP 500")

    act(() => result.current.reset())
    expect(result.current.error).toBeNull()
    expect(result.current.installedPackId).toBeNull()
  })
})
