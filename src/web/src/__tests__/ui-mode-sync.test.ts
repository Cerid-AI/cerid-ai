// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import React from "react"

vi.mock("@/lib/api", () => ({
  syncPreferences: vi.fn().mockResolvedValue(undefined),
  fetchUserState: vi.fn().mockResolvedValue({
    settings: {}, preferences: {}, conversation_ids: [],
  }),
  fetchSettings: vi.fn().mockResolvedValue({}),
  updateSettings: vi.fn().mockResolvedValue({}),
}))

import { renderHook, act } from "@testing-library/react"
import { UIModeProvider, useUIMode } from "@/contexts/ui-mode-context"
import * as api from "@/lib/api"

describe("UI Mode cloud sync", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it("syncs mode change to server", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(UIModeProvider, null, children)
    const { result } = renderHook(() => useUIMode(), { wrapper })
    act(() => { result.current.setMode("advanced") })
    await vi.waitFor(() => {
      expect(api.syncPreferences).toHaveBeenCalledWith(
        expect.objectContaining({ ui_mode: "advanced" })
      )
    })
  })

  it("syncs toggle to server", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(UIModeProvider, null, children)
    const { result } = renderHook(() => useUIMode(), { wrapper })
    act(() => { result.current.toggle() })
    await vi.waitFor(() => {
      expect(api.syncPreferences).toHaveBeenCalledTimes(1)
    })
  })
})
