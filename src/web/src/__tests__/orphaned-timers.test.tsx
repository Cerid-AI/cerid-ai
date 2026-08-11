// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Pins the invariant behind ./orphaned-timers.ts: after RTL unmounts, no timer
// created by app or Radix code is left able to run.
//
// The failure this guards against is a race — an orphaned Radix open-delay
// timer firing after vitest tears down the jsdom environment, which exits the
// run 1 with an uncaught "window is not defined" while every test passes. That
// race cannot be reproduced on demand (it needs teardown to land inside the
// 300ms window, which is why it surfaced on a loaded CI runner and not
// locally). So these assert the *cause*: the orphan is real, and the sweep
// stops its callback from ever running.
//
// Each test here was checked against a deliberately broken sweep. An earlier
// draft asserted only what `sweepOrphanedTimers` RETURNED, and stayed green
// with the `clearTimeout` call deleted — bookkeeping is not the guarantee.

import { describe, it, expect, vi } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ReactMarkdown from "react-markdown"
import { buildLinkifyComponents } from "@/components/wiki/linkify"
import { sweepOrphanedTimers, isAppOwnedStack } from "./orphaned-timers"
import type { LinkifyEntity } from "@/components/wiki/linkify"

// A real path inside the app source root, built the same way the tracker
// derives it — hardcoding one would drift the moment the tree moves.
const APP_FILE = (() => {
  const self = new URL(import.meta.url).pathname
  return `${self.slice(0, self.indexOf("/__tests__/"))}/components/wiki/linkify.tsx`
})()

// The tracker module itself — its frames must never count as ownership.
// Derived by string surgery, not `new URL("./x", import.meta.url)`: Vite
// rewrites that two-argument form into an asset path at build time, which
// yielded a bare "/src/__tests__/orphaned-timers.ts" that matched nothing and
// left this test passing against a broken predicate.
const TRACKER_FILE = new URL(import.meta.url).pathname.replace(
  /orphaned-timers\.test\.tsx$/,
  "orphaned-timers.ts",
)

const ENTITIES: LinkifyEntity[] = [
  {
    slug: "python",
    name: "Python",
    entity_type: "OTHER",
    has_summary: true,
    one_liner: "High-level programming language.",
  },
]

/** Records whether each Radix open-delay callback ever executes. */
function watchOpenDelayTimers(): { ran: boolean }[] {
  const markers: { ran: boolean }[] = []
  const inner = globalThis.setTimeout
  globalThis.setTimeout = ((handler: never, ms?: number, ...rest: never[]) => {
    if (ms === 300 && typeof handler === "function") {
      const marker = { ran: false }
      markers.push(marker)
      const fn = handler as unknown as () => void
      return inner(
        (() => {
          marker.ran = true
          fn()
        }) as never,
        ms,
        ...rest,
      )
    }
    return inner(handler, ms, ...rest)
  }) as typeof globalThis.setTimeout
  return markers
}

async function clickLinkifiedEntity(): Promise<void> {
  const user = userEvent.setup()
  render(
    <ReactMarkdown components={buildLinkifyComponents({ entities: ENTITIES, onSelect: vi.fn() })}>
      {"Python is great."}
    </ReactMarkdown>,
  )
  await user.click(screen.getByRole("button", { name: "Navigate to Python" }))
}

describe("orphaned Radix timers", () => {
  it("one click on a HoverCard trigger leaves a timer unmount cannot reach", async () => {
    // Radix's HoverCard opens on pointerenter AND on focus, and `handleOpen`
    // overwrites openTimerRef without clearing what it replaces — so React's
    // unmount cleanup clears the last timer and the earlier one survives with
    // no reference to it anywhere.
    await clickLinkifiedEntity()
    cleanup()

    const swept = sweepOrphanedTimers()
    expect(
      swept.filter((stack) => stack.includes("node_modules/@radix-ui/react-hover-card")).length,
    ).toBeGreaterThan(0)
  })

  it("the sweep stops the orphaned callback from running", async () => {
    const realSetTimeout = globalThis.setTimeout
    const markers = watchOpenDelayTimers()
    try {
      await clickLinkifiedEntity()
      cleanup()
      sweepOrphanedTimers()
    } finally {
      globalThis.setTimeout = realSetTimeout
    }

    // The bug is only interesting if unmount really did leave more than one.
    expect(markers.length).toBeGreaterThan(1)
    await new Promise((resolve) => realSetTimeout(resolve, 400))
    expect(markers.filter((m) => m.ran)).toEqual([])
  })

  it("claims Radix and app frames, never the runner's own", () => {
    const appFrame = `    at foo (${APP_FILE}:12:3)`
    const radixFrame = "    at bar (/repo/src/web/node_modules/@radix-ui/react-tooltip/dist/index.mjs:124:35)"
    // Lives under the repo's `src/web/` path, so a naive /src/ test claims it.
    const runnerFrame = "    at baz (/repo/src/web/node_modules/vitest/dist/chunks/runtime.js:9:1)"

    expect(isAppOwnedStack(`Error\n${appFrame}`)).toBe(true)
    expect(isAppOwnedStack(`Error\n${radixFrame}`)).toBe(true)
    expect(isAppOwnedStack(`Error\n${runnerFrame}`)).toBe(false)
    expect(isAppOwnedStack("Error\n    at nowhere (node:internal/timers:1:1)")).toBe(false)

    // Every real captured stack begins inside the tracker's own wrapper, and
    // the tracker lives under the app source root. Counting that frame makes
    // the predicate claim every timer in the process, so it must be ignored —
    // this is the shape a hand-built frame list does not otherwise produce.
    const trackerFrame = `    at Object.setTimeout (${TRACKER_FILE}:60:24)`
    expect(isAppOwnedStack(`Error\n${trackerFrame}\n${runnerFrame}`)).toBe(false)
    expect(isAppOwnedStack(`Error\n${trackerFrame}\n${appFrame}`)).toBe(true)
  })
})
