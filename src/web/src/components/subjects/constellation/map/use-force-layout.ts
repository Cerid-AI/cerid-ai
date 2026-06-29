// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Live ForceAtlas2 layout for the 2D Cartographer map. Runs FA2 in a Web
// Worker (off main thread) over the SAME graphology graph Sigma renders, so
// positions mutate in place — never reconstructs Sigma. The server layout is
// the seed; FA2 warms it, then idles at low energy ("breathing"). Reheats on
// graph change; pauses during drag.

import { useCallback, useEffect, useRef } from "react"
import type Sigma from "sigma"
import FA2Layout from "graphology-layout-forceatlas2/worker"
import { buildForceSettings, shouldRunLayout } from "./force-settings"

export interface ForceLayoutController {
  reheat: () => void
  pause: () => void
  resume: () => void
  isRunning: () => boolean
}

// Long enough for the seed to actually migrate into affinity clusters under the
// (now lighter-gravity, linLog) settings before settling to the idle trickle.
const DEFAULT_WARM_MS = 5000
// Refresh cadence: full rAF while warming; throttled while idle-breathing so a
// continuously-running worker doesn't burn the main thread.
const IDLE_REFRESH_INTERVAL_MS = 1000 / 30

export function useForceLayout(args: {
  sigma: Sigma | null
  enabled: boolean
  reducedMotion: boolean
  warmMs?: number
  /** When this returns true the sim stays paused — reheat()/resume() no-op.
   *  Used to keep FA2 idle while communities are collapsed (members hidden). */
  shouldStayPaused?: () => boolean
}): ForceLayoutController {
  const { sigma, enabled, reducedMotion, warmMs = DEFAULT_WARM_MS, shouldStayPaused } = args
  const shouldStayPausedRef = useRef(shouldStayPaused)
  shouldStayPausedRef.current = shouldStayPaused
  const layoutRef = useRef<FA2Layout | null>(null)
  const rafRef = useRef<number | null>(null)
  const lastRefreshRef = useRef(0)
  const warmUntilRef = useRef(0)

  const stopRefreshLoop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [])

  const refreshLoop = useCallback(() => {
    const s = sigma
    const layout = layoutRef.current
    if (!s || !layout || !layout.isRunning()) {
      rafRef.current = null
      return
    }
    const now = performance.now()
    const warming = now < warmUntilRef.current
    if (warming || now - lastRefreshRef.current >= IDLE_REFRESH_INTERVAL_MS) {
      s.refresh({ skipIndexation: true })
      lastRefreshRef.current = now
    }
    rafRef.current = requestAnimationFrame(refreshLoop)
  }, [sigma])

  const startRefreshLoop = useCallback(() => {
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(refreshLoop)
    }
  }, [refreshLoop])

  const reheat = useCallback(() => {
    // Stay paused while collapsed — no point simulating a fully-hidden mesh.
    // Single enforced invariant covering every reheat owner (drag, data
    // change, filter reveal, expand). On expand, collapsedRef is already false
    // before this fires, so reheat proceeds normally.
    if (shouldStayPausedRef.current?.()) return
    const layout = layoutRef.current
    if (!layout) return
    warmUntilRef.current = performance.now() + warmMs
    if (!layout.isRunning()) layout.start()
    startRefreshLoop()
  }, [warmMs, startRefreshLoop])

  const pause = useCallback(() => {
    layoutRef.current?.stop()
    stopRefreshLoop()
    // Reindex once after motion stops so hit-testing is accurate at rest.
    sigma?.refresh()
  }, [sigma, stopRefreshLoop])

  const resume = useCallback(() => reheat(), [reheat])

  const isRunning = useCallback(() => layoutRef.current?.isRunning() ?? false, [])

  // Create/destroy the supervisor with the sigma instance + enablement.
  useEffect(() => {
    if (!sigma) return
    const graph = sigma.getGraph()
    const run = shouldRunLayout({
      reducedMotion,
      liveLayout: enabled,
      nodeCount: graph.order,
    })
    if (!run) return

    const layout = new FA2Layout(graph, {
      settings: buildForceSettings(graph.order),
    })
    layoutRef.current = layout
    warmUntilRef.current = performance.now() + warmMs
    layout.start()
    startRefreshLoop()

    return () => {
      stopRefreshLoop()
      layout.kill()
      layoutRef.current = null
    }
  }, [sigma, enabled, reducedMotion, warmMs, startRefreshLoop, stopRefreshLoop])

  return { reheat, pause, resume, isRunning }
}
