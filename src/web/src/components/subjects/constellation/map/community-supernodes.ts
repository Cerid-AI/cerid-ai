// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Super-node overlay for the Cartographer map. At overview zoom (camera ratio
// >= threshold) each Leiden community collapses to a single disc (sized by
// member count) connected by aggregated super-edges. Drawn on a canvas overlay
// synced to the sigma camera — NO graphology mutation. Clicking a disc surfaces
// onCommunityClick (the map zooms to that community's bbox = expand).

import { useEffect, useRef } from "react"
import type Sigma from "sigma"
import type { CommunityHull } from "@/lib/api/graph-map"
import type { MapTokens } from "./community-layer"
import { levelForRatio } from "./community-hierarchy-levels"

export interface SuperEdge { a: string; b: string; weight: number }

// Collapse at a MODERATE zoom-out (1.4) rather than far out (2.0): just past
// the default fit (ratio ~1) the graph still fills most of the viewport, so the
// super-node discs spread across the canvas instead of compressing into a
// central blob. One scroll-out from fit lands in this clean-overview band.
const COLLAPSE_THRESHOLD_DEFAULT = 1.4
// Each additional zoom-out step of this ratio magnitude advances one Leiden level.
const LEVEL_STEP_DEFAULT = 1.0

export function superNodeRadius(count: number): number {
  // sqrt scaling, floored so tiny communities stay clickable, capped so a giant
  // community doesn't dominate the canvas.
  return Math.min(60, Math.max(8, 4 + Math.sqrt(Math.max(0, count)) * 3))
}

export function isCollapsed(cameraRatio: number, threshold: number): boolean {
  return cameraRatio >= threshold
}

/**
 * Build an affine transform that stretches the bounding box of a set of
 * viewport points to fill the canvas (centered, aspect-preserving, padded).
 *
 * The corpus force-layout packs communities into a dense central ball, so
 * projecting anchors straight through the camera leaves the collapsed
 * super-node overview crammed in the middle. Feeding the camera-projected
 * anchors through this spread fans them across the whole viewport while
 * preserving their relative arrangement — the "cathedral" overview. Computed
 * from the live camera projection each frame, so it stays filled at any zoom.
 */
export function makeViewportSpread(
  pts: { x: number; y: number }[],
  vw: number,
  vh: number,
  padding = 0.08,
): (p: { x: number; y: number }) => { x: number; y: number } {
  if (pts.length === 0) return (p) => p
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const { x, y } of pts) {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const spanX = (maxX - minX) || 1
  const spanY = (maxY - minY) || 1
  const pad = Math.min(vw, vh) * padding
  const scale = Math.min((vw - 2 * pad) / spanX, (vh - 2 * pad) / spanY)
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  return ({ x, y }) => ({ x: vw / 2 + (x - cx) * scale, y: vh / 2 + (y - cy) * scale })
}

export function aggregateCommunityEdges(
  entities: { id: string; community: string | null }[],
  links: [number, number, number, string][],
): SuperEdge[] {
  const comm = entities.map((e) => e.community)
  const acc = new Map<string, SuperEdge>()
  for (const [si, ti, w] of links) {
    const cs = comm[si]
    const ct = comm[ti]
    if (!cs || !ct || cs === ct) continue
    const a = cs < ct ? cs : ct
    const b = cs < ct ? ct : cs
    const key = `${a}::${b}`
    const cur = acc.get(key)
    if (cur) cur.weight += w
    else acc.set(key, { a, b, weight: w })
  }
  return [...acc.values()]
}

function slot(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash) + id.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash) % 8
}

/** Draw one community disc + label at viewport point `p`. Shared by the global
 *  zoom-collapse overview and the per-community combo path (A10). */
