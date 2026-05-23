// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Phase C Day 3 — UIModeProvider was reduced to a pass-through that
// always returns "advanced". These tests lock that contract: legacy
// callers see no behavioral change beyond the constant value, and
// setMode/toggle are no-ops that don't crash.

import { describe, expect, it } from "vitest"
import { renderHook } from "@testing-library/react"
import { UIModeProvider, useUIMode } from "@/contexts/ui-mode-context"

describe("UIModeProvider — always advanced (Phase C)", () => {
  it("returns mode='advanced' regardless of localStorage state", () => {
    localStorage.setItem("cerid-ui-mode", "simple")
    const { result } = renderHook(() => useUIMode(), {
      wrapper: ({ children }) => <UIModeProvider>{children}</UIModeProvider>,
    })
    expect(result.current.mode).toBe("advanced")
    expect(result.current.isSimple).toBe(false)
  })

  it("setMode and toggle are no-ops (do not throw, do not mutate)", () => {
    const { result } = renderHook(() => useUIMode(), {
      wrapper: ({ children }) => <UIModeProvider>{children}</UIModeProvider>,
    })
    expect(() => result.current.setMode("simple")).not.toThrow()
    expect(() => result.current.toggle()).not.toThrow()
    expect(result.current.mode).toBe("advanced")
  })

  it("useUIMode() outside a Provider returns the always-advanced default", () => {
    const { result } = renderHook(() => useUIMode())
    expect(result.current.mode).toBe("advanced")
    expect(result.current.isSimple).toBe(false)
  })
})
