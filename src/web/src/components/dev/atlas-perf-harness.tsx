// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas perf harness — dev-only route that renders Atlas against a
// synthetic graph fixture (NOT the production /graph/neighborhood
// endpoint) so we can measure frame rate without a live KB stack.
//
// Activation:
//   - Append `?dev=atlas-perf` to the app URL, OR
//   - Programmatically navigate to it from the dev-tier sidebar entry.
//
// Designed to be driven by Playwright (tests/perf/atlas-perf.spec.ts):
//   - FPS stats surfaced as `data-testid="atlas-fps"` for assertion.
//   - Camera pan animation can be triggered via `data-testid="pan-trigger"`.
//   - Lens stack toggle via `data-testid="enable-all-lenses"`.
//
// Reusable for Constellation (Phase B): the FPS meter + fixture
// generator + Playwright assertion harness all factor cleanly across
// 2D/3D renderers.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Sigma from "sigma"
import { adaptNeighborhood } from "@/lib/graph/graphology-adapter"
import { resolveMapTokens } from "@/lib/graph/identity"
import { applyLayout } from "@/lib/graph/apply-layout"
import { SURFACE_HEX } from "@/theme/shader-tokens"
import {
  ATLAS_DEFAULT_EDGE_TYPE,
  ATLAS_DEFAULT_NODE_TYPE,
  ATLAS_EDGE_PROGRAM_CLASSES,
  ATLAS_NODE_PROGRAM_CLASSES,
} from "@/lib/graph/programs"
import {
  generateSyntheticGraph,
  PERF_FIXTURE_SIZES,
  type PerfFixtureSize,
} from "@/lib/graph/fixtures/synthetic"
import { composeLenses, LENS_ORDER, LENS_REGISTRY, type LensId } from "@/lib/graph/lenses"
import { createFpsMeter, type FpsStats } from "@/lib/perf/fps-meter"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

type AtlasSigma = Sigma<AtlasNodeAttributes, AtlasEdgeAttributes>

interface HarnessStatus {
  state: "idle" | "generating" | "laying-out" | "ready" | "error"
  message?: string
  layoutMs?: number
  firstFrameMs?: number
  totalNodes?: number
  totalEdges?: number
}

interface RenderCost {
  /** Rolling median ms for the inner sigma.refresh() call (renderer-only) */
  medianMs: number
  /** p95 ms — tail behavior */
  p95Ms: number
  /** Last sampled value (raw) */
  lastMs: number
  /** Implied FPS if RAF were unlimited (1000/median) */
  impliedFps: number
}