function drawDisc(
  ctx: CanvasRenderingContext2D,
  c: CommunityHull,
  p: { x: number; y: number },
  tokens: MapTokens,
): void {
  const r = superNodeRadius(c.count)
  const color = tokens.clusters[slot(c.id)] ?? tokens.clusterOther
  ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
  ctx.globalAlpha = 0.85; ctx.fillStyle = color; ctx.fill()
  ctx.globalAlpha = 1; ctx.lineWidth = 1.5; ctx.strokeStyle = tokens.background; ctx.stroke()
  const label = c.label.toUpperCase()
  const fontSize = Math.max(10, Math.min(16, r * 0.5))
  ctx.font = `600 ${fontSize}px ${tokens.fontSans ?? "system-ui, sans-serif"}`
  ctx.textAlign = "center"; ctx.textBaseline = "middle"
  ctx.fillStyle = tokens.background; ctx.globalAlpha = 0.9
  const tw = ctx.measureText(label).width + 6
  ctx.fillRect(p.x - tw / 2, p.y + r + 2, tw, fontSize + 4)
  ctx.globalAlpha = 1; ctx.fillStyle = color
  ctx.fillText(label, p.x, p.y + r + 2 + (fontSize + 4) / 2)
}

export function useSuperNodeLayer(args: {
  sigma: Sigma | null
  levels: CommunityHull[][]
  levelSuperEdges: SuperEdge[][]
  tokens: MapTokens
  enabled: boolean
  threshold?: number
  levelStep?: number
  onCommunityClick?: (c: CommunityHull) => void
  onCollapsedChange?: (collapsed: boolean) => void
  /** Per-community combos (A10): level-0 community ids collapsed into a disc. */
  manualCollapsed?: ReadonlySet<string>
  /** Called when the user clicks a manual combo disc (to expand it). */
  onManualDiscClick?: (id: string) => void
}): void {
  const {
    sigma,
    levels,
    levelSuperEdges,
    tokens,
    enabled,
    threshold = COLLAPSE_THRESHOLD_DEFAULT,
    levelStep = LEVEL_STEP_DEFAULT,
    onCommunityClick,
    onCollapsedChange,
    manualCollapsed,
    onManualDiscClick,
  } = args
  const clickRef = useRef(onCommunityClick)
  clickRef.current = onCommunityClick
  const collapsedChangeRef = useRef(onCollapsedChange)
  collapsedChangeRef.current = onCollapsedChange
  const manualCollapsedRef = useRef<ReadonlySet<string>>(manualCollapsed ?? new Set())
  manualCollapsedRef.current = manualCollapsed ?? new Set()
  const manualClickRef = useRef(onManualDiscClick)
  manualClickRef.current = onManualDiscClick
  const lastCollapsedRef = useRef<boolean | null>(null)

  useEffect(() => {
    if (!sigma) return
    const s = sigma
    const container = s.getContainer()
    let canvas = container.querySelector<HTMLCanvasElement>("canvas[data-cartographer-supernodes]")
    if (!canvas) {
      canvas = document.createElement("canvas")
      canvas.dataset.cartographerSupernodes = "1"
      canvas.style.position = "absolute"
      canvas.style.inset = "0"
      canvas.style.pointerEvents = "none"
      // Above hulls + node mesh, below DOM cards.
      canvas.style.zIndex = "3"
      container.appendChild(canvas)
    }
    const cvs = canvas
    const resize = () => {
      const w = container.offsetWidth, h = container.offsetHeight
      const dpr = window.devicePixelRatio || 1
      cvs.width = w * dpr; cvs.height = h * dpr
      cvs.style.width = `${w}px`; cvs.style.height = `${h}px`
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)

    const draw = () => {
      const ctx = cvs.getContext("2d")
      if (!ctx) return
      const dpr = window.devicePixelRatio || 1
      ctx.clearRect(0, 0, cvs.width, cvs.height)
      const ratio = s.getCamera().ratio
      const collapsed = enabled && levels[0] && levels[0].length > 0 && isCollapsed(ratio, threshold)
      // notify CartographerMap so its reducers hide/show member nodes
      if (collapsed !== lastCollapsedRef.current) {
        lastCollapsedRef.current = collapsed
        collapsedChangeRef.current?.(collapsed)
      }
      if (!collapsed) {
        // Per-community combos (A10): outside the global zoom-collapse, draw a
        // disc for each manually-collapsed community at its REAL anchor (no
        // spread — these sit exactly where the hidden members were).
        const manual = manualCollapsedRef.current
        if (manual.size === 0 || !levels[0]) return
        ctx.save()
        ctx.scale(dpr, dpr)
        for (const c of levels[0]) {
          if (!manual.has(c.id)) continue
          drawDisc(ctx, c, s.graphToViewport({ x: c.anchor[0], y: c.anchor[1] }), tokens)
        }
        ctx.restore()
        return
      }
      // Pick the active Leiden level for this camera ratio.
      const lvl = levelForRatio(ratio, levels.length, threshold, levelStep)
      const communities = levels[lvl] ?? levels[0]
      const superEdges = levelSuperEdges[lvl] ?? levelSuperEdges[0] ?? []
      // index communities by id for anchor lookup (per-frame, since level may change)
      const byId = new Map(communities.map((c) => [c.id, c]))

      ctx.save()
      ctx.scale(dpr, dpr)

      // Spread the centrally-clustered anchors across the viewport so the
      // overview reads as a constellation, not a central blob. Built from the
      // live camera projection so it stays filled at any zoom.
      const vw = container.offsetWidth, vh = container.offsetHeight
      const projected = communities.map((c) => s.graphToViewport({ x: c.anchor[0], y: c.anchor[1] }))
      const spread = makeViewportSpread(projected, vw, vh)
      const at = (c: CommunityHull) => spread(s.graphToViewport({ x: c.anchor[0], y: c.anchor[1] }))

      // super-edges first (under discs)
      const maxW = superEdges.reduce((m, e) => Math.max(m, e.weight), 1)
      ctx.strokeStyle = tokens.edge
      for (const e of superEdges) {
        const ca = byId.get(e.a), cb = byId.get(e.b)
        if (!ca || !cb) continue
        const pa = at(ca)
        const pb = at(cb)
        ctx.globalAlpha = 0.10 + 0.25 * (e.weight / maxW)
        ctx.lineWidth = 0.5 + 3 * (e.weight / maxW)
        ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke()
      }
      ctx.globalAlpha = 1

      // discs + labels
      for (const c of communities) {
        drawDisc(ctx, c, at(c), tokens)
      }
      ctx.restore()
    }

    s.on("afterRender", draw)
    draw()

    const handleClick = (evt: MouseEvent) => {
      const ratio = s.getCamera().ratio
      const globallyCollapsed = enabled && levels[0] && levels[0].length > 0 && isCollapsed(ratio, threshold)
      if (!globallyCollapsed) {
        // Manual combo mode: a click on a collapsed community's disc expands it.
        const manual = manualCollapsedRef.current
        if (manual.size === 0 || !manualClickRef.current || !levels[0]) return
        const rect = container.getBoundingClientRect()
        const cx = evt.clientX - rect.left, cy = evt.clientY - rect.top
        for (const c of levels[0]) {
          if (!manual.has(c.id)) continue
          const p = s.graphToViewport({ x: c.anchor[0], y: c.anchor[1] })
          if (Math.hypot(cx - p.x, cy - p.y) <= superNodeRadius(c.count)) {
            manualClickRef.current(c.id)
            return
          }
        }
        return
      }
      if (!clickRef.current) return
      // Pick the active level — must match draw() so hit regions align.
      const lvl = levelForRatio(ratio, levels.length, threshold, levelStep)
      const communities = levels[lvl] ?? levels[0]
      const rect = container.getBoundingClientRect()
      const cx = evt.clientX - rect.left, cy = evt.clientY - rect.top
      // Same spread transform as draw() so hit-testing matches the rendered discs.
      const vw = container.offsetWidth, vh = container.offsetHeight
      const projected = communities.map((c) => s.graphToViewport({ x: c.anchor[0], y: c.anchor[1] }))
      const spread = makeViewportSpread(projected, vw, vh)
      for (const c of communities) {
        const p = spread(s.graphToViewport({ x: c.anchor[0], y: c.anchor[1] }))
        const r = superNodeRadius(c.count)
        if (Math.hypot(cx - p.x, cy - p.y) <= r) { clickRef.current(c); return }
      }
    }
    container.addEventListener("click", handleClick)

    return () => {
      s.off("afterRender", draw)
      container.removeEventListener("click", handleClick)
      ro.disconnect()
      cvs.remove()
    }
  }, [sigma, levels, levelSuperEdges, tokens, enabled, threshold, levelStep])
}
