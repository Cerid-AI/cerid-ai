// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { toast } from "sonner"

import { captureException } from "@/lib/sentry"

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
  Toaster: () => null,
}))

vi.mock("@/lib/sentry", () => ({
  captureException: vi.fn(),
}))

describe("notifyError — imperative error surface", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("toasts the message, logs structured context, and captures to Sentry", async () => {
    const { notifyError } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})

    notifyError(new Error("disk on fire"), { op: "memory.delete", memoryId: "m-1" })

    expect(toast.error).toHaveBeenCalledWith("disk on fire")
    expect(consoleErr).toHaveBeenCalledWith(
      "client.error",
      expect.objectContaining({
        op: "memory.delete",
        memoryId: "m-1",
        fullMessage: "disk on fire",
      }),
    )
    expect(captureException).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ op: "memory.delete", fullMessage: "disk on fire" }),
    )
    consoleErr.mockRestore()
  })

  it("coerces non-Error throwables to a readable message", async () => {
    const { notifyError } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})

    notifyError("string boom")
    expect(toast.error).toHaveBeenCalledWith("string boom")

    notifyError({ weird: true })
    expect(toast.error).toHaveBeenCalledWith("Something went wrong")
    consoleErr.mockRestore()
  })

  it("truncates very long messages for the toast but logs the full text", async () => {
    const { notifyError } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})
    const long = "x".repeat(500)

    notifyError(new Error(long))

    const toasted = vi.mocked(toast.error).mock.calls[0][0] as string
    expect(toasted.length).toBeLessThanOrEqual(240)
    expect(toasted.endsWith("…")).toBe(true)
    expect(consoleErr).toHaveBeenCalledWith(
      "client.error",
      expect.objectContaining({ fullMessage: long }),
    )
    consoleErr.mockRestore()
  })

  it("does not throw when the toast backend itself fails", async () => {
    const { notifyError } = await import("@/lib/query-client")
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {})
    vi.mocked(toast.error).mockImplementationOnce(() => {
      throw new Error("sonner down")
    })

    expect(() => notifyError(new Error("original"))).not.toThrow()
    expect(consoleErr).toHaveBeenCalledWith("client.error.toast_threw", expect.any(Error))
    consoleErr.mockRestore()
  })
})
