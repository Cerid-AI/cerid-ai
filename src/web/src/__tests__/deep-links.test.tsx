// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// The renderer half of `cerid://` routing. Every item Cerid donates to
// Spotlight carries a `cerid://kb/<artifact-id>` contentURL and the scheme has
// been registered since Phase G, so a click has always launched or raised the
// app — and then shown whatever pane the user had left open, which is
// indistinguishable from a link that worked.
//
// What is pinned here is that a queued link reaches `goTo`, because a bridge
// that is subscribed to but never drained looks identical from the outside.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { render, waitFor } from "@testing-library/react"
import { NavigationProvider } from "@/contexts/navigation-context"
import { DeepLinkRouter } from "@/components/layout/deep-link-router"

type Link = { kind: string; id: string }

function installBridge(queued: Link[]) {
  const consume = vi.fn().mockImplementation(() => Promise.resolve(queued.splice(0, queued.length)))
  const unsubscribe = vi.fn()
  let fire: (() => void) | null = null
  const onAvailable = vi.fn().mockImplementation((cb: () => void) => {
    fire = cb
    return unsubscribe
  })
  ;(window as unknown as { cerid: object }).cerid = { deepLinks: { consume, onAvailable } }
  return { consume, onAvailable, unsubscribe, fireAvailable: () => fire?.() }
}

function renderRouter(onPaneChange = vi.fn()) {
  render(
    <NavigationProvider activePane="chat" onPaneChange={onPaneChange}>
      <DeepLinkRouter />
    </NavigationProvider>,
  )
  return onPaneChange
}

beforeEach(() => {
  window.history.replaceState({}, "", "/")
})

afterEach(() => {
  delete (window as unknown as { cerid?: object }).cerid
})

describe("DeepLinkRouter", () => {
  it("drains at mount, which is the only path a cold launch has", async () => {
    // On a cold launch macOS delivers open-url before this bundle has parsed,
    // so there is no event to subscribe to — the link is already in the main
    // process's queue and mount is what collects it. A subscription-only
    // implementation passes every warm test and never works from Spotlight.
    const { consume } = installBridge([{ kind: "artifact", id: "abc123" }])
    const onPaneChange = renderRouter()

    await waitFor(() => expect(consume).toHaveBeenCalled())
    await waitFor(() => expect(onPaneChange).toHaveBeenCalledWith("sources"))
    expect(new URLSearchParams(window.location.search).get("artifact")).toBe("abc123")
  })

  it("navigates on a link that arrives while the app is running", async () => {
    const queued: Link[] = []
    const { fireAvailable } = installBridge(queued)
    const onPaneChange = renderRouter()
    await waitFor(() => expect(onPaneChange).not.toHaveBeenCalled())

    queued.push({ kind: "artifact", id: "later" })
    fireAvailable()

    await waitFor(() => expect(onPaneChange).toHaveBeenCalledWith("sources"))
    expect(new URLSearchParams(window.location.search).get("artifact")).toBe("later")
  })

  it("navigates to the most recent of several queued links", async () => {
    // Someone who clicked three results while the app was starting means the
    // last one; replaying all three flashes through the others to get there.
    installBridge([
      { kind: "artifact", id: "first" },
      { kind: "artifact", id: "second" },
      { kind: "artifact", id: "third" },
    ])
    const onPaneChange = renderRouter()

    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("artifact")).toBe("third"),
    )
    expect(onPaneChange).toHaveBeenCalledTimes(1)
  })

  it("does not navigate when nothing is queued", async () => {
    const { consume } = installBridge([])
    const onPaneChange = renderRouter()

    await waitFor(() => expect(consume).toHaveBeenCalled())
    expect(onPaneChange).not.toHaveBeenCalled()
    expect(window.location.search).toBe("")
  })

  it("ignores a link kind it does not route", async () => {
    installBridge([{ kind: "something-else", id: "x" }])
    const onPaneChange = renderRouter()

    await waitFor(() => expect(onPaneChange).not.toHaveBeenCalled())
    expect(window.location.search).toBe("")
  })

  it("is inert in a browser build, where there is no bridge", async () => {
    const onPaneChange = renderRouter()
    await waitFor(() => expect(onPaneChange).not.toHaveBeenCalled())
  })

  it("unsubscribes on unmount", async () => {
    const { unsubscribe } = installBridge([])
    const { unmount } = render(
      <NavigationProvider activePane="chat" onPaneChange={vi.fn()}>
        <DeepLinkRouter />
      </NavigationProvider>,
    )
    unmount()
    expect(unsubscribe).toHaveBeenCalled()
  })
})
