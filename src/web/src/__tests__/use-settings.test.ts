// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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
    // CH8: default must be one of the toolbar dropdown options so the radio
    // group shows a selected item. 0.15 is the "Standard" option.
    expect(result.current.autoInjectThreshold).toBe(0.15)
    expect([0.10, 0.15, 0.25, 0.40]).toContain(result.current.autoInjectThreshold)
    // RA-48: mirrors the server's AUTO_INJECT_MAX default (src/mcp/config/settings.py)
    expect(result.current.autoInjectMax).toBe(3)
    expect(result.current.costSensitivity).toBe("medium")
    expect(result.current.hallucinationEnabled).toBe(false)
  })

  it("reads from localStorage on init", () => {
    localStorage.setItem("cerid-feedback-loop", "true")
    localStorage.setItem("cerid-hallucination-check", "true")
    localStorage.setItem("cerid-cost-sensitivity", "high")
    localStorage.setItem("cerid-auto-inject-threshold", "0.9")
    localStorage.setItem("cerid-auto-inject-max", "7")

    const { result } = renderHook(() => useSettings())
    expect(result.current.feedbackLoop).toBe(true)
    expect(result.current.hallucinationEnabled).toBe(true)
    expect(result.current.costSensitivity).toBe("high")
    expect(result.current.autoInjectThreshold).toBe(0.9)
    expect(result.current.autoInjectMax).toBe(7)
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

  it("setRoutingMode sets mode and persists to localStorage", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.setRoutingMode("auto") })
    expect(result.current.routingMode).toBe("auto")
    expect(localStorage.getItem("cerid-routing-mode")).toBe("auto")

    act(() => { result.current.setRoutingMode("recommend") })
    expect(result.current.routingMode).toBe("recommend")

    act(() => { result.current.setRoutingMode("manual") })
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

  it("sets autoInjectMax", () => {
    const { result } = renderHook(() => useSettings())
    act(() => { result.current.setAutoInjectMax(5) })
    expect(result.current.autoInjectMax).toBe(5)
    expect(localStorage.getItem("cerid-auto-inject-max")).toBe("5")
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
      auto_inject_max: 8,
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
    // RA-48: the server's configured AUTO_INJECT_MAX must hydrate into the
    // client hook that use-chat-send.ts consumes — without this, the client
    // always enforced the hardcoded default regardless of server config.
    expect(result.current.autoInjectMax).toBe(8)
  })

  it("falls back to localStorage when server fails", async () => {
    localStorage.setItem("cerid-feedback-loop", "true")
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")))

    const { result } = renderHook(() => useSettings())
    // Should keep localStorage value
    expect(result.current.feedbackLoop).toBe(true)
  })

  // ── Audit F-7: version-vector reconciliation across machines ──────────────
  describe("cross-machine reconciliation", () => {
    /**
     * Route fetch calls to their appropriate mock payloads.
     *
     * `useSettings` hits /settings, /user-state, and /settings/private-mode
     * during hydration. We key on URL substring so the test can control
     * each response without caring about call order.
     */
    function routedFetch(routes: Record<string, unknown>) {
      return vi.fn((url: string) => {
        for (const [key, body] of Object.entries(routes)) {
          if (url.includes(key)) {
            return Promise.resolve({
              ok: true,
              status: 200,
              json: () => Promise.resolve(body),
            })
          }
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        })
      })
    }

    it("server wins when server.updated_at is newer than localUpdatedAt", async () => {
      // Local wrote at t=1000, server wrote at t=2000 (a later machine's edit).
      localStorage.setItem("cerid-feedback-loop", "false")
      localStorage.setItem("cerid-settings-updated-at", "1000")
      const serverIso = new Date(2000).toISOString()

      const fetchMock = routedFetch({
        "/settings/private-mode": { enabled: false, level: 0 },
        "/user-state": {
          settings: { updated_at: serverIso },
          preferences: {},
          conversation_ids: [],
        },
        "/settings": { enable_feedback_loop: true },
      })
      vi.stubGlobal("fetch", fetchMock)

      const { result } = renderHook(() => useSettings())

      await waitFor(() => {
        expect(result.current.feedbackLoop).toBe(true)
      })
      // Local record replaced and stamp advanced to the server revision.
      expect(localStorage.getItem("cerid-feedback-loop")).toBe("true")
      expect(localStorage.getItem("cerid-settings-updated-at")).toBe("2000")
      // No PATCH fired — we took the server value, not pushed ours.
      const patchCalls = fetchMock.mock.calls.filter(
        (call) => {
          const [url, init] = call as unknown as [string, RequestInit | undefined]
          return url.includes("/settings") && init?.method === "PATCH"
        },
      )
      expect(patchCalls).toHaveLength(0)
    })

    it("local wins when localUpdatedAt is newer than server.updated_at", async () => {
      // Local wrote at t=5000, server still has t=1000. Expect a PATCH with
      // the local toggle values so the server catches up.
      localStorage.setItem("cerid-feedback-loop", "true")
      localStorage.setItem("cerid-settings-updated-at", "5000")
      const serverIso = new Date(1000).toISOString()

      const fetchMock = routedFetch({
        "/settings/private-mode": { enabled: false, level: 0 },
        "/user-state": {
          settings: { updated_at: serverIso },
          preferences: {},
          conversation_ids: [],
        },
        "/settings": { enable_feedback_loop: false },
      })
      vi.stubGlobal("fetch", fetchMock)

      const { result } = renderHook(() => useSettings())

      // Local value is preserved — reconciliation must not clobber it with
      // the stale server value.
      await waitFor(() => {
        expect(result.current.feedbackLoop).toBe(true)
      })

      // PATCH fires with the divergent local toggle.
      await waitFor(() => {
        const patchCalls = fetchMock.mock.calls.filter((call) => {
          const [url, init] = call as unknown as [string, RequestInit | undefined]
          return url.includes("/settings") && init?.method === "PATCH"
        })
        expect(patchCalls.length).toBeGreaterThan(0)
        const [, firstInit] = patchCalls[0] as unknown as [string, RequestInit]
        const body = JSON.parse(firstInit.body as string)
        expect(body.enable_feedback_loop).toBe(true)
      })
    })
  })
})

