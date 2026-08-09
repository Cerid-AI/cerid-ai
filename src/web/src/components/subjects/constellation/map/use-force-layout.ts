// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
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

// Short warm: a brief organic settle out of the server seed, then the sim
// freezes (see refreshLoop). The server layout is already a filled disc; running
// FA2 to convergence pulls this hub-and-leaf corpus into a hollow ring (its FA2
// equilibrium — the ~90% degree-1 tail gets repelled to the rim). So we warm
// just long enough to feel alive on load, then lock positions to keep the disc.
const DEFAULT_WARM_MS = 700

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
    if (performance.now() >= warmUntilRef.current) {
      // Warm elapsed — freeze. A continuously-running FA2 drifts the disc-filled
      // server seed into a hollow ring, so we lock positions after the brief
      // settle. reheat() (drag end, data change, reveal) starts a fresh warm.
      layout.stop()
      s.refresh()
      rafRef.current = null
      return
    }
    s.refresh({ skipIndexation: true })
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
