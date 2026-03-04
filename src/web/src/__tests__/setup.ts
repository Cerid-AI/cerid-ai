// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import "@testing-library/jest-dom/vitest"

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
