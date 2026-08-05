// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Community hull + label canvas overlay for the Cartographer map.
//
// Draws Leiden community alpha-shape hulls and cartographic region labels
// on a 2D-canvas overlay synced to the sigma camera via `graphToViewport`.
// Registered via sigma's `afterRender` event so it redraws every frame.
//
// LOD contract:
//   far  (cameraRatio >= FAR_THRESHOLD): hulls fully visible + region labels
//   mid  (MID_THRESHOLD .. FAR_THRESHOLD): hulls at 10% alpha, labels fade
//   near (cameraRatio < MID_THRESHOLD): hulls at 4% alpha, labels gone

import { useEffect, useRef } from "react"
import type Sigma from "sigma"
import type { CommunityHull } from "@/lib/api/graph-map"
import { selectVisibleLabels, type LabelRect } from "./label-collision"

interface CommunityLayerProps {
  sigma: Sigma | null
  communities: CommunityHull[]
  /** CSS custom property resolved values for the current theme */
  tokens: MapTokens
  /** Whether hull fills/labels are enabled at all */
  hullsVisible: boolean
  /** Camera ratio above which the far-zoom state is active */
  farThreshold?: number
  /** Camera ratio below which entities are near-zoom */
  nearThreshold?: number
  /** Called when a community hull is clicked */
  onCommunityClick?: (community: CommunityHull) => void
  /** Optional per-region colour override (e.g. domain colour for domain-layout
   *  territories). Falls back to the community cluster palette when absent. */
  colorFor?: (id: string) => string
}

export interface MapTokens {
  /** CSS color string for cluster hue 0..7 + "other" */
  clusters: string[]
  clusterOther: string
  /** CSS color strings for domain hue 0..11 (resolved OKLCH→hex via canvas readback).
   *  DOM surfaces use var(--color-domain-N) directly; only sigma/canvas/WebGL paths
   *  go through resolveMapTokens(). Non-optional: TS structural typing guards stubs. */
  domains: string[]
  domainOther: string
  edge: string
  dim: string
  interaction: string
  /** Foreground color for label text */
  foreground: string
  background: string
  /** Trust-band colors (neutral ramp from index.css) */
  trustVerified: string
  trustPartial: string
  trustUnverified: string
  /** Isolated-node sentinel: fixed neutral graphite, distinct from community colors.
   *  Used when community_id === "isolated" in the cluster lens. */
  graphite: string
  /** Optional dot-grid underlay color */
  grid: string
  /** Resolved font-family string for canvas 2D (var() stripped).
   *  Present after resolveMapTokens(); absent in SSR/test stubs — callers fall back to labelFont. */
  fontSans?: string
}

