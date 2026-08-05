// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { withViewTransition, tagForTransition } from "@/lib/view-transitions"

describe("withViewTransition", () => {
  beforeEach(() => {
    // Reset both feature flags between tests
    delete (document as unknown as { startViewTransition?: unknown }).startViewTransition
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    })
  })

  it("runs update directly when startViewTransition is unavailable", async () => {
    const update = vi.fn()
    await withViewTransition(update)
    expect(update).toHaveBeenCalledOnce()
  })

  it("uses startViewTransition when available", async () => {
    const finished = Promise.resolve()
    const mockTransition = vi.fn(() => ({
      ready: Promise.resolve(),
      finished,
      updateCallbackDone: Promise.resolve(),
      skipTransition: vi.fn(),
    }))
    ;(document as unknown as { startViewTransition: unknown }).startViewTransition = mockTransition

    const update = vi.fn()
    await withViewTransition(update)
    expect(mockTransition).toHaveBeenCalledOnce()
    // The browser invokes the update callback itself; we just verified
    // startViewTransition was called with our update wrapper.
  })

  it("bypasses transition when prefers-reduced-motion is set", async () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    })
    const mockTransition = vi.fn()
    ;(document as unknown as { startViewTransition: unknown }).startViewTransition = mockTransition

    const update = vi.fn()
    await withViewTransition(update)
    expect(update).toHaveBeenCalledOnce()
    expect(mockTransition).not.toHaveBeenCalled()
  })
})

describe("tagForTransition", () => {
  beforeEach(() => {
    delete (document as unknown as { startViewTransition?: unknown }).startViewTransition
  })

  it("returns a no-op when startViewTransition is unavailable", () => {
    const el = document.createElement("div")
    const cleanup = tagForTransition(el, "focal-entity")
    expect(el.style.viewTransitionName).toBe("")
    cleanup()
  })

  it("sets viewTransitionName when supported and restores on cleanup", () => {
    ;(document as unknown as { startViewTransition: unknown }).startViewTransition = vi.fn()
    const el = document.createElement("div")
    el.style.viewTransitionName = "prior"
    const cleanup = tagForTransition(el, "focal-entity")
    expect(el.style.viewTransitionName).toBe("focal-entity")
    cleanup()
    expect(el.style.viewTransitionName).toBe("prior")
  })

  it("handles null element gracefully", () => {
    const cleanup = tagForTransition(null, "focal-entity")
    expect(typeof cleanup).toBe("function")
    cleanup()
  })
})
