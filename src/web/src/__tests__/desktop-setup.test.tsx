// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Per-machine setup, and the gate that decides whether to show it.
//
// The bug: onboarding was gated entirely on the SERVER's `setup_required` /
// `onboarding_complete`. Connect a brand-new desktop client to a server
// configured months ago from a browser and both are false, so the client lands
// in the main app having never been asked for a TCC grant and never told where
// the Apple connectors are. The server's flag answers a different question
// from "has this Mac been set up".

import { describe, expect, it, vi, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  DESKTOP_SETUP_KEY,
  DesktopSetup,
  markDesktopSetupComplete,
  needsDesktopSetup,
} from "@/components/setup/desktop-setup"

afterEach(() => {
  localStorage.removeItem(DESKTOP_SETUP_KEY)
  delete (window as unknown as { cerid?: unknown }).cerid
  vi.restoreAllMocks()
})

describe("needsDesktopSetup", () => {
  it("is required on a desktop client that has not completed it", () => {
    expect(needsDesktopSetup({ hasDesktopBridge: true, completedFlag: null })).toBe(true)
  })

  it("is NOT required in a browser build", () => {
    // Nothing here is per-machine: the container serves the UI and injects the
    // key, and there are no TCC grants to give.
    expect(needsDesktopSetup({ hasDesktopBridge: false, completedFlag: null })).toBe(false)
  })

  it("is not required once this machine has completed it", () => {
    expect(needsDesktopSetup({ hasDesktopBridge: true, completedFlag: "true" })).toBe(false)
  })

  it("ignores the server's onboarding flag entirely", () => {
    // The regression that motivated this: a configured SERVER said onboarding
    // was complete, which suppressed setup on a Mac that had granted nothing.
    // This gate takes no server input at all — that is the fix, stated as a
    // signature rather than a comment.
    expect(needsDesktopSetup.length).toBe(1)
    expect(needsDesktopSetup({ hasDesktopBridge: true, completedFlag: null })).toBe(true)
  })
})

describe("markDesktopSetupComplete", () => {
  it("records completion for this machine", () => {
    markDesktopSetupComplete()
    expect(localStorage.getItem(DESKTOP_SETUP_KEY)).toBe("true")
  })

  it("survives localStorage being unavailable", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("private mode")
    })
    expect(() => markDesktopSetupComplete()).not.toThrow()
  })
})

describe("DesktopSetup", () => {
  function installBridge() {
    ;(window as unknown as { cerid: object }).cerid = {
      connection: {
        get: vi.fn().mockResolvedValue({
          mode: "local",
          serverUrl: "http://localhost:8888",
          hasApiKey: true,
        }),
        set: vi.fn().mockResolvedValue({}),
        test: vi.fn().mockResolvedValue({ ok: true, detail: "Connected", auth: "ok" }),
      },
      permissions: {
        // Realistic rows — an empty list renders no permissions at all, which
        // would make the mount assertion below pass for the wrong reason.
        getAll: vi.fn().mockResolvedValue([
          { category: "calendar", status: "not-determined", required: false, description: "Events" },
          { category: "reminders", status: "not-determined", required: false, description: "Tasks" },
          { category: "photos", status: "not-determined", required: false, description: "Library" },
          {
            category: "full-disk-access",
            status: "not-determined",
            required: false,
            description: "Notes, Mail, iMessage",
          },
        ]),
        get: vi.fn(),
        request: vi.fn(),
      },
      // PermissionsStep's getCeridBridge() requires `app` as well as
      // `permissions` — without it the component renders its browser fallback
      // and the mount assertion below fails for the wrong reason.
      app: { openExternal: vi.fn().mockResolvedValue({ success: true }) },
    }
  }

  it("starts on the connection step", async () => {
    installBridge()
    render(<DesktopSetup open onDone={vi.fn()} onOpenConnectors={vi.fn()} />)
    expect(await screen.findByTestId("desktop-setup-connection")).toBeInTheDocument()
  })

  it("walks connection → permissions → connectors", async () => {
    installBridge()
    render(<DesktopSetup open onDone={vi.fn()} onOpenConnectors={vi.fn()} />)
    const user = userEvent.setup()

    await user.click(await screen.findByTestId("desktop-setup-next"))
    expect(await screen.findByTestId("desktop-setup-permissions")).toBeInTheDocument()

    await user.click(screen.getByTestId("desktop-setup-next"))
    expect(await screen.findByTestId("desktop-setup-connectors")).toBeInTheDocument()
  })

  it("hands off to Connectors and records completion", async () => {
    // "Sources → Connectors" is not a path anyone guesses, so the last step
    // takes the user there rather than describing it.
    installBridge()
    const onOpenConnectors = vi.fn()
    render(<DesktopSetup open onDone={vi.fn()} onOpenConnectors={onOpenConnectors} />)
    const user = userEvent.setup()

    await user.click(await screen.findByTestId("desktop-setup-next"))
    await user.click(await screen.findByTestId("desktop-setup-next"))
    await user.click(await screen.findByTestId("desktop-setup-open-connectors"))

    expect(onOpenConnectors).toHaveBeenCalled()
    expect(localStorage.getItem(DESKTOP_SETUP_KEY)).toBe("true")
  })

  it("mounts PermissionsStep, which nothing else in the app does", async () => {
    // The step existed, had tests, and was reachable from no screen. This is
    // the assertion that keeps it mounted.
    installBridge()
    render(<DesktopSetup open onDone={vi.fn()} onOpenConnectors={vi.fn()} />)
    await userEvent.click(await screen.findByTestId("desktop-setup-next"))
    // The row test-ids PermissionsStep renders — a stable handle, unlike the
    // label text, which is split across elements.
    expect(await screen.findByTestId("permission-row-full-disk-access")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-calendar")).toBeInTheDocument()
    expect(screen.getByTestId("permission-row-photos")).toBeInTheDocument()
  })
})