describe("useSettings — private mode reconciliation (E1 CR-020)", () => {
  function routed(routes: Record<string, { ok?: boolean; status?: number; body: unknown }>) {
    return vi.fn((url: string) => {
      for (const [key, r] of Object.entries(routes)) {
        if (url.includes(key)) {
          return Promise.resolve({
            ok: r.ok ?? true,
            status: r.status ?? 200,
            json: () => Promise.resolve(r.body),
            text: () => Promise.resolve(JSON.stringify(r.body)),
          })
        }
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
    })
  }

  it("hydrates a genuinely-authoritative server private-mode value on a fresh client (WB-38 regression)", async () => {
    // Reproduces the WB-38 frontend regression: a client that has never
    // touched any *other* setting locally (no `cerid-settings-updated-at`
    // stamp) and whose server settings.json was also never explicitly
    // written (no `updated_at`), so the old `serverWins` gate — computed
    // from that unrelated timestamp — was false even though the server's
    // private-mode Redis flag is genuinely ON. Private-mode writes never
    // advance settings.json's `updated_at` on either side, so gating
    // hydration on it made this a near-permanent no-op. The fix hydrates
    // whenever there's no in-flight local optimistic write, independent of
    // the unrelated settings timestamp.
    vi.stubGlobal("fetch", routed({
      "/settings/private-mode": { body: { level: 2 } },
      "/user-state": { body: { settings: {}, preferences: {}, conversation_ids: [] } },
      "/settings": { body: {} },
    }))

    const { result } = renderHook(() => useSettings())

    await waitFor(() => expect(result.current.privateModeEnabled).toBe(true))
    expect(result.current.privateModeLevel).toBe(2)
    expect(localStorage.getItem("cerid-private-mode")).toBe("true")
    expect(localStorage.getItem("cerid-private-mode-level")).toBe("2")
  })

  it("reconciles a stale local private flag when the server disagrees", async () => {
    // Local cache says private is OFF; the server's global private-mode
    // flag is ON. The server is the single authoritative source for private
    // mode (app/services/private_mode.py) — this must hydrate regardless of
    // the unrelated settings.json revision stamp.
    localStorage.setItem("cerid-private-mode", "false")
    localStorage.setItem("cerid-private-mode-level", "0")
    vi.stubGlobal("fetch", routed({
      "/settings/private-mode": { body: { level: 1 } },
      "/user-state": { body: { settings: {}, preferences: {}, conversation_ids: [] } },
      "/settings": { body: {} },
    }))

    const { result } = renderHook(() => useSettings())

    await waitFor(() => expect(result.current.privateModeEnabled).toBe(true))
    expect(result.current.privateModeLevel).toBe(1)
    expect(localStorage.getItem("cerid-private-mode")).toBe("true")
    expect(localStorage.getItem("cerid-private-mode-level")).toBe("1")
  })

  it("does not let a slower initial GET clobber an in-flight local private-mode write (WB-38)", async () => {
    // The mount-time hydration fetch and a user-triggered togglePrivateMode()
    // can race: if the user toggles before the initial private-mode GET
    // resolves, the GET's (now stale) answer must not stomp the optimistic
    // write the user just made. This is the one case use-settings still
    // guards against — everything else defers to the server as authoritative.
    let resolvePrivateMode!: (v: { ok: boolean; status: number; json: () => Promise<unknown>; text: () => Promise<string> }) => void
    const privateModePromise = new Promise((resolve) => { resolvePrivateMode = resolve })
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      // The mount-time GET (no `method`, defaults to GET) stays pending until
      // resolved below; the toggle's own POST write resolves immediately so
      // the optimistic state isn't reverted by an unrelated failure.
      if (url.includes("/settings/private-mode") && !init?.method) return privateModePromise
      if (url.includes("/settings/private-mode")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ level: 1 }), text: () => Promise.resolve("{}") })
      }
      if (url.includes("/user-state")) {
        return Promise.resolve({
          ok: true, status: 200,
          json: () => Promise.resolve({ settings: {}, preferences: {}, conversation_ids: [] }),
          text: () => Promise.resolve("{}"),
        })
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { result } = renderHook(() => useSettings())

    // User toggles private mode on before the mount-time GET has resolved.
    act(() => { result.current.togglePrivateMode() })
    expect(result.current.privateModeEnabled).toBe(true)
    expect(result.current.privateModeLevel).toBe(1)

    // The slower initial GET now resolves with the server's pre-toggle
    // answer (level 0) — it must not clobber the just-made local write.
    await act(async () => {
      resolvePrivateMode({ ok: true, status: 200, json: () => Promise.resolve({ level: 0 }), text: () => Promise.resolve("{}") })
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(result.current.privateModeEnabled).toBe(true)
    expect(result.current.privateModeLevel).toBe(1)
  })

  it("keeps the local private flag when the server errors (no flip to not-private)", async () => {
    localStorage.setItem("cerid-private-mode", "true")
    localStorage.setItem("cerid-private-mode-level", "1")
    vi.stubGlobal("fetch", routed({ "/settings/private-mode": { ok: false, status: 500, body: {} } }))

    const { result } = renderHook(() => useSettings())
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    // Server errored → fetchPrivateMode threw → local (private) value preserved.
    expect(result.current.privateModeEnabled).toBe(true)
    expect(localStorage.getItem("cerid-private-mode")).toBe("true")
  })
})

