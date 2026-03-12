// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { UIModeProvider, useUIMode } from "@/contexts/ui-mode-context"

beforeEach(() => {
  localStorage.clear()
})

describe("UIModeProvider", () => {
  it("defaults to simple for new users (no onboarding complete)", () => {
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.mode).toBe("simple")
    expect(result.current.isSimple).toBe(true)
  })

  it("defaults to advanced for returning users (onboarding complete)", () => {
    localStorage.setItem("cerid-onboarding-complete", "true")
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.mode).toBe("advanced")
    expect(result.current.isSimple).toBe(false)
  })

  it("reads explicit mode from localStorage", () => {
    localStorage.setItem("cerid-ui-mode", "advanced")
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.mode).toBe("advanced")
  })

  it("explicit mode overrides onboarding default", () => {
    localStorage.setItem("cerid-ui-mode", "simple")
    localStorage.setItem("cerid-onboarding-complete", "true")
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.mode).toBe("simple")
  })

  it("setMode updates mode and persists to localStorage", () => {
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    act(() => { result.current.setMode("advanced") })
    expect(result.current.mode).toBe("advanced")
    expect(result.current.isSimple).toBe(false)
    expect(localStorage.getItem("cerid-ui-mode")).toBe("advanced")
  })

  it("toggle switches from simple to advanced", () => {
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.mode).toBe("simple")
    act(() => { result.current.toggle() })
    expect(result.current.mode).toBe("advanced")
    expect(localStorage.getItem("cerid-ui-mode")).toBe("advanced")
  })

  it("toggle switches from advanced to simple", () => {
    localStorage.setItem("cerid-ui-mode", "advanced")
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.mode).toBe("advanced")
    act(() => { result.current.toggle() })
    expect(result.current.mode).toBe("simple")
    expect(localStorage.getItem("cerid-ui-mode")).toBe("simple")
  })

  it("isSimple reflects mode correctly", () => {
    const { result } = renderHook(() => useUIMode(), { wrapper: UIModeProvider })
    expect(result.current.isSimple).toBe(true)
    act(() => { result.current.setMode("advanced") })
    expect(result.current.isSimple).toBe(false)
    act(() => { result.current.setMode("simple") })
    expect(result.current.isSimple).toBe(true)
  })
})

describe("useUIMode outside provider", () => {
  it("throws when used outside UIModeProvider", () => {
    expect(() => {
      renderHook(() => useUIMode())
    }).toThrow("useUIMode must be used within UIModeProvider")
  })
})
