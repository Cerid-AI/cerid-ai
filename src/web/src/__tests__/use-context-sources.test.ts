// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"

vi.mock("@/lib/api", () => ({ updateSettings: vi.fn().mockResolvedValue({ status: "ok", updated: {} }) }))

import { updateSettings } from "@/lib/api"
import { useContextSources } from "@/hooks/use-context-sources"

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})

describe("useContextSources (F229)", () => {
  it("keeps a toggle in this browser and sends no settings PATCH the server would drop", () => {
    const { result } = renderHook(() => useContextSources())

    act(() => result.current.toggleSource("memory"))

    expect(result.current.sources).toEqual({ kb: true, memory: false, external: true })
    expect(JSON.parse(localStorage.getItem("cerid-context-sources")!)).toEqual({ kb: true, memory: false, external: true })
    expect(updateSettings).not.toHaveBeenCalled()
  })
})
