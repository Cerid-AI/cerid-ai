// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { toast } from "sonner"

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
  Toaster: () => null,
}))

describe("Global MutationCache.onError", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.resetModules()
  })

  it("fires toast.error with the error message on mutation failure", async () => {
    const { queryClient } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})

    await queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationKey: ["demo"],
        mutationFn: () => Promise.reject(new Error("boom")),
      })
      .execute(undefined)
      .catch(() => {})

    expect(toast.error).toHaveBeenCalledWith("boom")
    expect(consoleErr).toHaveBeenCalledWith(
      "mutation.failure",
      expect.objectContaining({
        err: expect.any(Error),
        mutationKey: ["demo"],
        fullMessage: "boom",
      }),
    )
    consoleErr.mockRestore()
  })

  it("extracts message from plain string errors", async () => {
    const { queryClient } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})

    await queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationKey: ["demo"],
        mutationFn: () => Promise.reject("string-error"),
      })
      .execute(undefined)
      .catch(() => {})

    expect(toast.error).toHaveBeenCalledWith("string-error")
    consoleErr.mockRestore()
  })

  it("falls back to 'Something went wrong' for non-Error/non-string errors", async () => {
    const { queryClient } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})

    await queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationKey: ["demo"],
        mutationFn: () => Promise.reject({ random: "object" }),
      })
      .execute(undefined)
      .catch(() => {})

    expect(toast.error).toHaveBeenCalledWith("Something went wrong")
    consoleErr.mockRestore()
  })
})