describe("useSettings — private mode write failures reconcile local state (WB-37)", () => {
  function routed(routes: Record<string, { ok?: boolean; status?: number; body: unknown }>) {
    return vi.fn((url: string) => {
      for (const [key, r] of Object.entries(routes)) {
        if (url.includes(key)) {
          return Promise.resolve({
            ok: r.ok ?? true,
            status: r.status ?? 200,
            json: () => Promise.resolve(r.body),
            text: () => Promise.resolve(JSON.stringify(r.body)),
          })
        }
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}") })
    })
  }

  it("reverts togglePrivateMode's optimistic state when the server write fails", async () => {
    // No hydration disagreement — mount hydrate is a no-op (fetch stubbed to
    // reject below before render).
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no server")))
    const { result } = renderHook(() => useSettings())
    expect(result.current.privateModeEnabled).toBe(false)
    expect(result.current.privateModeLevel).toBe(0)

    // Now the write itself fails (POST /settings/private-mode 500).
    vi.stubGlobal("fetch", routed({ "/settings/private-mode": { ok: false, status: 500, body: {} } }))

    act(() => { result.current.togglePrivateMode() })
    // Optimistic update applied immediately.
    expect(result.current.privateModeEnabled).toBe(true)
    expect(result.current.privateModeLevel).toBe(1)

    // Once enablePrivateMode's rejection is observed, the optimistic state
    // reverts to what it was before the toggle instead of sticking on a
    // write that never landed server-side.
    await waitFor(() => expect(result.current.privateModeEnabled).toBe(false))
    expect(result.current.privateModeLevel).toBe(0)
    expect(localStorage.getItem("cerid-private-mode")).toBe("false")
    expect(localStorage.getItem("cerid-private-mode-level")).toBe("0")
  })

  it("reverts changePrivateModeLevel's optimistic state when the server write fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no server")))
    localStorage.setItem("cerid-private-mode", "true")
    localStorage.setItem("cerid-private-mode-level", "2")
    const { result } = renderHook(() => useSettings())
    expect(result.current.privateModeLevel).toBe(2)

    vi.stubGlobal("fetch", routed({ "/settings/private-mode": { ok: false, status: 500, body: {} } }))

    act(() => { result.current.changePrivateModeLevel(3) })
    expect(result.current.privateModeLevel).toBe(3)

    await waitFor(() => expect(result.current.privateModeLevel).toBe(2))
    expect(result.current.privateModeEnabled).toBe(true)
    expect(localStorage.getItem("cerid-private-mode-level")).toBe("2")
  })
})
