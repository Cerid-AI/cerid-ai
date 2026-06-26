// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas mode — Meridian v2. The family's 2D analytic graph view.
// WebGL2 rendering via sigma.js v3; layout via force-atlas2 in a
// Web Worker; visual encoding per Meridian identity pipeline.
//
// AMENDMENT 4: the Sigma rebuild lifecycle (data-effect lines 124–224
// in the original) is structurally preserved. We re-theme and re-chrome
// only; the F2 callback-ref pattern is intact.

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery, keepPreviousData } from "@tanstack/react-query"
import Sigma from "sigma"
import type Graph from "graphology"
import { Settings2, X, BookOpen, Clock, Quote, Network, ChevronRight, Bookmark, ArrowLeft, Link2, Users, ShieldCheck } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { fetchNeighborhood } from "@/lib/api/graph"
import { adaptNeighborhood, recolorGraph } from "@/lib/graph/graphology-adapter"
import { applyLayout } from "@/lib/graph/apply-layout"
import {
  ATLAS_V2_NODE_PROGRAM_CLASSES as ATLAS_NODE_PROGRAM_CLASSES,
  ATLAS_V2_DEFAULT_NODE_TYPE as ATLAS_DEFAULT_NODE_TYPE,
  ATLAS_V2_EDGE_PROGRAM_CLASSES as ATLAS_EDGE_PROGRAM_CLASSES,
  ATLAS_V2_DEFAULT_EDGE_TYPE as ATLAS_DEFAULT_EDGE_TYPE,
} from "@/lib/graph/atlas-programs"
import { composeLensesWithTokens, LENS_ORDER, type LensId } from "@/lib/graph/lenses"
import {
  resolveMapTokens,
  applyParallelEdgeCurvature,
  type MapTokens,
} from "@/lib/graph/identity"
import { makeDrawNodeHover } from "@/lib/graph/draw-node-hover"
import { HOVER_INTENT_DELAY_MS } from "@/lib/graph/hover-intent"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import { useAtlasKeyboard } from "./use-atlas-keyboard"
import { AtlasA11yTree } from "./atlas-a11y-tree"
import { AtlasContextMenu, type AtlasContextMenuTarget } from "./atlas-context-menu"
import { AtlasSavedViews } from "./atlas-saved-views"
import type { AtlasView } from "@/lib/api/atlas-views"
import {
  NEIGHBORHOOD_HOPS_MAX_PROMOTED,
  type OnInspect,
  type OnFocusEntity,
} from "@/lib/graph/cycle4-contracts"

type AtlasSigma = Sigma<AtlasNodeAttributes, AtlasEdgeAttributes>
type AtlasGraph = Graph<AtlasNodeAttributes, AtlasEdgeAttributes>

// ---------------------------------------------------------------------------
// Config persistence
// ---------------------------------------------------------------------------

const CONFIG_KEY = "cerid-atlas-config"

interface AtlasConfig {
  labelDensity: "sparse" | "normal" | "dense"
  edgeLabels: boolean
}

function loadConfig(): AtlasConfig {
  try {
    const raw = localStorage.getItem(CONFIG_KEY)
    if (raw) return { labelDensity: "normal", edgeLabels: false, ...JSON.parse(raw) }
  } catch { /* SSR / parse error */ }
  return { labelDensity: "normal", edgeLabels: false }
}

function saveConfig(c: AtlasConfig) {
  try { localStorage.setItem(CONFIG_KEY, JSON.stringify(c)) } catch { /* quota / SSR */ }
}

// ---------------------------------------------------------------------------
// Hop-ring graticule canvas overlay
// ---------------------------------------------------------------------------

