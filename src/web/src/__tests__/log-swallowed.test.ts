// src/web/src/__tests__/log-swallowed.test.ts
import { describe, it, expect, vi, afterEach } from "vitest"

describe("logSwallowedError", () => {
  let consoleWarn: ReturnType<typeof vi.spyOn>

  afterEach(() => {
    consoleWarn?.mockRestore()
  })

  it("warns with reason + err in dev mode", async () => {
    vi.stubEnv("DEV", true)
    consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const { logSwallowedError } = await import("../lib/log-swallowed")
    const err = new Error("boom")
    logSwallowedError(err, "test-reason", { k: 1 })
    expect(consoleWarn).toHaveBeenCalledWith("[swallowed] test-reason", err, { k: 1 })
  })

  it("passes empty object when extra omitted", async () => {
    vi.stubEnv("DEV", true)
    consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const { logSwallowedError } = await import("../lib/log-swallowed")
    logSwallowedError("simple-err", "no-extra")
    expect(consoleWarn).toHaveBeenCalledWith("[swallowed] no-extra", "simple-err", {})
  })

  it("is silent in production", async () => {
    vi.stubEnv("DEV", false)
    vi.resetModules()
    consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {})
    const { logSwallowedError } = await import("../lib/log-swallowed")
    logSwallowedError(new Error("prod-err"), "prod-reason")
    expect(consoleWarn).not.toHaveBeenCalled()
  })
})
