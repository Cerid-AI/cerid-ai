// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useSettings } from "@/hooks/use-settings"

function mockFetch(data: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  }))
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  // Stub fetch to prevent server hydration from affecting tests unless needed
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no server")))
})

describe("useSettings", () => {
  it("returns default values when localStorage is empty", () => {
    const { result } = renderHook(() => useSettings())
    expect(result.current.feedbackLoop).toBe(false)
    expect(result.current.showDashboard).toBe(false)
    expect(result.current.routingMode).toBe("manual")
    expect(result.current.autoInject).toBe(false)
    expect(result.current.autoInjectThreshold).toBe(0.82)
    expect(result.current.costSensitivity).toBe("medium")
    expect(result.current.hallucinationEnabled).toBe(false)
  })

  it("reads from localStorage on init", () => {
    localStorage.setItem("cerid-feedback-loop", "true")
    localStorage.setItem("cerid-hallucination-check", "true")
    localStorage.setItem("cerid-cost-sensitivity", "high")
    localStorage.setItem("cerid-auto-inject-threshold", "0.9")

    const { result } = renderHook(() => useSettings())
    expect(result.current.feedbackLoop).toBe(true)
    expect(result.current.hallucinationEnabled).toBe(true)
    expect(result.current.costSensitivity).toBe("high")
    expect(result.current.autoInjectThreshold).toBe(0.9)
  })

  it("toggles feedbackLoop and persists to localStorage", () => {
    const { result } = renderHook(() => useSettings())
    expect(result.current.feedbackLoop).toBe(false)

    act(() => { result.current.toggleFeedbackLoop() })
    expect(result.current.feedbackLoop).toBe(true)
    expect(localStorage.getItem("cerid-feedback-loop")).toBe("true")
  })

  it("toggles showDashboard", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.toggleDashboard() })
    expect(result.current.showDashboard).toBe(true)
    expect(localStorage.getItem("cerid-show-dashboard")).toBe("true")
  })

  it("cycles routingMode through manual → recommend → auto → manual", () => {
    const { result } = renderHook(() => useSettings())
    expect(result.current.routingMode).toBe("manual")

    act(() => { result.current.cycleRoutingMode() })
    expect(result.current.routingMode).toBe("recommend")
    expect(localStorage.getItem("cerid-routing-mode")).toBe("recommend")

    act(() => { result.current.cycleRoutingMode() })
    expect(result.current.routingMode).toBe("auto")

    act(() => { result.current.cycleRoutingMode() })
    expect(result.current.routingMode).toBe("manual")
  })

  it("migrates old autoModelSwitch boolean to routingMode", () => {
    localStorage.setItem("cerid-auto-model-switch", "true")
    const { result } = renderHook(() => useSettings())
    expect(result.current.routingMode).toBe("recommend")
  })

  it("toggles autoInject", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.toggleAutoInject() })
    expect(result.current.autoInject).toBe(true)
    expect(localStorage.getItem("cerid-auto-inject")).toBe("true")
  })

  it("sets autoInjectThreshold", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.setAutoInjectThreshold(0.95) })
    expect(result.current.autoInjectThreshold).toBe(0.95)
    expect(localStorage.getItem("cerid-auto-inject-threshold")).toBe("0.95")
  })

  it("updates costSensitivity", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.updateCostSensitivity("low") })
    expect(result.current.costSensitivity).toBe("low")
    expect(localStorage.getItem("cerid-cost-sensitivity")).toBe("low")
  })

  it("toggles hallucinationEnabled", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.toggleHallucinationEnabled() })
    expect(result.current.hallucinationEnabled).toBe(true)
    expect(localStorage.getItem("cerid-hallucination-check")).toBe("true")
  })

  it("hydrates from server on mount", async () => {
    mockFetch({
      enable_feedback_loop: true,
      enable_hallucination_check: true,
      cost_sensitivity: "high",
      enable_model_router: true,
      enable_auto_inject: true,
      auto_inject_threshold: 0.88,
    })

    const { result } = renderHook(() => useSettings())
    // Initially false from localStorage
    expect(result.current.feedbackLoop).toBe(false)

    // After server hydration
    await waitFor(() => {
      expect(result.current.feedbackLoop).toBe(true)
    })
    expect(result.current.hallucinationEnabled).toBe(true)
    expect(result.current.costSensitivity).toBe("high")
    expect(result.current.routingMode).toBe("recommend")
    expect(result.current.autoInject).toBe(true)
    expect(result.current.autoInjectThreshold).toBe(0.88)
  })

  it("falls back to localStorage when server fails", async () => {
    localStorage.setItem("cerid-feedback-loop", "true")
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")))

    const { result } = renderHook(() => useSettings())
    // Should keep localStorage value
    expect(result.current.feedbackLoop).toBe(true)
  })
})
