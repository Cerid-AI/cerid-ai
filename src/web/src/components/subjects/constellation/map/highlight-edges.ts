// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Highlight-edge overlay: draws the focus node's incident edges ABOVE the node
// mesh on a dedicated canvas synced to the Sigma camera (afterRender). Sigma's
// own edge program never raises edges over nodes; this layer fills that gap.
//
// The overlay must trace the SAME quadratic bezier @sigma/edge-curve renders
// (per-edge `curvature` from applyParallelEdgeCurvature) — straight chords
// visibly de-link from curved/fanned edges mid-span on pan/zoom.

import { useEffect, useRef } from "react"
import type Sigma from "sigma"
import type { MapTokens } from "./community-layer"

export interface EdgeSegment {
  /** Edge SOURCE endpoint (graph space) — order matters: curvature sign is relative to source→target. */
  x1: number
  y1: number
  /** Edge TARGET endpoint (graph space). */
  x2: number
  y2: number
  /** @sigma/edge-curve curvature attribute; 0 = straight. */
  curvature: number
}

/**
 * Quadratic-bezier control point in viewport (canvas, y-down) coordinates.
 * Replicates @sigma/edge-curve's vertex shader, which places the control
 * point at the segment midpoint offset along the source→target normal by
 * length·curvature (v_cpB = mid + unitNormal·len·curvature, computed in GL
 * y-up viewport space — the y-flip to canvas space yields the signs below;
 * the package's own canvas label drawer uses this exact form). The transform
 * graph→viewport is a similarity, so computing from projected endpoints is
 * exactly the curve the edge program rasterizes.
 */
export function curveControlPoint(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  curvature: number,
): { x: number; y: number } {
  return {
    x: (x1 + x2) / 2 + (y2 - y1) * curvature,
    y: (y1 + y2) / 2 - (x2 - x1) * curvature,
  }
}

interface SegmentGraph {
  hasNode(id: string): boolean
  forEachEdge(
    node: string,
    cb: (edge: string, attrs: Record<string, unknown>, source: string, target: string) => void,
  ): void
  getNodeAttribute(id: string, k: string): unknown
}

export function incidentEdgeSegments(graph: SegmentGraph, focusCenter: string | null): EdgeSegment[] {
  if (!focusCenter || !graph.hasNode(focusCenter)) return []
  const segs: EdgeSegment[] = []
  // Per-EDGE (not per-neighbor): parallel edges fan with distinct curvatures,
  // and source→target order must be preserved for the curvature sign.
  graph.forEachEdge(focusCenter, (_edge, attrs, source, target) => {
    segs.push({
      x1: graph.getNodeAttribute(source, "x") as number,
      y1: graph.getNodeAttribute(source, "y") as number,
      x2: graph.getNodeAttribute(target, "x") as number,
      y2: graph.getNodeAttribute(target, "y") as number,
      curvature: typeof attrs.curvature === "number" ? attrs.curvature : 0,
    })
  })
  return segs
}

export function useHighlightEdges(args: {
  sigma: Sigma | null
  tokens: MapTokens
  getFocusCenter: () => string | null
  /** 0..1 eased focus strength — fades the highlight in/out (default 1). */
  getFocusProgress?: () => number
}): void {
  const { sigma, tokens, getFocusCenter, getFocusProgress } = args
  const getFocusProgressRef = useRef(getFocusProgress)
  getFocusProgressRef.current = getFocusProgress
  useEffect(() => {
    if (!sigma) return
    const container = sigma.getContainer()
    const canvas = document.createElement("canvas")
    canvas.setAttribute("data-cartographer-highlight-edges", "")
    canvas.style.position = "absolute"
    canvas.style.inset = "0"
    canvas.style.pointerEvents = "none"
    // Above the node layer; below the DOM tooltip/cards (which are z-50).
    canvas.style.zIndex = "2"
    container.appendChild(canvas)
    const ctx = canvas.getContext("2d")

    const draw = () => {
      if (!ctx) return
      const dpr = window.devicePixelRatio || 1
      const { width, height } = container.getBoundingClientRect()
      canvas.width = width * dpr
      canvas.height = height * dpr
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)
      const focus = getFocusCenter()
      const graph = sigma.getGraph()
      const segs = incidentEdgeSegments(graph as never, focus)
      if (segs.length === 0) return
      const progress = getFocusProgressRef.current?.() ?? 1
      if (progress <= 0.01) return
      ctx.lineWidth = 1.6
      ctx.strokeStyle = tokens.interaction
      ctx.globalAlpha = 0.9 * progress
      for (const s of segs) {
        const p1 = sigma.graphToViewport({ x: s.x1, y: s.y1 })
        const p2 = sigma.graphToViewport({ x: s.x2, y: s.y2 })
        ctx.beginPath()
        ctx.moveTo(p1.x, p1.y)
        if (s.curvature !== 0) {
          const cp = curveControlPoint(p1.x, p1.y, p2.x, p2.y, s.curvature)
          ctx.quadraticCurveTo(cp.x, cp.y, p2.x, p2.y)
        } else {
          ctx.lineTo(p2.x, p2.y)
        }
        ctx.stroke()
      }
      ctx.globalAlpha = 1
    }

    sigma.on("afterRender", draw)
    return () => {
      sigma.off("afterRender", draw)
      canvas.remove()
    }
  }, [sigma, tokens, getFocusCenter])
}
