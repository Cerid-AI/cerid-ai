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
