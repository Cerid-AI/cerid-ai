// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Radix open-delay timers can be orphaned the moment they are created, and an
// orphaned one takes down a whole suite that otherwise passed.
//
// `HoverCard` and `Tooltip` open on BOTH pointerenter and focus, and their
// `handleOpen` overwrites `openTimerRef.current` WITHOUT clearing the timer it
// replaces (@radix-ui/react-hover-card 1.1.23, dist/index.mjs:48). A single
// `userEvent.click` fires both events, so the first timer loses its only
// reference immediately — React's unmount cleanup can then clear the ref's
// last value and nothing else. Verified directly: one click creates two 300ms
// timers and unmount clears exactly one of them.
//
// Why that is not merely untidy: vitest tears down the jsdom environment when
// a test file finishes, while the worker process lives on. When the orphan
// fires after that teardown, React reads a `window` that no longer exists and
// vitest records an uncaught `ReferenceError: window is not defined`. On
// 2026-08-10 that failed the `frontend` job on main with 227/227 files and
// 2749/2749 tests passing — an unhandled error alone exits 1. It is a race
// against teardown, so it surfaces under CI load and not on an idle box.
//
// After RTL's `cleanup()` nothing is mounted, so a timer still pending is
// unobservable by definition — no assertion can depend on one. Sweeping them
// is therefore safe, and `sweepOrphanedTimers` is exported so the invariant
// can be asserted in a test rather than living only inside an `afterEach`.

const pending = new Map<ReturnType<typeof setTimeout>, string>()

let realClearTimeout: typeof globalThis.clearTimeout = globalThis.clearTimeout

// Ownership is derived from this file's own path, never a hardcoded pattern.
// A literal `/src/` test also matches the repo's own `src/web/` path segment,
// which claims every timer in the process — including vitest's and Node's,
// which must never be swept. An early cut did exactly that and swept 1399
// timers instead of 81.
const SELF = new URL(import.meta.url).pathname
// The app source root — the parent of this file's `__tests__` directory, so
// component timers are claimed too, not just ones created under `__tests__`.
const APP_SRC = SELF.slice(0, SELF.indexOf("/__tests__/"))

/**
 * Whether a captured stack belongs to app or Radix code, and is therefore
 * safe to sweep once nothing is mounted.
 *
 * This file's own frames are ignored: the `setTimeout` wrapper below appears
 * in every stack it captures, so counting them would claim everything.
 */
export function isAppOwnedStack(stack: string): boolean {
  return stack
    .split("\n")
    .some(
      (line) =>
        !line.includes(SELF) &&
        (line.includes(APP_SRC) || line.includes("node_modules/@radix-ui")),
    )
}

/**
 * Track every timer created by app or Radix code so orphans can be swept after
 * unmount. Call once, from the vitest setup file.
 */
export function installOrphanedTimerTracking(): void {
  const realSetTimeout = globalThis.setTimeout
  realClearTimeout = globalThis.clearTimeout

  globalThis.setTimeout = ((handler: never, ms?: number, ...rest: never[]) => {
    const id = realSetTimeout(handler, ms, ...rest)
    if ((ms ?? 0) > 0) {
      const stack = new Error().stack ?? ""
      if (isAppOwnedStack(stack)) pending.set(id, stack)
    }
    return id
  }) as typeof globalThis.setTimeout

  globalThis.clearTimeout = ((id: never) => {
    pending.delete(id)
    return realClearTimeout(id)
  }) as typeof globalThis.clearTimeout
}

/**
 * Clear every tracked timer still pending. Call AFTER `cleanup()`, when
 * nothing is mounted and no surviving timer can be observed.
 *
 * @returns the stack of each timer swept, so callers can assert on them.
 */
export function sweepOrphanedTimers(): string[] {
  const swept = [...pending.values()]
  for (const id of pending.keys()) realClearTimeout(id)
  pending.clear()
  return swept
}
