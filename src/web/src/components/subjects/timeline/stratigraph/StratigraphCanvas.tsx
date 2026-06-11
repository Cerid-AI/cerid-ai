// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Stratigraph canvas: canvas-2D mark layer + SVG time axis + d3-brushX
// overview strip (bidirectional sync with d3-zoom) + DOM gutter labels.
//
// Design rules (mirror Cartographer):
//   - FLAT. No glow, gradients.
//   - Teal --color-map-interaction ONLY for hover/selection.
//   - All colors flow through resolved MapTokens (never raw hex in canvas ops).
//   - DPR-aware canvas sizing via ResizeObserver.
//   - rAF-coalesced redraws — one requestAnimationFrame per logical frame.
//   - ctx.scale() is NOT used for zoom — positions recomputed via transform.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import { scaleTime, type ScaleTime } from "d3-scale"
import { zoom as d3Zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform, type D3ZoomEvent } from "d3-zoom"
import { brushX, type BrushBehavior, type D3BrushEvent } from "d3-brush"
import { quadtree, type Quadtree } from "d3-quadtree"
import { select } from "d3-selection"
import { timeDay, timeWeek, timeMonth } from "d3-time"
import { timeFormat } from "d3-time-format"

import type { MapTokens } from "@/components/subjects/constellation/map/community-layer"
import { domainIcon } from "@/lib/graph/domain-icons"
import { titleCase } from "@/lib/graph/domain-icons"
import {
  computeStrata,
  computeLOD,
  clusterMarkers,
  bucketTrustSuffix,
  computeTypeLensStrata,
  computeDomainLensStrata,
  type StratumLayout,
  type LODLevel,
} from "./strata-layout"
import type { TimelineStrataResponse, TrackEvent } from "@/lib/api/graph"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TimelineLens = "cluster" | "trust" | "type" | "domain"

export interface PinnedTrack {
  canonicalId: string
  name: string
  communityId: string
  trustState: string
  events?: TrackEvent[]
}

export interface PinnedCommunity {
  communityId: string
  label: string
  topHubs: Array<{ canonical_id: string; name: string }>
  totalMentions: number
}

interface HitEntry {
  x: number
  y: number
  type: "bucket" | "track-tick" | "marker" | "entity-birth"
  bucketIdx?: number
  trackId?: string
  markerIdx?: number
  stratumIdx?: number
}

export interface StratigraphCanvasProps {
  data: TimelineStrataResponse
  lens: TimelineLens
  typeFilter: Set<string>
  pinnedIds: Set<string>
  frozenOrder: string[] | null
  trackBudget: number
  markersVisible: boolean
  ingestHatch: boolean
  lodLevel: LODLevel
  onLODChange: (level: LODLevel, visibleDays: number) => void
  tokens: MapTokens
  onCommunityClick: (community: PinnedCommunity) => void
  onTrackClick: (track: PinnedTrack) => void
  onBrushChange: (from: string, to: string) => void
  reducedMotion: boolean
}

// ---------------------------------------------------------------------------
// LOD crossfade alpha
// ---------------------------------------------------------------------------

const LOD_CROSSFADE_MS = 150

// ---------------------------------------------------------------------------
// Tick format helpers
// ---------------------------------------------------------------------------

const FMT_MONTH = timeFormat("%b %Y")
const FMT_WEEK = timeFormat("%b %d")
const FMT_DAY = timeFormat("%b %d")

function autoTickFormat(date: Date, visibleDays: number): string {
  if (visibleDays > 180) return FMT_MONTH(date)
  if (visibleDays > 30) return FMT_WEEK(date)
  return FMT_DAY(date)
}

