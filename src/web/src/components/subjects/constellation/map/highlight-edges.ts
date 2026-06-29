// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Highlight-edge overlay: draws the focus node's incident edges ABOVE the node
// mesh on a dedicated canvas synced to the Sigma camera (afterRender). Sigma's
// own edge program never raises edges over nodes; this layer fills that gap.

import { useEffect, useRef } from "react"
import type Sigma from "sigma"
import type { MapTokens } from "./community-layer"

export interface EdgeSegment {
  x1: number
  y1: number
  x2: number
  y2: number
}

export function incidentEdgeSegments(
  graph: { hasNode: (id: string) => boolean; forEachNeighbor: (id: string, cb: (n: string) => void) => void; getNodeAttribute: (id: string, k: string) => unknown },
  focusCenter: string | null,
): EdgeSegment[] {
  if (!focusCenter || !graph.hasNode(focusCenter)) return []
  const x1 = graph.getNodeAttribute(focusCenter, "x") as number
  const y1 = graph.getNodeAttribute(focusCenter, "y") as number
  const segs: EdgeSegment[] = []
  graph.forEachNeighbor(focusCenter, (n) => {
    segs.push({
      x1,
      y1,
      x2: graph.getNodeAttribute(n, "x") as number,
      y2: graph.getNodeAttribute(n, "y") as number,
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
        ctx.lineTo(p2.x, p2.y)
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
