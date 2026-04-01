// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useTheme } from "@/hooks/use-theme"

beforeEach(() => {
  localStorage.clear()
  document.documentElement.classList.remove("dark")
})

describe("useTheme", () => {
  it("defaults to dark when no localStorage and matchMedia prefers dark", () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: query === "(prefers-color-scheme: dark)",
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    })
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe("dark")
  })

  it("reads stored theme from localStorage", () => {
    localStorage.setItem("cerid-theme", "light")
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe("light")
  })

  it("toggles from dark to light", () => {
    localStorage.setItem("cerid-theme", "dark")
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe("dark")

    act(() => { result.current.toggleTheme() })
    expect(result.current.theme).toBe("light")
  })

  it("toggles from light to dark", () => {
    localStorage.setItem("cerid-theme", "light")
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe("light")

    act(() => { result.current.toggleTheme() })
    expect(result.current.theme).toBe("dark")
  })

  it("persists theme to localStorage on change", () => {
    localStorage.setItem("cerid-theme", "dark")
    const { result } = renderHook(() => useTheme())
    act(() => { result.current.toggleTheme() })
    expect(localStorage.getItem("cerid-theme")).toBe("light")
  })

  it("adds dark class to documentElement when dark", () => {
    localStorage.setItem("cerid-theme", "dark")
    renderHook(() => useTheme())
    expect(document.documentElement.classList.contains("dark")).toBe(true)
  })

  it("removes dark class when light", () => {
    document.documentElement.classList.add("dark")
    localStorage.setItem("cerid-theme", "light")
    renderHook(() => useTheme())
    expect(document.documentElement.classList.contains("dark")).toBe(false)
  })
})
