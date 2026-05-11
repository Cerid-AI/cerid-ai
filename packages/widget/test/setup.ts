// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vitest global test setup.
 *
 * jsdom's CustomElementRegistry doesn't support re-defining elements.
 * We guard all definitions with customElements.get() checks in tests,
 * but this file handles the base environment setup.
 */

// window.matchMedia is not implemented in jsdom — stub it.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// AbortSignal.timeout is not in jsdom < 21 — polyfill it.
if (!("timeout" in AbortSignal)) {
  Object.defineProperty(AbortSignal, "timeout", {
    value: (ms: number) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException("TimeoutError", "TimeoutError")), ms);
      return controller.signal;
    },
    writable: false,
    configurable: true,
  });
}

// AbortSignal.any polyfill for jsdom
if (!("any" in AbortSignal)) {
  Object.defineProperty(AbortSignal, "any", {
    value: (signals: AbortSignal[]) => {
      const controller = new AbortController();
      for (const signal of signals) {
        if (signal.aborted) {
          controller.abort(signal.reason);
          return controller.signal;
        }
        signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
      }
      return controller.signal;
    },
    writable: false,
    configurable: true,
  });
}