function useHopRingLayer(
  sigma: AtlasSigma | null,
  graph: AtlasGraph | null,
  focalEntity: string,
  hops: 1 | 2 | 3,
  tokens: MapTokens,
) {
  useEffect(() => {
    if (!sigma || !graph) return
    const s = sigma
    const container = s.getContainer()

    let canvas = container.querySelector<HTMLCanvasElement>("canvas[data-atlas-graticule]")
    if (!canvas) {
      canvas = document.createElement("canvas")
      canvas.dataset.atlasGraticule = "1"
      canvas.style.position = "absolute"
      canvas.style.inset = "0"
      canvas.style.pointerEvents = "none"
      container.appendChild(canvas)
    }
    const cvs = canvas

    function resize() {
      const dpr = window.devicePixelRatio || 1
      cvs.width = container.offsetWidth * dpr
      cvs.height = container.offsetHeight * dpr
      cvs.style.width = `${container.offsetWidth}px`
      cvs.style.height = `${container.offsetHeight}px`
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(container)

    function draw() {
      const ctx = cvs.getContext("2d")
      if (!ctx || !graph) return
      const dpr = window.devicePixelRatio || 1
      ctx.clearRect(0, 0, cvs.width, cvs.height)
      if (!graph.hasNode(focalEntity)) return

      ctx.save()
      ctx.scale(dpr, dpr)

      // Compute per-hop max distance from focal node in viewport coords
      const focalDisplay = s.getNodeDisplayData(focalEntity)
      if (!focalDisplay) { ctx.restore(); return }
      const focalVp = { x: focalDisplay.x, y: focalDisplay.y }

      // BFS to assign hop distances
      const hopDist = new Map<string, number>([[focalEntity, 0]])
      const queue = [focalEntity]
      while (queue.length) {
        const cur = queue.shift()!
        const d = hopDist.get(cur)!
        if (d >= hops) continue
        graph.forEachNeighbor(cur, (nb) => {
          if (!hopDist.has(nb)) {
            hopDist.set(nb, d + 1)
            queue.push(nb)
          }
        })
      }

      // Max viewport distance per hop
      const maxDist = new Array(hops).fill(0) as number[]
      graph.forEachNode((id) => {
        const d = hopDist.get(id)
        if (d === undefined || d === 0 || d > hops) return
        const nd = s.getNodeDisplayData(id)
        if (!nd) return
        const vp = s.graphToViewport({ x: nd.x, y: nd.y })
        const fvp = s.graphToViewport({ x: focalVp.x, y: focalVp.y })
        const dist = Math.hypot(vp.x - fvp.x, vp.y - fvp.y)
        maxDist[d - 1] = Math.max(maxDist[d - 1], dist)
      })

      const focalVpCoord = s.graphToViewport({ x: focalVp.x, y: focalVp.y })

      // Draw concentric rings
      for (let h = 1; h <= hops; h++) {
        const r = maxDist[h - 1]
        if (r < 4) continue
        ctx.beginPath()
        ctx.arc(focalVpCoord.x, focalVpCoord.y, r, 0, Math.PI * 2)
        ctx.globalAlpha = 0.12
        ctx.strokeStyle = tokens.interaction
        ctx.lineWidth = 1
        ctx.setLineDash([4, 6])
        ctx.stroke()
        ctx.setLineDash([])

        // Whisper caption at top of ring
        ctx.globalAlpha = 0.35
        ctx.font = "10px var(--font-sans, system-ui, sans-serif)"
        ctx.fillStyle = tokens.foreground
        ctx.textAlign = "center"
        ctx.textBaseline = "bottom"
        ctx.fillText(`${h} hop${h > 1 ? "s" : ""}`, focalVpCoord.x, focalVpCoord.y - r - 4)
      }
      ctx.restore()
    }

    s.on("afterRender", draw)
    draw()

    return () => {
      s.off("afterRender", draw)
      ro.disconnect()
      cvs.remove()
    }
  }, [sigma, graph, focalEntity, hops, tokens])
}

// ---------------------------------------------------------------------------
// Arrival ping — 2s teal highlight pulse on the focal node
// ---------------------------------------------------------------------------

function useArrivalPing(
  sigma: AtlasSigma | null,
  graph: AtlasGraph | null,
  focalEntity: string,
  pingKey: number,  // increment to re-trigger
) {
  useEffect(() => {
    if (!sigma || !graph || !graph.hasNode(focalEntity)) return
    const s = sigma

    // Set a temporary "arriving" attribute for the focal node
    graph.setNodeAttribute(focalEntity, "highlighted", true)
    s.refresh()

    const timer = setTimeout(() => {
      try {
        graph.setNodeAttribute(focalEntity, "highlighted", false)
        s.refresh()
      } catch { /* node may have been removed */ }
    }, 2000)

    return () => {
      clearTimeout(timer)
      try { graph.setNodeAttribute(focalEntity, "highlighted", false) } catch { /* ok */ }
    }
  }, [sigma, graph, focalEntity, pingKey])
}

// ---------------------------------------------------------------------------
// Entity card (hover tooltip + click-to-pin)
// ---------------------------------------------------------------------------

// Trust-band legend: one-line human label for each trust state.
const TRUST_LABELS: Record<string, string> = {
  verified: "verified",
  partial: "partial",
  unverified: "unverified",
  contradicted: "contradicted",
  unknown: "unknown",
}

interface EntityCardProps {
  nodeId: string
  attrs: AtlasNodeAttributes
  /** Screen position of the card anchor */
  screenPos: { x: number; y: number }
  tokens: MapTokens
  graph: AtlasGraph | null
  onOpenWiki: (id: string) => void
  onOpenTimeline: (id: string) => void
  onMakeFocal: (id: string) => void
  onCiteInChat: (id: string, name: string) => void
  onClose: () => void
  /** True = pinned; false = hover tooltip */
  pinned: boolean
}

export function EntityCard({
  nodeId,
  attrs,
  screenPos,
  tokens,
  graph,
  onOpenWiki,
  onOpenTimeline,
  onMakeFocal,
  onCiteInChat,
  onClose,
  pinned,
}: EntityCardProps) {
  // Keep card within viewport bounds
  const cardW = 260
  const cardH = 260
  const left = Math.min(screenPos.x + 12, window.innerWidth - cardW - 8)
  const top = Math.min(screenPos.y - 8, window.innerHeight - cardH - 8)

  const borderHex = attrs.borderColor ?? tokens.trustUnverified

  // Degree + top-3 neighbors from live graph
  const degree = graph?.degree(nodeId) ?? 0
  const topNeighbors: Array<{ id: string; name: string; edgeType: string }> = []
  if (graph?.hasNode(nodeId)) {
    graph.forEachNeighbor(nodeId, (nbId, nbAttrs) => {
      if (topNeighbors.length >= 3) return
      // Get the edge kind (co_mention vs similar) from the first edge between them
      let edgeType = "co_mention"
      graph.forEachEdge(nodeId, nbId, (_key, edgeAttrs: AtlasEdgeAttributes) => {
        edgeType = edgeAttrs.type ?? "co_mention"
      })
      topNeighbors.push({ id: nbId, name: nbAttrs.name, edgeType })
    })
  }

  const trustLabel = TRUST_LABELS[attrs.trust_state] ?? "unknown"

  return (
    <div
      role={pinned ? "dialog" : "tooltip"}
      aria-label={`Entity details: ${attrs.name}`}
      style={{ left, top, borderLeftColor: borderHex }}
      className="fixed z-50 w-[260px] rounded-lg border border-border/60 border-l-4 bg-card/95 shadow-xl backdrop-blur"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 px-3 pt-3 pb-1">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{attrs.name}</p>
          <div className="mt-0.5 flex items-center gap-1.5 text-label-xs text-muted-foreground">
            <span className="rounded bg-accent/50 px-1">{attrs.type}</span>
            <span>{attrs.mention_count} mentions</span>
          </div>
        </div>
        {pinned && (
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent/40"
            aria-label="Close"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>

      {/* Connection count + trust band */}
      <div className="px-3 pb-2 flex flex-col gap-1">
        <div className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
          <Link2 className="h-3 w-3 shrink-0" aria-hidden="true" />
          <span data-testid="entity-card-degree">{degree} {degree === 1 ? "connection" : "connections"}</span>
        </div>
        {attrs.trust_state !== "unknown" && (
          <div className="flex items-center gap-1.5 text-label-xs text-muted-foreground">
            <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span
              data-testid="entity-card-trust"
              aria-label={`Trust: ${trustLabel}`}
            >
              {trustLabel}
            </span>
            <span aria-hidden="true" className="text-muted-foreground/40">·</span>
            <span className="text-muted-foreground/60">verified / partial / unverified / contradicted</span>
          </div>
        )}

        {/* Top-3 neighbors */}
        {topNeighbors.length > 0 && (
          <div className="mt-0.5">
            <div className="mb-0.5 flex items-center gap-1 text-label-xs text-muted-foreground/70">
              <Users className="h-3 w-3 shrink-0" aria-hidden="true" />
              <span>Top neighbors</span>
            </div>
            <ul className="flex flex-col gap-0.5" aria-label="Top neighbors">
              {topNeighbors.map((nb) => (
                <li key={nb.id} className="flex items-center gap-1.5 text-label-xs text-foreground/70">
                  <span
                    className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/40"
                    aria-hidden="true"
                  />
                  <span className="truncate">{nb.name}</span>
                  <span className="shrink-0 text-muted-foreground/40">
                    {nb.edgeType === "similar" ? "≈" : "·"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {!pinned && (
        <p className="px-3 pb-1 text-label-xs text-muted-foreground">Click to pin</p>
      )}

      {/* Actions (visible when pinned) */}
      {pinned && (
        <div className="border-t border-border/40 px-1 py-1">
          <button type="button" onClick={() => onOpenWiki(nodeId)}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-foreground/80 hover:bg-accent/40">
            <BookOpen className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            Open in Wiki
          </button>
          <button type="button" onClick={() => onOpenTimeline(nodeId)}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-foreground/80 hover:bg-accent/40">
            <Clock className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            Open in Timeline
          </button>
          <button type="button" onClick={() => { onMakeFocal(nodeId); onClose() }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-foreground/80 hover:bg-accent/40">
            <Network className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            Make focal
          </button>
          <button type="button" onClick={() => { onCiteInChat(nodeId, attrs.name); onClose() }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-foreground/80 hover:bg-accent/40">
            <Quote className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            Cite in chat
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pill toolbar — lens radiogroup + hop stepper + entity-type chips +
// stats + config popover
// ---------------------------------------------------------------------------

interface PillToolbarProps {
  activeLenses: Set<LensId>
  onLensToggle: (id: LensId) => void
  hops: 1 | 2 | 3
  onHopsChange: (h: 1 | 2 | 3) => void
  typeCounts: Map<string, number>
  activeTypeChips: Set<string>
  onTypeChipToggle: (type: string) => void
  totalNodes: number
  totalEdges: number
  config: AtlasConfig
  onConfigChange: (patch: Partial<AtlasConfig>) => void
  savedViewsSlot?: ReactNode
  onBackToOverview?: () => void
  includeIsolated: boolean
  onIncludeIsolatedChange: (v: boolean) => void
  isolatedCount: number
}

function PillToolbar({
  activeLenses,
  onLensToggle,
  hops,
  onHopsChange,
  typeCounts,
  activeTypeChips,
  onTypeChipToggle,
  totalNodes,
  totalEdges,
  config,
  onConfigChange,
  savedViewsSlot,
  onBackToOverview,
  includeIsolated,
  onIncludeIsolatedChange,
  isolatedCount,
}: PillToolbarProps) {
  return (
    <div className="absolute inset-x-0 top-0 z-10 flex items-center gap-1.5 border-b border-border/40 bg-card/80 px-3 py-1.5 backdrop-blur">
      {/* Back to overview */}
      {onBackToOverview && (
        <>
          <button
            type="button"
            onClick={onBackToOverview}
            aria-label="Back to overview"
            className="flex items-center gap-1 rounded-full border border-border/60 px-2 py-0.5 text-label-xs text-muted-foreground hover:bg-accent/30"
          >
            <ArrowLeft className="h-3 w-3" aria-hidden="true" />
            Overview
          </button>
          <div className="h-4 w-px bg-border/40" aria-hidden="true" />
        </>
      )}
      {/* Lens radiogroup */}
      <div role="radiogroup" aria-label="Analysis lens" className="flex items-center gap-0.5">
        {LENS_ORDER.map((lens) => {
          const active = activeLenses.has(lens.id)
          return (
            <button
              key={lens.id}
              type="button"
              role="radio"
              aria-checked={active}
              title={lens.description}
              onClick={() => onLensToggle(lens.id)}
              className={`flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-label-xs transition-colors ${
                active
                  ? "border-transparent bg-accent text-accent-foreground"
                  : "border-border/60 text-muted-foreground hover:bg-accent/30"
              }`}
            >
              <span
                className="h-1.5 w-1.5 rounded-full shrink-0"
                style={{ backgroundColor: lens.legendColor }}
                aria-hidden="true"
              />
              {lens.label}
            </button>
          )
        })}
      </div>

      <div className="h-4 w-px bg-border/40" aria-hidden="true" />

      {/* Hop stepper 1|NEIGHBORHOOD_HOPS_MAX_PROMOTED (A4: hops=3 URL-reachable not promoted) */}
      <div role="group" aria-label="Hop depth" className="flex items-center gap-0.5">
        <span className="text-label-xs text-muted-foreground">Hops</span>
        {(Array.from({ length: NEIGHBORHOOD_HOPS_MAX_PROMOTED }, (_, i) => (i + 1) as 1 | 2 | 3)).map((h) => (
          <button
            key={h}
            type="button"
            aria-pressed={hops === h}
            onClick={() => onHopsChange(h)}
            className={`h-5 w-5 rounded text-label-xs font-medium transition-colors ${
              hops === h
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/30"
            }`}
            title={`${h} hop${h > 1 ? "s" : ""} (key ${h})`}
          >
            {h}
          </button>
        ))}
      </div>

      <div className="h-4 w-px bg-border/40" aria-hidden="true" />

      {/* Entity-type chips (dim-don't-remove) */}
      <div role="group" aria-label="Entity type filter" className="flex flex-wrap items-center gap-0.5">
        {Array.from(typeCounts.entries()).map(([type, count]) => {
          const active = !activeTypeChips.size || activeTypeChips.has(type)
          return (
            <button
              key={type}
              type="button"
              aria-pressed={activeTypeChips.has(type)}
              onClick={() => onTypeChipToggle(type)}
              className={`rounded-full border px-2 py-0.5 text-label-xs transition-colors ${
                active
                  ? "border-border/60 text-foreground/80 hover:bg-accent/30"
                  : "border-border/30 text-muted-foreground/40"
              }`}
            >
              {type}
              <span className="ml-1 text-muted-foreground/60">{count}</span>
            </button>
          )
        })}
      </div>

      {/* Show isolated pill — hidden when count is 0 */}
      {isolatedCount > 0 && (
        <>
          <div className="h-4 w-px bg-border/40" aria-hidden="true" />
          <label className="flex cursor-pointer items-center gap-1.5 text-label-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={includeIsolated}
              disabled={isolatedCount === 0}
              onChange={(e) => onIncludeIsolatedChange(e.target.checked)}
              className="rounded border-border/60"
            />
            Show isolated ({isolatedCount})
          </label>
        </>
      )}

      <div className="grow" />

      {/* Stats */}
      {totalNodes > 0 && (
        <span className="text-label-xs text-muted-foreground">
          {totalNodes} {totalNodes === 1 ? "entity" : "entities"} · {totalEdges} connections
        </span>
      )}

      {/* Saved-views trigger (rendered from Atlas so the Popover root wraps both) */}
      {savedViewsSlot}

      {/* Config popover */}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label="Atlas settings"
            className="rounded border border-border/60 p-1 text-muted-foreground hover:bg-accent/30"
          >
            <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-52 p-3">
          <div className="flex flex-col gap-2">
            <div>
              <div className="mb-1 text-label-xs font-medium text-muted-foreground">Label density</div>
              <div className="flex gap-0.5" role="radiogroup" aria-label="Label density">
                {(["sparse", "normal", "dense"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={config.labelDensity === v}
                    onClick={() => onConfigChange({ labelDensity: v })}
                    className={`rounded px-1.5 py-0.5 text-label-xs capitalize transition-colors ${
                      config.labelDensity === v
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/30"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-label-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={config.edgeLabels}
                onChange={(e) => onConfigChange({ edgeLabels: e.target.checked })}
                className="rounded border-border/60"
              />
              Edge labels
            </label>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Atlas props
// ---------------------------------------------------------------------------

export interface AtlasProps {
  entity: string
  hops?: 1 | 2 | 3
  filter?: string
  /**
   * Unified click contract (Cycle 4): pin/inspect only — never mode-switch.
   * Replaces old onNodeClick. Called on node click to pin the entity card.
   */
  onInspect?: OnInspect
  /**
   * Explicit refocus: re-centers the neighborhood on a different entity.
   * Called by "Make focal" card action only.
   */
  onFocusEntity?: OnFocusEntity
  /** Callback when user changes hop depth (from stepper or 1/2/3 keys) */
  onHopsChange?: (hops: 1 | 2 | 3) => void
  onToggleLensMenu?: () => void
  onSearchPalette?: () => void
  onCiteInChat?: (entityId: string, entityName: string) => void
  onOpenInWiki?: (entityId: string) => void
  /** Open entity in Timeline (new in Meridian) */
  onOpenInTimeline?: (entityId: string) => void
  onRestoreView?: (view: AtlasView) => void
  /** Called when user clicks "Back to overview" in the toolbar */
  onBackToOverview?: () => void
}

interface LayoutStatus {
  state: "idle" | "fetching" | "laying-out" | "ready" | "error"
  message?: string
  progressPercent?: number
}

// Hover intent delay: shared with Cartographer (see lib/graph/hover-intent.ts)

// ---------------------------------------------------------------------------
// Atlas
// ---------------------------------------------------------------------------

export function Atlas({
  entity,
  hops = 2,
  filter,
  onInspect,
  onFocusEntity,
  onHopsChange,
  onToggleLensMenu,
  onSearchPalette,
  onCiteInChat,
  onOpenInWiki,
  onOpenInTimeline,
  onRestoreView,
  onBackToOverview,
}: AtlasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<AtlasSigma | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Callback refs — F2 fix preserved from v1
  const onInspectRef = useRef(onInspect)
  const onFocusEntityRef = useRef(onFocusEntity)
  useEffect(() => {
    onInspectRef.current = onInspect
    onFocusEntityRef.current = onFocusEntity
  }, [onInspect, onFocusEntity])

  const [status, setStatus] = useState<LayoutStatus>({ state: "idle" })
  const prevQueryKeyRef = useRef<string>("")
  const [sigmaInstance, setSigmaInstance] = useState<AtlasSigma | null>(null)
  const [graphInstance, setGraphInstance] = useState<AtlasGraph | null>(null)
  const [activeLenses, setActiveLenses] = useState<Set<LensId>>(new Set())
  const [lensPanelVisible, setLensPanelVisible] = useState(true)
  const [contextMenuTarget, setContextMenuTarget] = useState<AtlasContextMenuTarget | null>(null)
  const [activeTypeChips, setActiveTypeChips] = useState<Set<string>>(new Set())
  const [config, setConfig] = useState<AtlasConfig>(loadConfig)
  const [arrivalPingKey, setArrivalPingKey] = useState(0)
  // Isolated-node toggle — off by default (graph shows connected core)
  const [includeIsolated, setIncludeIsolated] = useState(false)

  // Hover tooltip + pinned entity card
  const [hoverNode, setHoverNode] = useState<{ id: string; pos: { x: number; y: number } } | null>(null)
  const [pinnedNode, setPinnedNode] = useState<{ id: string; pos: { x: number; y: number } } | null>(null)

  // Theme tokens — resolve on mount and re-resolve on theme change
  const [tokens, setTokens] = useState<MapTokens>(() =>
    typeof document !== "undefined" ? resolveMapTokens(document.documentElement) : {
      clusters: Array(8).fill("#888888") as string[], // drift-allowed: SSR fallback only
      clusterOther: "#888888", // drift-allowed: SSR fallback only
      domains: Array(12).fill("#888888") as string[], // drift-allowed: SSR fallback only
      domainOther: "#666666",   // drift-allowed: SSR fallback only
      edge: "#888888",          // drift-allowed: SSR fallback only
      dim: "#888888",           // drift-allowed: SSR fallback only
      interaction: "#00C8B4",   // drift-allowed: SSR fallback only
      foreground: "#111111",    // drift-allowed: SSR fallback only
      background: "#f5f5f5",    // drift-allowed: SSR fallback only
      trustVerified: "#555555", // drift-allowed: SSR fallback only
      trustPartial: "#777777",  // drift-allowed: SSR fallback only
      trustUnverified: "#999999", // drift-allowed: SSR fallback only
      graphite: "#6b7080",      // drift-allowed: SSR fallback only
      grid: "#eeeeee",          // drift-allowed: SSR fallback only
      fontSans: "system-ui, sans-serif", // drift-allowed: SSR fallback only
    }
  )

  useEffect(() => {
    const observer = new MutationObserver(() => {
      const fresh = resolveMapTokens(document.documentElement)
      setTokens(fresh)
      // Push new colors into sigma settings + graph
      const s = sigmaRef.current
      const g = graphInstance
      if (s && g) {
        recolorGraph(g, fresh)
        s.setSetting("labelColor", { color: fresh.foreground })
        s.setSetting("defaultEdgeColor", fresh.edge)
        s.setSetting("defaultNodeColor", fresh.clusterOther)
        s.setSetting("defaultDrawNodeHover", makeDrawNodeHover(fresh))
        s.refresh()
      }
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [graphInstance])

  // Deep-link param consumption — ?lens= and ?hops=
  const [deepLinkConsumed, setDeepLinkConsumed] = useState(false)
  useEffect(() => {
    if (deepLinkConsumed) return
    const params = new URLSearchParams(window.location.search)
    const lensParam = params.get("lens")
    const hopsParam = params.get("hops")
    if (lensParam) {
      const ids = lensParam.split(",").filter((id): id is LensId =>
        ["contradiction", "open-question", "provenance", "quality", "domain"].includes(id)
      )
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional one-shot URL param read on mount; no subscriptions, no cascading renders
      if (ids.length) setActiveLenses(new Set(ids))
    }
    if (hopsParam && ["1", "2", "3"].includes(hopsParam)) {
      onHopsChange?.(parseInt(hopsParam) as 1 | 2 | 3)
    }
    setDeepLinkConsumed(true)
    // Trigger arrival ping for inbound deep-link
    if (lensParam || hopsParam) setArrivalPingKey((k) => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional one-shot on mount
  }, [])

  const { data, isLoading, isFetching, isError, error } = useQuery({
    queryKey: ["graph-neighborhood", entity, hops, filter ?? null, includeIsolated],
    queryFn: ({ signal }) => fetchNeighborhood(entity, hops, filter, { signal, includeIsolated }),
    staleTime: 30_000,
    enabled: Boolean(entity),
    placeholderData: keepPreviousData,
  })

  // Reset layout status when query key changes so refocus always shows loading/error correctly.
  const queryKey = `${entity}:${hops}:${filter ?? ""}`
  useEffect(() => {
    if (prevQueryKeyRef.current && prevQueryKeyRef.current !== queryKey) {
      setStatus({ state: "idle" })
    }
    prevQueryKeyRef.current = queryKey
  // eslint-disable-next-line react-hooks/exhaustive-deps -- queryKey is a stable derived string; setStatus is stable
  }, [queryKey])

  // Precedence: error > fetching/loading > layout-status.
  // keepPreviousData means isFetching can be true while previous data is still shown.
  const renderedStatus: LayoutStatus = useMemo(() => {
    if (isError) {
      return { state: "error", message: error instanceof Error ? error.message : "Graph fetch failed" }
    }
    if (isLoading || (isFetching && status.state === "idle")) {
      return { state: "fetching" }
    }
    return status
  }, [isError, isLoading, isFetching, status, error])

  // Entity type counts for chip toolbar
  const typeCounts = useMemo((): Map<string, number> => {
    if (!graphInstance) return new Map()
    const counts = new Map<string, number>()
    graphInstance.forEachNode((_, attrs) => {
      counts.set(attrs.type, (counts.get(attrs.type) ?? 0) + 1)
    })
    return counts
  }, [graphInstance])

  // Graph build + Sigma lifecycle (AMENDMENT 4: structurally preserved from v1)
  useEffect(() => {
    const container = containerRef.current
    if (!container || !data) return

    let cancelled = false
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    const currentTokens = resolveMapTokens(document.documentElement)
    const graph = adaptNeighborhood(data, currentTokens)
    // Parallel edge fanning is async (lazy dynamic import to avoid WebGL in tests)
    void applyParallelEdgeCurvature(graph)
    setGraphInstance(graph)

    if (sigmaRef.current) {
      sigmaRef.current.kill()
      sigmaRef.current = null
      setSigmaInstance(null)
    }
    if (graph.order === 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
      setStatus({ state: "ready", message: "No entities in scope" })
      return
    }

    const sigma = new Sigma(graph, container, {
      // Tolerate a container that has no measured width yet at mount (e.g. the
      // MiniGraph embedded in the narrow wiki infobox column before layout
      // settles) — sigma renders once the container resizes. No-op for the
      // full-width Atlas tab. Without this it throws "Container has no width".
      allowInvalidContainer: true,
      renderLabels: true,
      labelSize: config.labelDensity === "dense" ? 13 : config.labelDensity === "sparse" ? 9 : 11,
      labelWeight: "500",
      defaultNodeColor: currentTokens.clusterOther,
      defaultEdgeColor: currentTokens.edge,
      labelColor: { color: currentTokens.foreground },
      defaultDrawNodeHover: makeDrawNodeHover(currentTokens),
      nodeProgramClasses: ATLAS_NODE_PROGRAM_CLASSES,
      edgeProgramClasses: ATLAS_EDGE_PROGRAM_CLASSES,
      defaultNodeType: ATLAS_DEFAULT_NODE_TYPE,
      defaultEdgeType: ATLAS_DEFAULT_EDGE_TYPE,
    }) as unknown as AtlasSigma
    sigmaRef.current = sigma
    setSigmaInstance(sigma)

    // Focal node emphasis
    if (graph.hasNode(entity)) {
      const currentSize = graph.getNodeAttribute(entity, "size")
      graph.setNodeAttribute(entity, "size", Math.max(currentSize, 12))
      graph.setNodeAttribute(entity, "forceLabel", true)
    }

    sigma.on("clickNode", ({ node, event }) => {
      const original = event.original as MouseEvent | undefined
      const pos = { x: original?.clientX ?? 0, y: original?.clientY ?? 0 }
      setPinnedNode({ id: node, pos })
      // Animate camera to node ~500ms
      const nd = sigma.getNodeDisplayData(node)
      if (nd) {
        sigma.getCamera().animate({ x: nd.x, y: nd.y }, { duration: 500 })
      }
      // Unified click contract (Cycle 4): pin only — no mode switch.
      onInspectRef.current?.(node)
    })

    // Double-click removed (Cycle 4 click contract: Enter = inspect, not navigate).
    sigma.on("doubleClickNode", ({ event }) => {
      // Suppress sigma's default double-click zoom — no action.
      try { (event.original as MouseEvent | undefined)?.preventDefault?.() } catch { /* ok */ }
    })

    sigma.on("enterNode", ({ node, event }) => {
      const original = event.original as MouseEvent | undefined
      const pos = { x: original?.clientX ?? 0, y: original?.clientY ?? 0 }
      // 300ms intent delay
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
      hoverTimerRef.current = setTimeout(() => {
        setHoverNode({ id: node, pos })
      }, HOVER_INTENT_DELAY_MS)
      try { graph.setNodeAttribute(node, "highlighted", true) } catch { /* removed mid-event */ }
    })

    sigma.on("leaveNode", ({ node }) => {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current)
      setHoverNode(null)
      try { graph.setNodeAttribute(node, "highlighted", false) } catch { /* ok */ }
    })

    sigma.on("clickStage", () => {
      // Unpin on stage click
      setPinnedNode(null)
      setHoverNode(null)
    })

    sigma.on("rightClickNode", ({ node, event }) => {
      const attrs = graph.getNodeAttributes(node)
      const original = event.original as MouseEvent | undefined
      original?.preventDefault?.()
      setContextMenuTarget({
        entityId: node,
        entityName: attrs.name ?? node,
        x: original?.clientX ?? 0,
        y: original?.clientY ?? 0,
      })
    })

    // Prevent sigma's double-click zoom on stage (doesn't affect node dbl-click)
    sigma.on("doubleClickStage", ({ event }) => {
      try { (event.original as MouseEvent | undefined)?.preventDefault?.() } catch { /* ok */ }
    })

    setStatus({ state: "laying-out", progressPercent: 0 })

    // Pin focal node at centroid during FA2 (sigma warm-start)
    if (graph.hasNode(entity)) {
      // Set initial position to center so focal starts there
      graph.setNodeAttribute(entity, "x", 0)
      graph.setNodeAttribute(entity, "y", 0)
    }

    applyLayout(graph, {
      iterations: graph.order > 500 ? 150 : 250,
      signal: abortRef.current.signal,
      onProgress: (iter, total) => {
        if (!cancelled) {
          setStatus({ state: "laying-out", progressPercent: Math.round((iter / total) * 100) })
        }
      },
    })
      .then(() => {
        if (cancelled) return
        sigmaRef.current?.refresh()
        setStatus({ state: "ready" })
        // Arrival ping after layout completes
        setArrivalPingKey((k) => k + 1)
      })
      .catch((err) => {
        if (cancelled) return
        if ((err as Error).name === "AbortError") return
        setStatus({ state: "error", message: err instanceof Error ? err.message : "Layout failed" })
      })

    return () => {
      cancelled = true
      abortRef.current?.abort()
      sigmaRef.current?.kill()
      sigmaRef.current = null
    }
    // Callbacks are read via refs (F2). Only `data` triggers full rebuild + relayout.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: config.labelDensity changes don't need full rebuild
  }, [data])

  // Re-bind sigma reducers when active lens set / type chips change
  useEffect(() => {
    if (!sigmaInstance || !graphInstance) return
    const lensIds = Array.from(activeLenses)

    // Type chip dimming: ghost filtered-out nodes
    const hasTChips = activeTypeChips.size > 0

    if (lensIds.length === 0 && !hasTChips) {
      sigmaInstance.setSetting("nodeReducer", null)
      sigmaInstance.setSetting("edgeReducer", null)
    } else if (lensIds.length > 0) {
      const { nodeReducer, edgeReducer } = composeLensesWithTokens(lensIds, tokens, graphInstance)
      sigmaInstance.setSetting("nodeReducer", hasTChips
        ? (node: string, attrs: AtlasNodeAttributes) => {
            const reduced = nodeReducer(node, attrs)
            if (!activeTypeChips.has(attrs.type)) {
              return { ...reduced, color: tokens.clusterOther, size: Math.max(reduced.size * 0.5, 3) }
            }
            return reduced
          }
        : nodeReducer,
      )
      sigmaInstance.setSetting("edgeReducer", edgeReducer)
    } else {
      // Only type chip dimming
      sigmaInstance.setSetting("nodeReducer", (_node: string, attrs: AtlasNodeAttributes) => {
        if (!activeTypeChips.has(attrs.type)) {
          return { ...attrs, color: tokens.clusterOther, size: Math.max(attrs.size * 0.5, 3) }
        }
        return attrs
      })
      sigmaInstance.setSetting("edgeReducer", null)
    }
    sigmaInstance.refresh()
  }, [sigmaInstance, graphInstance, activeLenses, activeTypeChips, tokens])

  const handleLensToggle = useCallback((id: LensId) => {
    setActiveLenses((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleTypeChipToggle = useCallback((type: string) => {
    setActiveTypeChips((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }, [])

  const handleToggleLensMenu = useCallback(() => {
    setLensPanelVisible((v) => !v)
    onToggleLensMenu?.()
  }, [onToggleLensMenu])

  const handleConfigChange = useCallback((patch: Partial<AtlasConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...patch }
      saveConfig(next)
      return next
    })
  }, [])

  const handleHopsChange = useCallback((h: 1 | 2 | 3) => {
    onHopsChange?.(h)
  }, [onHopsChange])

  const handleUnpin = useCallback(() => {
    setPinnedNode(null)
  }, [])

  // Make focal: animate camera then trigger parent explicit refocus (A4 contract)
  const handleMakeFocal = useCallback((nodeId: string) => {
    const s = sigmaRef.current
    if (s) {
      const nd = s.getNodeDisplayData(nodeId)
      if (nd) s.getCamera().animate({ x: nd.x, y: nd.y }, { duration: 500 })
    }
    onFocusEntity?.(nodeId)
    setArrivalPingKey((k) => k + 1)
  }, [onFocusEntity])

  // Hop-ring graticule overlay
  useHopRingLayer(sigmaInstance, graphInstance, entity, hops, tokens)

  // Arrival ping on focal node
  useArrivalPing(sigmaInstance, graphInstance, entity, arrivalPingKey)

  const { selectedNodeId, setSelectedNodeId, onKeyDown } = useAtlasKeyboard({
    sigma: sigmaInstance,
    graph: graphInstance,
    focalEntity: entity,
    // Cycle 4: Enter = inspect (pin card), not navigate. A5 keyboard contract.
    onActivate: (id) => { onInspect?.(id) },
    onToggleLensMenu: handleToggleLensMenu,
    onSearchPalette,
    onHopsChange: handleHopsChange,
    onUnpin: handleUnpin,
  })

  // Resolve hovered/pinned node attrs for card rendering
  const cardNodeId = pinnedNode?.id ?? hoverNode?.id ?? null
  const cardPos = pinnedNode?.pos ?? hoverNode?.pos ?? null
  const cardAttrs = cardNodeId && graphInstance?.hasNode(cardNodeId)
    ? graphInstance.getNodeAttributes(cardNodeId)
    : null
  const cardIsPinned = pinnedNode !== null

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      ref={wrapperRef}
      className="relative h-full w-full bg-background outline-none focus-visible:ring-2 focus-visible:ring-brand"
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      role="application"
      aria-roledescription="knowledge graph"
      aria-label={`Atlas view of ${entity}'s neighborhood`}
      aria-activedescendant={selectedNodeId ?? undefined}
      onKeyDown={onKeyDown}
    >
      {/* Pill toolbar (lens + hops + type chips + stats + saved-views + config) */}
      {lensPanelVisible && renderedStatus.state !== "error" && (
        <PillToolbar
          activeLenses={activeLenses}
          onLensToggle={handleLensToggle}
          hops={hops}
          onHopsChange={handleHopsChange}
          typeCounts={typeCounts}
          activeTypeChips={activeTypeChips}
          onTypeChipToggle={handleTypeChipToggle}
          totalNodes={graphInstance?.order ?? 0}
          totalEdges={graphInstance?.size ?? 0}
          config={config}
          onConfigChange={handleConfigChange}
          onBackToOverview={onBackToOverview}
          includeIsolated={includeIsolated}
          onIncludeIsolatedChange={setIncludeIsolated}
          isolatedCount={data?.isolated_count ?? 0}
          savedViewsSlot={
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label="Saved views"
                  className="rounded border border-border/60 p-1 text-muted-foreground hover:bg-accent/30"
                >
                  <Bookmark className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-64 p-0">
                <AtlasSavedViews
                  focalEntity={entity}
                  hops={hops}
                  filter={filter}
                  activeLenses={activeLenses}
                  activeChips={activeTypeChips}
                  getCameraState={() => {
                    const sigma = sigmaInstance
                    if (!sigma) return null
                    const cam = sigma.getCamera().getState()
                    return { x: cam.x, y: cam.y, ratio: cam.ratio, angle: cam.angle }
                  }}
                  onRestore={(view) => {
                    setActiveLenses(new Set(view.lenses as LensId[]))
                    if (view.chips?.length) setActiveTypeChips(new Set(view.chips))
                    onRestoreView?.(view)
                  }}
                />
              </PopoverContent>
            </Popover>
          }
        />
      )}

      {/* Sigma canvas container — offset top to clear the toolbar */}
      <div
        ref={containerRef}
        className={`h-full w-full ${lensPanelVisible && renderedStatus.state !== "error" ? "pt-10" : ""}`}
        aria-hidden="true"
      />

      <AtlasA11yTree
        graph={graphInstance}
        selectedNodeId={selectedNodeId}
        onSelect={setSelectedNodeId}
        focalEntity={entity}
      />

      <AtlasContextMenu
        target={contextMenuTarget}
        onClose={() => setContextMenuTarget(null)}
        onCite={(id, name) => onCiteInChat?.(id, name)}
        onOpenWiki={(id) => onOpenInWiki?.(id)}
      />

      {/* Hover tooltip / pinned entity card */}
      {cardAttrs && cardPos && (
        <EntityCard
          nodeId={cardNodeId!}
          attrs={cardAttrs}
          screenPos={cardPos}
          tokens={tokens}
          graph={graphInstance}
          onOpenWiki={(id) => { onOpenInWiki?.(id); setPinnedNode(null) }}
          onOpenTimeline={(id) => { onOpenInTimeline?.(id); setPinnedNode(null) }}
          onMakeFocal={handleMakeFocal}
          onCiteInChat={(id, name) => { onCiteInChat?.(id, name); setPinnedNode(null) }}
          onClose={() => setPinnedNode(null)}
          pinned={cardIsPinned}
        />
      )}

      {/* 4-state matrix overlays */}
      {renderedStatus.state === "fetching" && (
        <div className="absolute inset-0 flex flex-col gap-3 p-4 pt-14" aria-busy="true" aria-label="Loading graph">
          <div className="flex gap-2">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-5 w-16" />
            <Skeleton className="h-5 w-24" />
          </div>
          <Skeleton className="flex-1 w-full rounded-lg" />
        </div>
      )}
      {renderedStatus.state === "laying-out" && (
        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-2 rounded-full border border-border/60 bg-card/90 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
          <span
            className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse"
            aria-hidden="true"
          />
          Computing layout… {renderedStatus.progressPercent ?? 0}%
        </div>
      )}
      {renderedStatus.state === "error" && (
        <div className="absolute inset-0 flex items-center justify-center p-6">
          <Alert variant="destructive" className="max-w-sm">
            <AlertDescription>
              {renderedStatus.message ?? "Atlas failed to load"}
            </AlertDescription>
          </Alert>
        </div>
      )}
      {renderedStatus.state === "ready" && graphInstance?.order === 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-8 text-center">
          <ChevronRight className="h-8 w-8 text-muted-foreground/30" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">No entities in this neighborhood</p>
        </div>
      )}
    </div>
  )
}