// Normalize any CSS color (oklch, hsl, named…) to #rrggbb via a 1×1
// canvas pixel readback. Sigma's WebGL color parser only understands
// hex/rgb — feeding it raw oklch() token strings renders BLACK nodes.
let _normCtx: CanvasRenderingContext2D | null = null
function normalizeColor(cssColor: string): string {
  if (!cssColor) return "#888888" // drift-allowed: neutral fallback for missing CSS var; never reaches the design system
  if (cssColor.startsWith("#")) return cssColor
  if (!_normCtx) {
    const canvas = document.createElement("canvas")
    canvas.width = canvas.height = 1
    _normCtx = canvas.getContext("2d", { willReadFrequently: true })
  }
  const ctx = _normCtx
  if (!ctx) return cssColor
  ctx.clearRect(0, 0, 1, 1)
  ctx.fillStyle = cssColor
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`
}

// Resolve the map palette CSS tokens from the live document.
// Called once at mount and re-called on theme change.
export function resolveMapTokens(root: Element): MapTokens {
  const style = getComputedStyle(root)
  const get = (name: string) => normalizeColor(style.getPropertyValue(name).trim())
  // Resolve font-family as a plain string — canvas ctx.font cannot parse var().
  const rawFont = style.getPropertyValue("--font-sans").trim()
  const fontSans = rawFont.replace(/^['"]|['"]$/g, "") || "system-ui, sans-serif"
  return {
    clusters: [0, 1, 2, 3, 4, 5, 6, 7].map((i) =>
      get(`--color-map-cluster-${i}`)
    ),
    clusterOther: get("--color-map-cluster-other"),
    domains: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) =>
      get(`--color-domain-${i}`)
    ),
    domainOther: get("--color-domain-other"),
    edge: get("--color-map-edge"),
    dim: get("--color-map-dim"),
    interaction: get("--color-map-interaction"),
    foreground: get("--foreground"),
    background: get("--background"),
    trustVerified: get("--color-map-trust-verified"),
    trustPartial: get("--color-map-trust-partial"),
    trustUnverified: get("--color-map-trust-unverified"),
    graphite: get("--color-map-graphite"),
    grid: get("--color-map-grid"),
    fontSans,
  }
}

// Stable hash for assigning community ids to palette slots.
function communitySlot(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash) % 8
}

const FAR_DEFAULT = 2.0
const NEAR_DEFAULT = 0.4

/**
 * Draws a smoothed closed path for a hull polygon.
 * Uses Chaikin smoothing (one pass) for softer edges.
 */
function drawHullPath(ctx: CanvasRenderingContext2D, pts: [number, number][]): void {
  if (pts.length < 3) return
  ctx.beginPath()
  const n = pts.length
  const mid = (a: [number, number], b: [number, number]): [number, number] =>
    [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
  const startMid = mid(pts[n - 1], pts[0])
  ctx.moveTo(startMid[0], startMid[1])
  for (let i = 0; i < n; i++) {
    const curr = pts[i]
    const next = pts[(i + 1) % n]
    const m1 = mid(curr, next)
    ctx.quadraticCurveTo(curr[0], curr[1], m1[0], m1[1])
  }
  ctx.closePath()
}

/**
 * Hook that registers a canvas overlay on the sigma instance and redraws
 * community hulls + labels after every sigma render pass.
 */
export function useCommunityLayer({
  sigma,
  communities,
  tokens,
  hullsVisible,
  farThreshold = FAR_DEFAULT,
  nearThreshold = NEAR_DEFAULT,
  onCommunityClick,
  colorFor,
}: CommunityLayerProps): void {
  const onCommunityClickRef = useRef(onCommunityClick)
  onCommunityClickRef.current = onCommunityClick
  const colorForRef = useRef(colorFor)
  colorForRef.current = colorFor

  useEffect(() => {
    if (!sigma) return
    // Capture non-null reference so closures below don't need re-checks.
    const s = sigma

    // Obtain or create the overlay canvas. Sigma exposes getCanvases() which
    // returns the internal canvas layers. We piggyback a custom overlay on top.
    const container = s.getContainer()
    let canvas = container.querySelector<HTMLCanvasElement>(
      "canvas[data-cartographer-community]"
    )
    if (!canvas) {
      canvas = document.createElement("canvas")
      canvas.dataset.cartographerCommunity = "1"
      canvas.style.position = "absolute"
      canvas.style.inset = "0"
      canvas.style.pointerEvents = "none"
      // Prepend so the nebula fill sits BEHIND sigma's node/edge canvases
      // (which are z-index:auto and stack in DOM order) — nodes float on top of
      // their shaded territory rather than being hazed over by it.
      container.insertBefore(canvas, container.firstChild)
    }

    const cvs = canvas

    function resize() {
      const w = container.offsetWidth
      const h = container.offsetHeight
      const dpr = window.devicePixelRatio || 1
      cvs.width = w * dpr
      cvs.height = h * dpr
      cvs.style.width = `${w}px`
      cvs.style.height = `${h}px`
    }

    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)

    function draw() {
      const ctx = cvs.getContext("2d")
      if (!ctx) return
      const dpr = window.devicePixelRatio || 1
      ctx.clearRect(0, 0, cvs.width, cvs.height)
      ctx.save()
      ctx.scale(dpr, dpr)

      const cameraRatio = s.getCamera().ratio
      const isFar = cameraRatio >= farThreshold
      const isNear = cameraRatio < nearThreshold

      if (!hullsVisible) {
        ctx.restore()
        return
      }

      // Nebula fill alpha (peak, at the anchor — fades to 0 at the hull edge via
      // the radial gradient below). Bold enough to read as a shaded territory
      // behind the nodes; eases as you zoom in so detail work isn't tinted.
      // far=44%, mid=30%, near=14%.
      const fillAlpha = isFar ? 0.44 : isNear ? 0.14 : 0.3
      // Hull border alpha: a defining rim. far=46%, mid=28%, near=13%.
      const borderAlpha = isFar ? 0.46 : isNear ? 0.13 : 0.28
      // Label alpha: far=80%, mid=35%, near=0%
      const labelAlpha = isFar ? 0.80 : isNear ? 0 : Math.max(0, (cameraRatio - nearThreshold) / (farThreshold - nearThreshold) * 0.35)

      // Collision pass (A3): decide which labels are legible at this zoom
      // BEFORE drawing, so the largest communities keep their labels and
      // overlapping smaller ones are suppressed instead of colliding.
      let visibleLabels: Set<string> | null = null
      if (labelAlpha > 0) {
        const rects: LabelRect[] = []
        for (const community of communities) {
          if (community.hull.length < 3) continue
          const vAnchor = s.graphToViewport({ x: community.anchor[0], y: community.anchor[1] })
          const fontSize = Math.max(11, Math.min(20, 11 + Math.sqrt(community.count) * 0.5))
          ctx.font = `500 ${fontSize}px ${tokens.fontSans}`
          const w = ctx.measureText(community.label.toUpperCase()).width + 6
          rects.push({ id: community.id, cx: vAnchor.x, cy: vAnchor.y, w, h: fontSize + 4, priority: community.count })
        }
        visibleLabels = selectVisibleLabels(rects, 4)
      }

      for (const community of communities) {
        if (community.hull.length < 3) continue
        const clusterColor = colorForRef.current
          ? colorForRef.current(community.id)
          : tokens.clusters[communitySlot(community.id)] ?? tokens.clusterOther

        // Convert map coordinates to viewport pixels.
        const vpts: [number, number][] = community.hull.map(([mx, my]) => {
          const vp = s.graphToViewport({ x: mx, y: my })
          return [vp.x, vp.y]
        })

        drawHullPath(ctx, vpts)

        // Radial nebula glow from the anchor: densest at the community centre,
        // fading to transparent at the hull edge (clipped to the hull shape).
        const vAnchor = s.graphToViewport({ x: community.anchor[0], y: community.anchor[1] })
        let maxR = 1
        for (const [px, py] of vpts) {
          maxR = Math.max(maxR, Math.hypot(px - vAnchor.x, py - vAnchor.y))
        }
        ctx.globalAlpha = fillAlpha
        if (clusterColor.length === 7 && clusterColor[0] === "#") {
          const grad = ctx.createRadialGradient(vAnchor.x, vAnchor.y, 0, vAnchor.x, vAnchor.y, maxR)
          grad.addColorStop(0, clusterColor)
          grad.addColorStop(0.6, clusterColor)
          grad.addColorStop(1, `${clusterColor}00`)
          ctx.fillStyle = grad
        } else {
          ctx.fillStyle = clusterColor
        }
        ctx.fill()

        ctx.globalAlpha = borderAlpha
        ctx.strokeStyle = clusterColor
        ctx.lineWidth = 1.25
        ctx.stroke()

        // Community label at anchor (reuses vAnchor from the fill above).
        // Only labels the collision pass accepted are drawn.
        if (labelAlpha > 0 && visibleLabels?.has(community.id)) {
          const labelText = community.label.toUpperCase()
          const fontSize = Math.max(11, Math.min(20, 11 + Math.sqrt(community.count) * 0.5))
          ctx.font = `500 ${fontSize}px ${tokens.fontSans}`
          ctx.letterSpacing = "0.08em"
          ctx.textAlign = "center"
          ctx.textBaseline = "middle"

          const textW = ctx.measureText(labelText).width + 6
          const textH = fontSize + 4

          // Background halo for readability
          ctx.globalAlpha = labelAlpha * 0.85
          ctx.fillStyle = tokens.background
          ctx.fillRect(vAnchor.x - textW / 2, vAnchor.y - textH / 2, textW, textH)

          ctx.globalAlpha = labelAlpha
          ctx.fillStyle = clusterColor
          ctx.fillText(labelText, vAnchor.x, vAnchor.y)
        }
      }

      ctx.restore()
    }

    s.on("afterRender", draw)
    // Initial draw
    draw()

    // Click detection on hull areas — check if click falls inside any hull.
    function handleClick(evt: MouseEvent) {
      if (!onCommunityClickRef.current) return
      const rect = container.getBoundingClientRect()
      const cx = evt.clientX - rect.left
      const cy = evt.clientY - rect.top

      for (const community of communities) {
        if (community.hull.length < 3) continue
        const vpts = community.hull.map(([mx, my]) =>
          s.graphToViewport({ x: mx, y: my })
        )
        if (pointInPolygon(cx, cy, vpts)) {
          onCommunityClickRef.current(community)
          return
        }
      }
    }

    container.addEventListener("click", handleClick)

    return () => {
      s.off("afterRender", draw)
      container.removeEventListener("click", handleClick)
      ro.disconnect()
      cvs.remove()
    }
  }, [sigma, communities, tokens, hullsVisible, farThreshold, nearThreshold])
}

/** Ray-casting point-in-polygon test for convex/concave hull. */
function pointInPolygon(
  px: number,
  py: number,
  pts: { x: number; y: number }[],
): boolean {
  let inside = false
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const xi = pts[i].x, yi = pts[i].y
    const xj = pts[j].x, yj = pts[j].y
    const intersect =
      yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi
    if (intersect) inside = !inside
  }
  return inside
}