function autoTicks(scale: ScaleTime<number, number>, visibleDays: number): Date[] {
  if (visibleDays > 180) return scale.ticks(timeMonth)
  if (visibleDays > 30) return scale.ticks(timeWeek)
  return scale.ticks(timeDay)
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StratigraphCanvas({
  data,
  lens,
  typeFilter,
  pinnedIds,
  frozenOrder,
  trackBudget,
  markersVisible,
  ingestHatch,
  lodLevel,
  onLODChange,
  tokens,
  onCommunityClick,
  onTrackClick,
  onBrushChange,
  reducedMotion,
}: StratigraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const gutterRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const brushSvgRef = useRef<SVGSVGElement>(null)

  const rafRef = useRef<number | null>(null)
  const dirtyRef = useRef(true)

  // Zoom state
  const zoomRef = useRef<ZoomBehavior<HTMLCanvasElement, unknown> | null>(null)
  const transformRef = useRef<ZoomTransform>(zoomIdentity)
  const brushRef = useRef<BrushBehavior<unknown> | null>(null)
  const brushBlockRef = useRef(false) // prevents zoom↔brush feedback loop

  // Dimensions (CSS pixels, not physical)
  const [dims, setDims] = useState({ w: 800, h: 400 })

  // Layout cache
  const strataRef = useRef<StratumLayout[]>([])
  const markersRef = useRef<ReturnType<typeof clusterMarkers>>([])

  // Quadtree for hit-testing (rebuilt on zoom settle)
  const qtRef = useRef<Quadtree<HitEntry> | null>(null)

  // LOD crossfade
  const lodAlphaRef = useRef(1)
  const lodCrossfadeRef = useRef<number | null>(null)
  const prevLodRef = useRef<LODLevel>(lodLevel)

  // Hover/tooltip state
  const [tooltip, setTooltip] = useState<{ x: number; y: number; label: string } | null>(null)
  const [legendStrata, setLegendStrata] = useState<StratumLayout[]>([])

  // Aria live region for bucket announcements
  const ariaLiveRef = useRef<HTMLSpanElement>(null)

  // ---------------------------------------------------------------------------
  // Resize
  // ---------------------------------------------------------------------------

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const e = entries[0]
      if (!e) return
      setDims({ w: e.contentRect.width, h: e.contentRect.height })
      dirtyRef.current = true
    })
    ro.observe(el)
    setDims({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  // ---------------------------------------------------------------------------
  // Sync DPR canvas
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = dims.w * dpr
    canvas.height = dims.h * dpr
    dirtyRef.current = true
  }, [dims])

  // ---------------------------------------------------------------------------
  // Base time scale from data
  // ---------------------------------------------------------------------------

  const baseScaleRef = useRef<ScaleTime<number, number>>(
    scaleTime().domain([new Date(), new Date()]).range([0, 800])
  )

  useEffect(() => {
    if (!data.bucket_dates.length) return
    const d0 = new Date(data.from_date)
    const d1 = new Date(data.to_date)
    const GUTTER_W = 80
    baseScaleRef.current = scaleTime()
      .domain([d0, d1])
      .range([GUTTER_W, dims.w])
    dirtyRef.current = true
  }, [data, dims.w])

  // ---------------------------------------------------------------------------
  // Strata layout recompute
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const AXIS_H = 28
    const BRUSH_H = 32
    const canvasH = Math.max(100, dims.h - AXIS_H - BRUSH_H - 4)

    if (lens === "type") {
      strataRef.current = computeTypeLensStrata(data, canvasH, trackBudget, pinnedIds)
    } else if (lens === "domain") {
      strataRef.current = computeDomainLensStrata(data, canvasH, trackBudget, pinnedIds)
    } else {
      const result = computeStrata({
        response: data,
        canvasHeight: canvasH,
        trackBudget,
        pinnedIds,
        frozenOrder,
      })
      strataRef.current = result.strata
    }

    markersRef.current = clusterMarkers(data.markers, data.from_date, data.to_date)
    dirtyRef.current = true
    qtRef.current = null // force quadtree rebuild
    setLegendStrata(lens === "domain" ? strataRef.current : [])
  }, [data, lens, typeFilter, pinnedIds, frozenOrder, trackBudget, dims.h])

  // LOD crossfade trigger
  useEffect(() => {
    if (lodLevel === prevLodRef.current || reducedMotion) {
      lodAlphaRef.current = 1
      prevLodRef.current = lodLevel
      dirtyRef.current = true
      return
    }
    // Animate crossfade
    const start = performance.now()
    const animate = (now: number) => {
      const elapsed = now - start
      lodAlphaRef.current = Math.min(1, elapsed / LOD_CROSSFADE_MS)
      dirtyRef.current = true
      if (lodAlphaRef.current < 1) {
        lodCrossfadeRef.current = requestAnimationFrame(animate)
      } else {
        prevLodRef.current = lodLevel
      }
    }
    lodCrossfadeRef.current = requestAnimationFrame(animate)
    return () => {
      if (lodCrossfadeRef.current !== null) cancelAnimationFrame(lodCrossfadeRef.current)
    }
  }, [lodLevel, reducedMotion])

  // ---------------------------------------------------------------------------
  // d3-zoom setup
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !data.bucket_dates.length) return

    const GUTTER_W = 80

    const zoom = d3Zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([1, 120])
      .translateExtent([[GUTTER_W, 0], [dims.w, dims.h]])
      .on("zoom", (event: D3ZoomEvent<HTMLCanvasElement, unknown>) => {
        transformRef.current = event.transform
        dirtyRef.current = true

        // Compute visible days and fire LOD update
        const xScale = event.transform.rescaleX(baseScaleRef.current)
        const [t0, t1] = xScale.domain() as [Date, Date]
        const visibleDays = (t1.getTime() - t0.getTime()) / 86_400_000
        const newLod = computeLOD(visibleDays, lodLevel)
        if (newLod !== lodLevel) onLODChange(newLod, visibleDays)

        // Sync brush without re-triggering zoom (sourceEvent guard)
        if (brushBlockRef.current) return
        const brushSvg = brushSvgRef.current
        if (!brushSvg || !brushRef.current) return
        const fullScale = baseScaleRef.current
        const xLeft = fullScale(t0)
        const xRight = fullScale(t1)
        brushBlockRef.current = true
        // brushRef.current.move requires a <g> element, not the <svg> root
        const brushG = brushSvg.querySelector("g")
        if (brushG) {
          select(brushG).call(brushRef.current.move, [xLeft - GUTTER_W, xRight - GUTTER_W])
        }
        brushBlockRef.current = false

        qtRef.current = null // rebuild on settle
      })
      .on("end", () => {
        // Rebuild quadtree after zoom settles
        rebuildQuadtree()
      })

    zoomRef.current = zoom
    select(canvas).call(zoom as ZoomBehavior<HTMLCanvasElement, unknown>)

    return () => {
      select(canvas).on(".zoom", null)
    }
    // Intentional: re-attach zoom only when data or dims change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dims])

  // ---------------------------------------------------------------------------
  // d3-brush overview strip
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const brushSvg = brushSvgRef.current
    if (!brushSvg || !data.bucket_dates.length) return

    const GUTTER_W = 80
    const brushWidth = dims.w - GUTTER_W

    const brush = brushX<unknown>()
      .extent([[0, 0], [brushWidth, 28]])
      .on("brush end", (event: D3BrushEvent<unknown>) => {
        if (brushBlockRef.current) return
        // Programmatic moves (initial extent, zoom→brush sync, data refresh)
        // have no sourceEvent. Reacting to them re-enters the zoom transform
        // and refires onBrushChange — the classic brush↔zoom feedback loop.
        if (!event.sourceEvent) return
        if (!event.selection) return
        const [x0, x1] = event.selection as [number, number]
        const fullScale = baseScaleRef.current
        const t0 = fullScale.invert(x0 + GUTTER_W)
        const t1 = fullScale.invert(x1 + GUTTER_W)

        // Compute zoom transform to match brush selection
        const k = brushWidth / Math.max(1, x1 - x0)
        const tx = -(x0 * k)

        if (zoomRef.current && canvasRef.current) {
          brushBlockRef.current = true
          const newTransform = zoomIdentity.scale(k).translate(tx / k, 0)
          select(canvasRef.current).call(
            (zoomRef.current as ZoomBehavior<HTMLCanvasElement, unknown>).transform,
            newTransform,
          )
          brushBlockRef.current = false
        }

        // Fire window change for query refresh
        const from = formatDate(t0)
        const to = formatDate(t1)
        onBrushChange(from, to)
        dirtyRef.current = true
      })

    brushRef.current = brush
    const g = select(brushSvg).append("g")
    g.call(brush)
    g.call(brush.move, [0, brushWidth]) // start fully extended

    return () => {
      select(brushSvg).selectAll("g").remove()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dims])

  // ---------------------------------------------------------------------------
  // Draw
  // ---------------------------------------------------------------------------

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const W = dims.w
    const H = dims.h
    const AXIS_H = 28
    const BRUSH_H = 32
    const GUTTER_W = 80
    const strata = strataRef.current
    const alpha = lodAlphaRef.current

    ctx.save()
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, W, H)

    const xform = transformRef.current
    const xScale = xform.rescaleX(baseScaleRef.current)

    const bucketCount = data.bucket_dates.length
    const bucketDates = data.bucket_dates.map((d) => new Date(d))

    // ---------------------------------------------------------------------------
    // Draw strata fills + filaments + ticks
    // ---------------------------------------------------------------------------

    for (let si = 0; si < strata.length; si++) {
      const st = strata[si]
      const topY = AXIS_H + st.topPx
      const botY = topY + st.heightPx

      // Base stratum color — colorFamily discriminator routes to domains or clusters palette.
      // colorSlot === -1 sentinel maps to the respective *Other token.
      const clusterColor = st.colorFamily === "domain"
        ? (st.colorSlot < 0 ? tokens.domainOther : (tokens.domains[st.colorSlot] ?? tokens.domainOther))
        : (st.colorSlot < 0 ? tokens.clusterOther : (tokens.clusters[st.colorSlot] ?? tokens.clusterOther))

      // Smoothed deposition band — one continuous filled path per stratum
      // (geological continuity: a centered moving average softens bucket
      // steps and a 2px floor keeps quiet stretches as a hairline, so the
      // stratum never breaks into disconnected cells).
      const FLOOR_PX = 2
      const raw = st.bucketHeights
      const bandH: number[] = new Array(bucketCount)
      for (let bi = 0; bi < bucketCount; bi++) {
        const a = raw[Math.max(0, bi - 1)] ?? 0
        const b = raw[bi] ?? 0
        const c = raw[Math.min(bucketCount - 1, bi + 1)] ?? 0
        bandH[bi] = Math.max(FLOOR_PX, (a + 2 * b + c) / 4)
      }
      const xMid = (bi: number): number => {
        const d = bucketDates[bi]
        const nd = bucketDates[bi + 1] ?? new Date(d.getTime() + 86_400_000)
        return (xScale(d) + xScale(nd)) / 2
      }
      const xLeft = xScale(bucketDates[0])
      const lastDate = bucketDates[bucketCount - 1]
      const xRight = xScale(bucketDates[bucketCount] ?? new Date(lastDate.getTime() + 86_400_000))

      ctx.globalAlpha = (lodLevel === "era" ? 0.55 : 0.7) * alpha
      ctx.fillStyle = clusterColor
      ctx.beginPath()
      ctx.moveTo(xLeft, botY - bandH[0])
      for (let bi = 0; bi < bucketCount - 1; bi++) {
        const mx = (xMid(bi) + xMid(bi + 1)) / 2
        const my = botY - (bandH[bi] + bandH[bi + 1]) / 2
        ctx.quadraticCurveTo(xMid(bi), botY - bandH[bi], mx, my)
      }
      ctx.lineTo(xRight, botY - bandH[bucketCount - 1])
      ctx.lineTo(xRight, botY)
      ctx.lineTo(xLeft, botY)
      ctx.closePath()
      ctx.fill()

      // Per-bucket overlays on top of the band: trust re-tint, ingest dimming
      for (let bi = 0; bi < bucketCount; bi++) {
        const bDate = bucketDates[bi]
        if (!bDate) continue
        const x0 = xScale(bDate)
        const nextDate = bucketDates[bi + 1] ?? new Date(bDate.getTime() + 86_400_000)
        const x1 = xScale(nextDate)
        const bw = Math.max(1, x1 - x0)
        const bh = bandH[bi]
        const yTop = botY - bh

        // Trust lens re-tints each bucket segment; severity wins (amendment 1)
        if (lens === "trust" && (raw[bi] ?? 0) > 0) {
          const suffix = bucketTrustSuffix(bi, st.unverifiedBuckets, st.trustMix)
          ctx.globalAlpha = 0.6 * alpha
          ctx.fillStyle = suffix === "verified" ? tokens.trustVerified :
            suffix === "partial" ? tokens.trustPartial : tokens.trustUnverified
          ctx.fillRect(x0, yTop, bw, bh)
        }

        // Ingest burst honesty (amendment 3): dim + hatch the bulk-import bucket
        const isIngestBurst = ingestHatch &&
          data.markers.some((m) => m.kind === "ingest_burst" && m.date === data.bucket_dates[bi])
        if (isIngestBurst) {
          ctx.globalAlpha = 0.45 * alpha
          ctx.fillStyle = tokens.background
          ctx.fillRect(x0, yTop, bw, bh)
          ctx.globalAlpha = 0.18 * alpha
          ctx.strokeStyle = clusterColor
          ctx.lineWidth = 1
          for (let hx = x0; hx < x0 + bw; hx += 4) {
            ctx.beginPath()
            ctx.moveTo(hx, yTop)
            ctx.lineTo(hx - 6, botY)
            ctx.stroke()
          }
        }

        // Faint grid banding at week boundaries
        if (lodLevel !== "era") {
          if (bDate.getDay() === 0) {
            ctx.globalAlpha = 0.06 * alpha
            ctx.strokeStyle = tokens.grid
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(x0, AXIS_H)
            ctx.lineTo(x0, H - BRUSH_H)
            ctx.stroke()
          }
        }
      }

      // Stratum baseline
      ctx.globalAlpha = 0.3
      ctx.strokeStyle = tokens.edge
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(GUTTER_W, botY)
      ctx.lineTo(W, botY)
      ctx.stroke()

      // Filaments (LOD >= bucket): darker lines for DOI tracks
      if (lodLevel !== "era" && st.trackIds.length > 0) {
        const tracksById = new Map(data.tracks.map((t) => [t.canonical_id, t]))
        const trackH = st.heightPx / Math.max(1, st.trackIds.length + 1)

        st.trackIds.forEach((tid, tidx) => {
          const track = tracksById.get(tid)
          if (!track) return
          const fy = topY + trackH * (tidx + 1)

          ctx.globalAlpha = 0.45 * alpha
          ctx.strokeStyle = clusterColor
          ctx.lineWidth = 2

          ctx.beginPath()
          let started = false
          for (let bi = 0; bi < bucketCount; bi++) {
            const bDate = bucketDates[bi]
            if (!bDate) continue
            const bx = xScale(bDate)
            const v = track.buckets[bi] ?? 0
            if (v === 0) { started = false; continue }
            if (!started) { ctx.moveTo(bx, fy); started = true }
            else ctx.lineTo(bx, fy)
          }
          ctx.stroke()

          // Birth diamond
          if (track.first_seen) {
            const birthDate = new Date(track.first_seen)
            const bx = xScale(birthDate)
            if (bx >= GUTTER_W && bx <= W) {
              ctx.globalAlpha = 0.9 * alpha
              ctx.fillStyle = tokens.interaction
              ctx.beginPath()
              ctx.moveTo(bx, fy - 5)
              ctx.lineTo(bx + 4, fy)
              ctx.lineTo(bx, fy + 5)
              ctx.lineTo(bx - 4, fy)
              ctx.closePath()
              ctx.fill()
            }
          }

          // LOD track: discrete event ticks for pinned or very zoomed
          if (lodLevel === "track" && pinnedIds.has(tid)) {
            ctx.globalAlpha = 0.7 * alpha
            ctx.strokeStyle = tokens.interaction
            ctx.lineWidth = 1
            for (let bi = 0; bi < bucketCount; bi++) {
              const v = track.buckets[bi] ?? 0
              if (v === 0) continue
              const bDate = bucketDates[bi]
              if (!bDate) continue
              const bx = xScale(bDate)
              ctx.beginPath()
              ctx.moveTo(bx, fy - 4)
              ctx.lineTo(bx, fy + 4)
              ctx.stroke()
            }
          }
        })
      }

      ctx.globalAlpha = 1
    }

    // ---------------------------------------------------------------------------
    // Event-horizon markers (hairlines)
    // ---------------------------------------------------------------------------

    if (markersVisible) {
      const markers = markersRef.current
      for (const m of markers) {
        const mDate = new Date(m.date)
        const mx = xScale(mDate)
        if (mx < GUTTER_W || mx > W) continue

        ctx.globalAlpha = 0.7
        ctx.strokeStyle = tokens.interaction
        ctx.lineWidth = 1
        ctx.setLineDash([3, 3])
        ctx.beginPath()
        ctx.moveTo(mx, AXIS_H)
        ctx.lineTo(mx, H - BRUSH_H - 4)
        ctx.stroke()
        ctx.setLineDash([])
      }
      ctx.globalAlpha = 1
    }

    ctx.restore()
  }, [data, dims, lens, tokens, markersVisible, ingestHatch, lodLevel, pinnedIds])

  // ---------------------------------------------------------------------------
  // rAF-coalesced render loop
  // ---------------------------------------------------------------------------

  useEffect(() => {
    let running = true
    const tick = () => {
      if (!running) return
      if (dirtyRef.current) {
        dirtyRef.current = false
        draw()
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      running = false
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [draw])

  // Mark dirty on all relevant changes
  useEffect(() => { dirtyRef.current = true }, [data, lens, typeFilter, pinnedIds, tokens, markersVisible, ingestHatch, lodLevel, dims])

  // ---------------------------------------------------------------------------
  // SVG axis rendering (React-side)
  // ---------------------------------------------------------------------------

  const xform = transformRef.current
  const xScale = xform.rescaleX(baseScaleRef.current)
  const [domainStart, domainEnd] = xScale.domain() as [Date, Date]
  const visibleDays = (domainEnd.getTime() - domainStart.getTime()) / 86_400_000
  const ticks = autoTicks(xScale, visibleDays)
  const GUTTER_W = 80
  const AXIS_H = 28
  const BRUSH_H = 32

  // ---------------------------------------------------------------------------
  // Quadtree hit-testing
  // ---------------------------------------------------------------------------

  const rebuildQuadtree = useCallback(() => {
    if (!data.bucket_dates.length || !strataRef.current.length) return
    const entries: HitEntry[] = []
    const xform2 = transformRef.current
    const xScale2 = xform2.rescaleX(baseScaleRef.current)
    const bucketDates2 = data.bucket_dates.map((d) => new Date(d))
    const strata2 = strataRef.current

    for (let si = 0; si < strata2.length; si++) {
      const st = strata2[si]
      const topY = AXIS_H + st.topPx
      const botY = topY + st.heightPx
      const midY = (topY + botY) / 2

      for (let bi = 0; bi < data.bucket_dates.length; bi++) {
        const bDate = bucketDates2[bi]
        if (!bDate) continue
        const bx = xScale2(bDate)
        entries.push({ x: bx, y: midY, type: "bucket", bucketIdx: bi, stratumIdx: si })
      }

      // Track-row hit zones: one hit-entry per track row positioned at the
      // track's y-centre spanning the full time range. LOD >= bucket only.
      if (st.trackIds.length > 0) {
        const trackH = st.heightPx / Math.max(1, st.trackIds.length + 1)
        const midX = xScale2(new Date(data.from_date)) + (xScale2(new Date(data.to_date)) - xScale2(new Date(data.from_date))) / 2
        st.trackIds.forEach((tid, tidx) => {
          const fy = topY + trackH * (tidx + 1)
          entries.push({ x: midX, y: fy, type: "track-tick", trackId: tid, stratumIdx: si })
        })
      }
    }

    qtRef.current = quadtree<HitEntry>()
      .x((d) => d.x)
      .y((d) => d.y)
      .addAll(entries)
  }, [data, dims])

  // ---------------------------------------------------------------------------
  // Mouse hit-testing
  // ---------------------------------------------------------------------------

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const qt = qtRef.current
    if (!qt) return
    const hit = qt.find(mx, my, 20)
    if (!hit) { setTooltip(null); return }

    if (hit.type === "bucket" && hit.bucketIdx !== undefined && hit.stratumIdx !== undefined) {
      const st = strataRef.current[hit.stratumIdx]
      const date = data.bucket_dates[hit.bucketIdx] ?? ""
      // Exact count, always: sqrt band heights distort visual comparison.
      const count = st?.bucketCounts[hit.bucketIdx] ?? 0
      const label = `${st?.label ?? ""} · ${date} · ${count} mention${count === 1 ? "" : "s"}`
      setTooltip({ x: e.clientX - rect.left + 12, y: e.clientY - rect.top + 12, label })

      // Aria-live announcement
      if (ariaLiveRef.current) {
        ariaLiveRef.current.textContent = `${st?.label ?? "stratum"} on ${date}: ${count} mentions`
      }
    }
  }, [data])

  const handleMouseLeave = useCallback(() => { setTooltip(null) }, [])

  // Double-click bucket → animated zoom one LOD rung (amendment 2)
  const handleDblClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect || !zoomRef.current || !canvasRef.current) return
    const mx = e.clientX - rect.left

    const qt = qtRef.current
    if (!qt) return
    const hit = qt.find(mx, e.clientY - rect.top, 30)
    if (!hit || hit.bucketIdx === undefined) return

    const bDate = data.bucket_dates[hit.bucketIdx]
    const nextDate = data.bucket_dates[hit.bucketIdx + 1]
    if (!bDate || !nextDate) return

    const d0 = new Date(bDate)
    const d1 = new Date(nextDate)
    const fullScale = baseScaleRef.current
    const x0 = fullScale(d0)
    const x1 = fullScale(d1)
    const bucketW = x1 - x0
    const newK = Math.min(120, (dims.w - GUTTER_W) / Math.max(1, bucketW) * 0.5)
    const cx = (x0 + x1) / 2
    const newTx = (dims.w / 2) - cx * newK

    const target = zoomIdentity.scale(newK).translate(newTx / newK, 0)
    const zoomBehavior = zoomRef.current as ZoomBehavior<HTMLCanvasElement, unknown>
    const canvasSel = select<HTMLCanvasElement, unknown>(canvasRef.current!)

    if (reducedMotion) {
      canvasSel.call(zoomBehavior.transform, target)
    } else {
      // Animate zoom via interpolated intermediate transforms (d3-transition not imported)
      const startTransform = transformRef.current
      const startTime = performance.now()
      const duration = 300
      const animateZoom = (now: number) => {
        const t = Math.min(1, (now - startTime) / duration)
        const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t
        const k = startTransform.k + (target.k - startTransform.k) * ease
        const tx = startTransform.x + (target.x - startTransform.x) * ease
        const ty = startTransform.y + (target.y - startTransform.y) * ease
        const interp = zoomIdentity.scale(k).translate(tx / k, ty / k)
        canvasSel.call(zoomBehavior.transform, interp)
        if (t < 1) requestAnimationFrame(animateZoom)
        else canvasSel.call(zoomBehavior.transform, target)
      }
      requestAnimationFrame(animateZoom)
    }
  }, [data, dims, reducedMotion])

  // Click stratum or track
  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const qt = qtRef.current
    if (!qt) return

    const hit = qt.find(mx, my, 24)
    if (!hit) return

    if (hit.type === "track-tick" && hit.trackId) {
      const track = data.tracks.find((t) => t.canonical_id === hit.trackId)
      if (!track) return
      onTrackClick({
        canonicalId: track.canonical_id,
        name: track.name,
        communityId: track.community_id,
        trustState: track.trust_state,
      })
      return
    }

    if (hit.type === "bucket" && hit.stratumIdx !== undefined) {
      const st = strataRef.current[hit.stratumIdx]
      if (!st) return
      const topHubs = data.tracks
        .filter((t) => t.community_id === st.communityId)
        .slice(0, 5)
        .map((t) => ({ canonical_id: t.canonical_id, name: t.name }))
      onCommunityClick({
        communityId: st.communityId,
        label: st.label,
        topHubs,
        totalMentions: st.totalMentions,
      })
    }
  }, [data, onCommunityClick, onTrackClick])

  // Escape collapses any expanded stratum
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setTooltip(null)
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  // Rebuild quadtree once on first render with data
  useEffect(() => {
    if (data.bucket_dates.length > 0) rebuildQuadtree()
  }, [data, rebuildQuadtree])

  // ---------------------------------------------------------------------------
  // Gutter labels (DOM)
  // ---------------------------------------------------------------------------

  const gutterLabels = strataRef.current.map((st, idx) => ({
    id: `gutter-${idx}`,
    label: st.label.toUpperCase(),
    y: AXIS_H + st.topPx + st.heightPx / 2,
  }))

  // ---------------------------------------------------------------------------
  // Overview strip data: aggregate per-bucket totals for brush background
  // ---------------------------------------------------------------------------

  const maxTotal = Math.max(
    1,
    ...data.bucket_dates.map((_, bi) =>
      data.series.reduce((s, ser) => s + (ser.buckets[bi] ?? 0), 0)
    ),
  )

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full select-none overflow-hidden"
      role="application"
      aria-roledescription="temporal knowledge-graph view"
      aria-label={`Stratigraph of ${data.totals.mentions} mentions`}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
      onDoubleClick={handleDblClick}
    >
      {/* Hidden aria-live region for bucket announcements */}
      <span
        ref={ariaLiveRef}
        role="status"
        aria-live="polite"
        className="sr-only"
      />

      {/* Left gutter labels (DOM, positioned absolute) */}
      <div
        ref={gutterRef}
        className="pointer-events-none absolute left-0 top-0 w-20 overflow-hidden"
        style={{ height: dims.h - BRUSH_H - 4 }} // drift-allowed: runtime height
        aria-hidden="true"
      >
        {gutterLabels.map((g) => (
          <div
            key={g.id}
            className="absolute left-0 w-20 truncate px-1 text-right text-label-xxs font-medium uppercase tracking-widest text-muted-foreground"
            style={{ top: g.y, transform: "translateY(-50%)" }} // drift-allowed: runtime layout position
          >
            {g.label}
          </div>
        ))}
      </div>

      {/* SVG time axis */}
      <svg
        ref={svgRef}
        className="pointer-events-none absolute left-0 top-0"
        width={dims.w}
        height={AXIS_H}
        aria-hidden="true"
      >
        {ticks.map((tick, i) => {
          const tx = xScale(tick)
          if (tx < GUTTER_W || tx > dims.w) return null
          return (
            <g key={i} transform={`translate(${tx},0)`}>
              <line y1={AXIS_H - 4} y2={AXIS_H} stroke={tokens.edge} strokeWidth={1} />
              <text
                y={AXIS_H - 7}
                textAnchor="middle"
                fontSize={9}
                fill={tokens.foreground}
                opacity={0.6}
              >
                {autoTickFormat(tick, visibleDays)}
              </text>
            </g>
          )
        })}
        {/* Axis label "observed (ingested)" — amendment 3 */}
        <text
          x={GUTTER_W + 4}
          y={12}
          fontSize={8}
          fill={tokens.foreground}
          opacity={0.4}
        >
          observed (ingested)
        </text>
      </svg>

      {/* Main canvas */}
      <canvas
        ref={canvasRef}
        style={{ // drift-allowed: canvas sizing from runtime ResizeObserver dimensions
          position: "absolute",
          left: 0,
          top: 0,
          width: dims.w,
          height: dims.h - BRUSH_H - 4,
          cursor: "crosshair",
        }}
        aria-hidden="true"
      />

      {/* Marker labels (DOM, above canvas) */}
      {markersVisible && markersRef.current.map((m, i) => {
        const mDate = new Date(m.date)
        const mx = xScale(mDate)
        if (mx < GUTTER_W || mx > dims.w) return null
        return (
          <div
            key={i}
            className="pointer-events-none absolute text-label-xxs font-medium uppercase tracking-wider text-muted-foreground"
            style={{ left: mx + 2, top: AXIS_H + 2 }} // drift-allowed: runtime marker position
          >
            {m.kind === "ingest_burst" ? "ingest" : "birth"}
          </div>
        )
      })}

      {/* Overview density strip + brush */}
      <div
        className="absolute left-0 w-full"
        style={{ bottom: 0, height: BRUSH_H }} // drift-allowed: runtime layout position
      >
        {/* Hidden range input for a11y scrubbing (data-testid required) */}
        <input
          type="range"
          min={0}
          max={Math.max(0, data.bucket_dates.length - 1)}
          defaultValue={data.bucket_dates.length - 1}
          aria-label="Timeline scrubber"
          data-testid="timeline-scrubber"
          className="sr-only"
          onChange={(e) => {
            // Zoom to the bucket at this index
            const bi = parseInt(e.target.value, 10)
            const bDate = data.bucket_dates[bi]
            if (!bDate || !zoomRef.current || !canvasRef.current) return
            const d0 = new Date(bDate)
            const nextB = data.bucket_dates[bi + 1]
            const d1 = nextB ? new Date(nextB) : d0
            const fullScale = baseScaleRef.current
            const x0 = fullScale(d0)
            const x1 = fullScale(d1)
            const newK = Math.min(120, (dims.w - GUTTER_W) / Math.max(1, x1 - x0) * 4)
            const cx = (x0 + x1) / 2
            const newTx = (dims.w / 2) - cx * newK
            const target = zoomIdentity.scale(newK).translate(newTx / newK, 0)
            select(canvasRef.current!).call(
              (zoomRef.current as ZoomBehavior<HTMLCanvasElement, unknown>).transform,
              target,
            )
          }}
        />

        {/* Overview background bars */}
        <svg
          className="absolute left-20 top-0"
          width={dims.w - GUTTER_W}
          height={BRUSH_H}
          aria-hidden="true"
        >
          {data.bucket_dates.map((bd, bi) => {
            const bDate = new Date(bd)
            const nextDate = data.bucket_dates[bi + 1]
              ? new Date(data.bucket_dates[bi + 1])
              : new Date(bDate.getTime() + 86_400_000)
            const overviewScale = scaleTime()
              .domain([new Date(data.from_date), new Date(data.to_date)])
              .range([0, dims.w - GUTTER_W])
            const x0 = overviewScale(bDate)
            const x1 = overviewScale(nextDate)
            const bw = Math.max(1, x1 - x0 - 0.5)
            const total = data.series.reduce((s, ser) => s + (ser.buckets[bi] ?? 0), 0)
            const barH = Math.max(1, Math.round((total / maxTotal) * (BRUSH_H - 4)))

            return (
              <rect
                key={bi}
                x={x0}
                y={BRUSH_H - 4 - barH}
                width={bw}
                height={barH}
                fill={tokens.clusterOther}
                opacity={0.35}
              />
            )
          })}
        </svg>

        {/* d3-brushX SVG */}
        <svg
          ref={brushSvgRef}
          className="absolute left-20 top-0"
          width={dims.w - GUTTER_W}
          height={BRUSH_H}
          aria-hidden="true"
        />
      </div>

      {/* Domain lens legend strip — icon + Title-cased label + hue chip; shown only when domain lens is active */}
      {lens === "domain" && legendStrata.length > 0 && (
        <div
          className="pointer-events-none absolute right-2 top-2 z-20 flex flex-col gap-0.5 rounded-md border border-border/40 bg-card/90 px-2 py-1.5 backdrop-blur"
          aria-label="Domain lens legend"
          role="list"
        >
          {legendStrata.map((st, idx) => {
            const color = st.colorFamily === "domain"
              ? (st.colorSlot < 0 ? tokens.domainOther : (tokens.domains[st.colorSlot] ?? tokens.domainOther))
              : (st.colorSlot < 0 ? tokens.clusterOther : (tokens.clusters[st.colorSlot] ?? tokens.clusterOther))
            const DomainIconComponent = domainIcon(null) // File fallback — icon names come from /graph/domains, not available here
            const displayLabel = st.isOther
              ? st.label  // "Other (N domains)" — already labeled by computeDomainLensStrata
              : titleCase(st.label)
            return (
              <div key={idx} className="flex items-center gap-1.5" role="listitem">
                <DomainIconComponent className="h-2.5 w-2.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="text-label-xxs text-muted-foreground">{displayLabel}</span>
                <span
                  className="ml-auto h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }} // drift-allowed: runtime-resolved token hex
                  aria-hidden="true"
                />
              </div>
            )
          })}
        </div>
      )}

      {/* Tooltip */}
      {tooltip && (
        <div
          className="pointer-events-none absolute z-50 rounded-md border border-border/60 bg-card/95 px-2.5 py-1.5 text-label-xs text-foreground shadow-lg backdrop-blur"
          style={{ left: tooltip.x, top: tooltip.y }} // drift-allowed: runtime pointer position
        >
          {tooltip.label}
        </div>
      )}

      {/* Visually-hidden DOM list of visible tracks for AT */}
      <ul className="sr-only" aria-label="Visible entity tracks">
        {data.tracks.slice(0, 24).map((t) => (
          <li key={t.canonical_id}>
            {t.name} — {t.total_mentions} mentions — {t.trust_state}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}
