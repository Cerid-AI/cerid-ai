// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import "@testing-library/jest-dom/vitest"
import { afterEach, expect, vi } from "vitest"
import { cleanup } from "@testing-library/react"
import { toHaveNoViolations } from "jest-axe"

// jest-axe matcher — any test can call `await expect(container).toHaveNoViolations()`
// after rendering. Backstop for the kind of affordance / labelling regressions
// surfaced in the 2026-04-23 Settings UX walkthrough.
expect.extend(toHaveNoViolations)

// Global mock for sonner so any test that renders a component using
// toast (or the <Toaster /> in main.tsx) doesn't throw in jsdom.
vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    message: vi.fn(),
  },
  Toaster: () => null,
}))

// Ensure React Testing Library cleanup runs after every test and flush
// pending requestAnimationFrame callbacks to avoid jsdom teardown errors.
afterEach(() => {
  cleanup()
})

// Polyfill ResizeObserver for jsdom (required by Radix ScrollArea)
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof globalThis.ResizeObserver
}

// Polyfill Element.getBoundingClientRect for jsdom + @tanstack/react-virtual
// (Cycle 3.2 / v0.93.5).  jsdom returns a zero-size DOMRect by default,
// which makes the virtualizer think every item has zero height and
// stops it from rendering anything.
//
// Two shims here:
//   1. Per-row: when the element carries a data-index attribute (the
//      virtualizer's per-item marker), return a realistic 40px-tall box
//      so the virtualizer measures + positions the row correctly.
//   2. Scroll-viewport: when the element is the Radix ScrollArea
//      viewport (carries data-radix-scroll-area-viewport), report an
//      800x600 box so the virtualizer's visible-window calculation has
//      something to clip against.
// Also overrides ``clientHeight`` for the viewport — the virtualizer
// uses both APIs to compute the visible window.
//
// All other callers get the original zero-rect, preserving existing
// test behavior.
{
  const original = Element.prototype.getBoundingClientRect
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    const dataIndex = this.getAttribute && this.getAttribute("data-index")
    if (dataIndex !== null && dataIndex !== undefined) {
      const i = parseInt(dataIndex, 10)
      const height = 40
      const top = i * height
      return {
        x: 0, y: top, top, left: 0,
        right: 800, bottom: top + height,
        width: 800, height,
        toJSON: () => ({}),
      } as DOMRect
    }
    if (
      typeof this.hasAttribute === "function" &&
      this.hasAttribute("data-radix-scroll-area-viewport")
    ) {
      return {
        x: 0, y: 0, top: 0, left: 0,
        right: 800, bottom: 600,
        width: 800, height: 600,
        toJSON: () => ({}),
      } as DOMRect
    }
    return original.call(this)
  }
  // The virtualizer also reads clientHeight / clientWidth.  jsdom
  // defaults to 0 for both; shim them to match the viewport box above.
  Object.defineProperty(Element.prototype, "clientHeight", {
    configurable: true,
    get(): number {
      if (
        typeof this.hasAttribute === "function" &&
        this.hasAttribute("data-radix-scroll-area-viewport")
      ) {
        return 600
      }
      return 0
    },
  })
  Object.defineProperty(Element.prototype, "clientWidth", {
    configurable: true,
    get(): number {
      if (
        typeof this.hasAttribute === "function" &&
        this.hasAttribute("data-radix-scroll-area-viewport")
      ) {
        return 800
      }
      return 0
    },
  })
}

// Polyfill localStorage for test environments where it's a broken proxy
// (Node 22 ships localStorage but requires --localstorage-file to work)
{
  let broken = false
  try { localStorage.setItem("__test__", "1"); localStorage.removeItem("__test__") } catch { broken = true }
  if (broken) {
    const store = new Map<string, string>()
    const storage: Storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, String(value)) },
      removeItem: (key: string) => { store.delete(key) },
      clear: () => { store.clear() },
      key: (index: number) => [...store.keys()][index] ?? null,
      get length() { return store.size },
    }
    Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true })
  }
}
