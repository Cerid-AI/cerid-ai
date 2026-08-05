// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { checkForUpdates } from "@/lib/api/updates"

beforeEach(() => vi.clearAllMocks())

describe("checkForUpdates", () => {
  it("returns parsed result on success", async () => {
    const payload = {
      running: "1.0.0",
      latest: "2.0.0",
      update_available: true,
      release_url: "https://github.com/Cerid-AI/cerid-ai/releases/tag/v2.0.0",
      error: null,
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload }),
    )
    const result = await checkForUpdates()
    expect(result.update_available).toBe(true)
    expect(result.latest).toBe("2.0.0")
    expect(result.release_url).toContain("v2.0.0")
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/updates/check")
  })

  it("returns up-to-date result when no update", async () => {
    const payload = {
      running: "1.5.0",
      latest: "1.5.0",
      update_available: false,
      release_url: null,
      error: null,
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload }),
    )
    const result = await checkForUpdates()
    expect(result.update_available).toBe(false)
    expect(result.latest).toBe("1.5.0")
  })

  it("returns error field when server reports degraded state", async () => {
    const payload = {
      running: "1.0.0",
      latest: null,
      update_available: false,
      release_url: null,
      error: "Could not retrieve release information",
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload }),
    )
    const result = await checkForUpdates()
    expect(result.update_available).toBe(false)
    expect(result.error).toBeTruthy()
    expect(result.latest).toBeNull()
  })

  it("throws when the fetch response is not ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ detail: "service unavailable" }),
      }),
    )
    await expect(checkForUpdates()).rejects.toThrow()
  })

  it("appends ?force=true when force param is true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ running: "1.0.0", latest: "1.0.0", update_available: false, release_url: null, error: null }),
      }),
    )
    await checkForUpdates(true)
    const calledUrl = String(vi.mocked(fetch).mock.calls[0][0])
    expect(calledUrl).toContain("force=true")
  })

  it("does not append force param when called without argument", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ running: "1.0.0", latest: "1.0.0", update_available: false, release_url: null, error: null }),
      }),
    )
    await checkForUpdates()
    const calledUrl = String(vi.mocked(fetch).mock.calls[0][0])
    expect(calledUrl).not.toContain("force=true")
  })
})