export default function AtlasPerfHarness() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<AtlasSigma | null>(null)
  const rafRef = useRef<number | null>(null)
  const meterRef = useRef<ReturnType<typeof createFpsMeter> | null>(null)
  const [nodeCount, setNodeCount] = useState<PerfFixtureSize>(1000)
  const [status, setStatus] = useState<HarnessStatus>({ state: "idle" })
  const [fps, setFps] = useState<FpsStats | null>(null)
  const [renderCost, setRenderCost] = useState<RenderCost | null>(null)
  const [activeLenses, setActiveLenses] = useState<Set<LensId>>(new Set())
  const [seedKey, setSeedKey] = useState(0)

  const cleanup = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    meterRef.current?.stop()
    meterRef.current = null
    sigmaRef.current?.kill()
    sigmaRef.current = null
  }, [])

  // Build + render the fixture whenever (nodeCount, seedKey) changes.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let cancelled = false
    cleanup()

    setStatus({ state: "generating" })
    // Defer to next frame so the spinner paints
    const genTimer = window.setTimeout(() => {
      if (cancelled) return
      const t0 = performance.now()
      const fixture = generateSyntheticGraph({ nodes: nodeCount, seed: 42 + seedKey })
      const tokens = resolveMapTokens(document.documentElement)
      const graph = adaptNeighborhood(fixture, tokens)

      const sigma = new Sigma(graph, container, {
        renderLabels: nodeCount <= 1000,  // labels at scale tank FPS
        labelSize: 11,
        defaultNodeColor: SURFACE_HEX.graphiteFallback,
        defaultEdgeColor: "#3D4760", // drift-allowed: dev perf-harness Sigma color (exact-value baseline mirror of CartographerMap)
        labelColor: { color: "#A8B5C8" }, // drift-allowed: dev perf-harness Sigma color (exact-value baseline mirror of CartographerMap)
        nodeProgramClasses: ATLAS_NODE_PROGRAM_CLASSES,
        edgeProgramClasses: ATLAS_EDGE_PROGRAM_CLASSES,
        defaultNodeType: ATLAS_DEFAULT_NODE_TYPE,
        defaultEdgeType: ATLAS_DEFAULT_EDGE_TYPE,
      }) as unknown as AtlasSigma
      sigmaRef.current = sigma

      setStatus({
        state: "laying-out",
        totalNodes: graph.order,
        totalEdges: graph.size,
      })
      const layoutStart = performance.now()
      applyLayout(graph, {
        iterations: graph.order > 5000 ? 80 : graph.order > 1000 ? 120 : 200,
      })
        .then((res) => {
          if (cancelled) return
          const layoutMs = performance.now() - layoutStart
          sigmaRef.current?.refresh()

          // Spin up FPS meter on the sigma render loop. Sigma fires "render"
          // events per draw; we tick a RAF in parallel because sigma only
          // renders on dirty state. The RAF + sigma.refresh() loop below
          // forces continuous redraw so we measure steady-state cost.
          const meter = createFpsMeter({
            windowMs: 500,
            onWindow: (s) => setFps(s),
          })
          meterRef.current = meter

          const firstFrameTime = performance.now() - t0
          setStatus({
            state: "ready",
            layoutMs: Math.round(layoutMs),
            firstFrameMs: Math.round(firstFrameTime),
            totalNodes: res.nodeCount,
            totalEdges: graph.size,
          })

          // Renderer-cost ring buffer: independent of Chrome's RAF rate.
          // Chrome RAFs at 30Hz under macOS power-saving regardless of
          // renderer cost; tracking sigma.refresh() wall-clock surfaces
          // the true budget signal.
          const COST_BUFFER_SIZE = 60
          const costSamples: number[] = []
          let costIdx = 0
          const recordCost = (ms: number) => {
            if (costSamples.length < COST_BUFFER_SIZE) {
              costSamples.push(ms)
            } else {
              costSamples[costIdx] = ms
              costIdx = (costIdx + 1) % COST_BUFFER_SIZE
            }
          }

          const loop = () => {
            if (!sigmaRef.current) return
            const t0 = performance.now()
            sigmaRef.current.refresh()
            const elapsed = performance.now() - t0
            recordCost(elapsed)
            meter.tick()
            // Throttle setState to ~5Hz so React doesn't dominate the
            // measurement we're trying to take.
            if (meter.current() && costSamples.length >= 10 && (meter.current()?.windowsCompleted ?? 0) % 1 === 0) {
              const sorted = [...costSamples].sort((a, b) => a - b)
              const median = sorted[Math.floor(sorted.length / 2)]
              const p95 = sorted[Math.floor(sorted.length * 0.95)]
              setRenderCost({
                medianMs: Math.round(median * 10) / 10,
                p95Ms: Math.round(p95 * 10) / 10,
                lastMs: Math.round(elapsed * 10) / 10,
                impliedFps: median > 0 ? Math.round(Math.min(120, 1000 / median)) : 0,
              })
            }
            rafRef.current = requestAnimationFrame(loop)
          }
          rafRef.current = requestAnimationFrame(loop)
        })
        .catch((err) => {
          if (cancelled) return
          setStatus({
            state: "error",
            message: err instanceof Error ? err.message : "Layout failed",
          })
        })
    }, 16)

    return () => {
      cancelled = true
      window.clearTimeout(genTimer)
      cleanup()
    }
  }, [nodeCount, seedKey, cleanup])

  // Re-bind lens reducers on toggle
  useEffect(() => {
    const sigma = sigmaRef.current
    if (!sigma) return
    const graph = sigma.getGraph() as unknown as Parameters<typeof composeLenses>[1]
    const lenses = Array.from(activeLenses).map((id) => LENS_REGISTRY[id]).filter(Boolean)
    if (lenses.length === 0) {
      sigma.setSetting("nodeReducer", null)
      sigma.setSetting("edgeReducer", null)
    } else {
      const { nodeReducer, edgeReducer } = composeLenses(lenses, graph)
      sigma.setSetting("nodeReducer", nodeReducer)
      sigma.setSetting("edgeReducer", edgeReducer)
    }
  }, [activeLenses])

  const handleToggleLens = useCallback((id: LensId) => {
    setActiveLenses((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleEnableAllLenses = useCallback(() => {
    setActiveLenses(new Set(LENS_ORDER.map((l) => l.id)))
  }, [])

  // Drive a camera pan animation for steady-state measurement.
  // The Playwright spec triggers this and samples FPS during the pan.
  const handleCameraPan = useCallback(() => {
    const sigma = sigmaRef.current
    if (!sigma) return
    const camera = sigma.getCamera()
    const start = camera.getState()
    const ratio = start.ratio
    let phase = 0
    const totalMs = 3000
    const panStart = performance.now()
    const animate = () => {
      const elapsed = performance.now() - panStart
      if (elapsed >= totalMs) return
      phase = (elapsed / totalMs) * 2 * Math.PI
      camera.setState({
        x: start.x + Math.cos(phase) * 0.3,
        y: start.y + Math.sin(phase) * 0.3,
        ratio,
        angle: start.angle,
      })
      requestAnimationFrame(animate)
    }
    requestAnimationFrame(animate)
  }, [])

  const lensIds = useMemo(() => LENS_ORDER.map((l) => l.id), [])

  return (
    <div className="flex h-full flex-col">
      {/* Control bar */}
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b bg-card/40 px-4 py-2 text-sm">
        <span className="font-semibold">Atlas perf harness</span>
        <label className="flex items-center gap-2">
          <span className="text-muted-foreground">N nodes</span>
          <select
            data-testid="node-count-select"
            value={nodeCount}
            onChange={(e) => setNodeCount(Number(e.target.value) as PerfFixtureSize)}
            className="rounded border bg-background px-2 py-1"
          >
            {PERF_FIXTURE_SIZES.map((n) => (
              <option key={n} value={n}>{n.toLocaleString()}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => setSeedKey((k) => k + 1)}
          className="rounded border bg-background px-3 py-1 hover:bg-accent/40"
        >
          Regenerate
        </button>
        <button
          type="button"
          data-testid="pan-trigger"
          onClick={handleCameraPan}
          className="rounded border bg-background px-3 py-1 hover:bg-accent/40"
        >
          Pan camera
        </button>
        <button
          type="button"
          data-testid="enable-all-lenses"
          onClick={handleEnableAllLenses}
          className="rounded border bg-background px-3 py-1 hover:bg-accent/40"
        >
          Enable all lenses
        </button>
        <div className="flex items-center gap-1">
          {lensIds.map((id) => (
            <button
              key={id}
              type="button"
              data-testid={`lens-toggle-${id}`}
              onClick={() => handleToggleLens(id)}
              className={`rounded px-2 py-1 text-label-xs ${
                activeLenses.has(id)
                  ? "bg-accent text-accent-foreground"
                  : "bg-background text-foreground/70 hover:bg-accent/40"
              }`}
            >
              {id}
            </button>
          ))}
        </div>
        <div className="grow" />
        <div
          data-testid="atlas-fps"
          data-fps={fps?.avgFps ?? "n/a"}
          data-min-fps={fps?.minFps ?? "n/a"}
          data-frames={fps?.frames ?? 0}
          className="font-mono text-label-xs text-foreground"
        >
          {fps
            ? `${fps.avgFps.toFixed(1)} fps (min ${fps.minFps.toFixed(1)})`
            : "measuring…"}
        </div>
        <div
          data-testid="atlas-render-cost"
          data-median-ms={renderCost?.medianMs ?? "n/a"}
          data-p95-ms={renderCost?.p95Ms ?? "n/a"}
          data-implied-fps={renderCost?.impliedFps ?? "n/a"}
          className="font-mono text-label-xs text-muted-foreground"
        >
          {renderCost
            ? `${renderCost.medianMs.toFixed(1)}ms/frame · p95 ${renderCost.p95Ms.toFixed(1)}ms · ${renderCost.impliedFps} fps cap`
            : ""}
        </div>
      </div>

      {/* Status */}
      <div
        data-testid="atlas-status"
        data-status={status.state}
        data-layout-ms={status.layoutMs ?? ""}
        data-first-frame-ms={status.firstFrameMs ?? ""}
        data-total-nodes={status.totalNodes ?? ""}
        data-total-edges={status.totalEdges ?? ""}
        className="shrink-0 border-b bg-card/20 px-4 py-1 text-label-xs text-muted-foreground"
      >
        {status.state === "ready"
          ? `Ready — ${status.totalNodes} nodes / ${status.totalEdges} edges, layout ${status.layoutMs}ms, first frame ${status.firstFrameMs}ms`
          : status.state === "error"
            ? `Error: ${status.message}`
            : `${status.state}…`}
      </div>

      {/* Canvas */}
      <div className="relative grow overflow-hidden">
        <div ref={containerRef} className="h-full w-full bg-background" />
      </div>
    </div>
  )
}
