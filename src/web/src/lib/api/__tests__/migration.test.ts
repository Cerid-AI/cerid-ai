// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * /api/migrate/* is mounted only by editions that ship the Pro/Enterprise
 * migration router, so the client has to be able to tell "this server does
 * not have the endpoint" from "the endpoint rejected my call".
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { isNotionMigrationAvailable, migrateNotionExport } from "@/lib/api/migration"

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("isNotionMigrationAvailable", () => {
  it("is false when the router is not mounted (404 on the path)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    await expect(isNotionMigrationAvailable()).resolves.toBe(false)
  })

  it("is true when the path exists but rejects the probe's method (405)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 405 })
    vi.stubGlobal("fetch", fetchMock)

    await expect(isNotionMigrationAvailable()).resolves.toBe(true)
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/migrate/notion")
    // A probe must not upload anything.
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
  })

  it("rejects on a transport failure rather than reporting the route absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")))
    await expect(isNotionMigrationAvailable()).rejects.toThrow(/failed to fetch/i)
  })
})

describe("migrateNotionExport", () => {
  it("surfaces the status code when the endpoint is missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, text: async () => "Not Found" }),
    )
    const file = new File(["zip"], "export.zip", { type: "application/zip" })
    await expect(migrateNotionExport(file)).rejects.toThrow(/HTTP 404/)
  })
})
