// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas mode — the everyday 2D analytic graph view inside the Subjects
// pane. WebGL2 rendering via sigma.js v3; layout via force-atlas2 in a
// Web Worker; visual encoding per cerid-design-system-v2.md §3.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import Sigma from "sigma"
import type Graph from "graphology"
import { fetchNeighborhood } from "@/lib/api/graph"
import { adaptNeighborhood } from "@/lib/graph/graphology-adapter"
import { applyLayout } from "@/lib/graph/apply-layout"
import {
  ATLAS_DEFAULT_EDGE_TYPE,
  ATLAS_DEFAULT_NODE_TYPE,
  ATLAS_EDGE_PROGRAM_CLASSES,
  ATLAS_NODE_PROGRAM_CLASSES,
} from "@/lib/graph/programs"
import { composeLenses, LENS_REGISTRY, type LensId } from "@/lib/graph/lenses"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"
import { useAtlasKeyboard } from "./use-atlas-keyboard"
import { AtlasA11yTree } from "./atlas-a11y-tree"
import { AtlasLensPanel } from "./atlas-lens-panel"
import { AtlasContextMenu, type AtlasContextMenuTarget } from "./atlas-context-menu"
import { AtlasSavedViews } from "./atlas-saved-views"
import type { AtlasView } from "@/lib/api/atlas-views"

type AtlasSigma = Sigma<AtlasNodeAttributes, AtlasEdgeAttributes>
type AtlasGraph = Graph<AtlasNodeAttributes, AtlasEdgeAttributes>

// Node + edge program classes live in lib/graph/programs/ so both
// the production Atlas component and the dev perf harness share them.


export interface AtlasProps {
  /** Focal entity to render (canonical_id) */
  entity: string
  /** Hop depth — 1, 2, or 3 (default 2 per design-system-v2 §3.1) */
  hops?: 1 | 2 | 3
  /** Optional entity-type filter to narrow the rendered graph */
  filter?: string
  /** Click handler — fires when user clicks a node */
  onNodeClick?: (entityId: string) => void
  /** Double-click handler — Atlas dispatches to "open Wiki for this entity" */
  onNodeDoubleClick?: (entityId: string) => void
  /** Triggered by L key — surface the lens menu (wired in Day 7) */
  onToggleLensMenu?: () => void
  /** Triggered by ⌘K / Ctrl-K — open the global search palette */
  onSearchPalette?: () => void
  /** Right-click → "Cite in chat" — caller composes chat seed text */
  onCiteInChat?: (entityId: string, entityName: string) => void
  /** Right-click → "Open in Wiki" — caller routes to Subjects/Wiki */
  onOpenInWiki?: (entityId: string) => void
  /** Restore a saved view — caller updates focal entity + active state */
  onRestoreView?: (view: AtlasView) => void
}

interface LayoutStatus {
  state: "idle" | "fetching" | "laying-out" | "ready" | "error"
  message?: string
  progressPercent?: number
}

export function Atlas({
  entity,
  hops = 2,
  filter,
  onNodeClick,
  onNodeDoubleClick,
  onToggleLensMenu,
  onSearchPalette,
  onCiteInChat,
  onOpenInWiki,
  onRestoreView,
}: AtlasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<AtlasSigma | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [status, setStatus] = useState<LayoutStatus>({ state: "idle" })
  const [sigmaInstance, setSigmaInstance] = useState<AtlasSigma | null>(null)
  const [graphInstance, setGraphInstance] = useState<AtlasGraph | null>(null)
  const [activeLenses, setActiveLenses] = useState<Set<LensId>>(new Set())
  const [lensPanelVisible, setLensPanelVisible] = useState(true)
  const [contextMenuTarget, setContextMenuTarget] = useState<AtlasContextMenuTarget | null>(null)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["graph-neighborhood", entity, hops, filter ?? null],
    queryFn: ({ signal }) => fetchNeighborhood(entity, hops, filter, { signal }),
    staleTime: 30_000,
    enabled: Boolean(entity),
  })

  // Derive surface state directly from query state to avoid set-state-in-effect.
  const fetchingStatus: LayoutStatus | null = useMemo(() => {
    if (isLoading) return { state: "fetching" }
    if (isError) {
      return {
        state: "error",
        message: error instanceof Error ? error.message : "Graph fetch failed",
      }
    }
    return null
  }, [isLoading, isError, error])
  const renderedStatus: LayoutStatus =
    fetchingStatus && status.state !== "laying-out" && status.state !== "ready" && status.state !== "error"
      ? fetchingStatus
      : status

  // Render lifecycle — mount Sigma + apply layout when data arrives
  useEffect(() => {
    const container = containerRef.current
    if (!container || !data) return

    let cancelled = false
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    const graph = adaptNeighborhood(data)
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
      renderLabels: true,
      labelSize: 11,
      labelWeight: "500",
      defaultNodeColor: "#5C6680",
      defaultEdgeColor: "#3D4760",
      labelColor: { color: "#A8B5C8" },
      nodeProgramClasses: ATLAS_NODE_PROGRAM_CLASSES,
      edgeProgramClasses: ATLAS_EDGE_PROGRAM_CLASSES,
      defaultNodeType: ATLAS_DEFAULT_NODE_TYPE,
      defaultEdgeType: ATLAS_DEFAULT_EDGE_TYPE,
    }) as unknown as AtlasSigma
    sigmaRef.current = sigma
    setSigmaInstance(sigma)

    if (onNodeClick) {
      sigma.on("clickNode", ({ node }) => onNodeClick(node))
    }
    if (onNodeDoubleClick) {
      sigma.on("doubleClickNode", ({ node }) => onNodeDoubleClick(node))
    }
    sigma.on("enterNode", ({ node }) => {
      try {
        graph.setNodeAttribute(node, "highlighted", true)
      } catch {
        // node may have been removed mid-event
      }
    })
    sigma.on("leaveNode", ({ node }) => {
      try {
        graph.setNodeAttribute(node, "highlighted", false)
      } catch {
        // node may have been removed mid-event
      }
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

    setStatus({ state: "laying-out", progressPercent: 0 })
    applyLayout(graph, {
      iterations: graph.order > 500 ? 150 : 250,
      signal: abortRef.current.signal,
      onProgress: (iter, total) => {
        if (!cancelled) {
          setStatus({
            state: "laying-out",
            progressPercent: Math.round((iter / total) * 100),
          })
        }
      },
    })
      .then(() => {
        if (cancelled) return
        sigmaRef.current?.refresh()
        setStatus({ state: "ready" })
      })
      .catch((err) => {
        if (cancelled) return
        if ((err as Error).name === "AbortError") return
        setStatus({
          state: "error",
          message: err instanceof Error ? err.message : "Layout failed",
        })
      })

    return () => {
      cancelled = true
      abortRef.current?.abort()
      sigmaRef.current?.kill()
      sigmaRef.current = null
    }
  }, [data, onNodeClick, onNodeDoubleClick])

  const handleLensToggle = useCallback((id: LensId) => {
    setActiveLenses((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleToggleLensMenu = useCallback(() => {
    setLensPanelVisible((v) => !v)
    onToggleLensMenu?.()
  }, [onToggleLensMenu])

  // Re-bind sigma reducers when active lens set changes
  useEffect(() => {
    if (!sigmaInstance || !graphInstance) return
    const lenses = Array.from(activeLenses)
      .map((id) => LENS_REGISTRY[id])
      .filter(Boolean)
    if (lenses.length === 0) {
      sigmaInstance.setSetting("nodeReducer", null)
      sigmaInstance.setSetting("edgeReducer", null)
    } else {
      const { nodeReducer, edgeReducer } = composeLenses(lenses, graphInstance)
      sigmaInstance.setSetting("nodeReducer", nodeReducer)
      sigmaInstance.setSetting("edgeReducer", edgeReducer)
    }
    sigmaInstance.refresh()
  }, [sigmaInstance, graphInstance, activeLenses])

  const { selectedNodeId, setSelectedNodeId, onKeyDown } = useAtlasKeyboard({
    sigma: sigmaInstance,
    graph: graphInstance,
    focalEntity: entity,
    onActivate: (id) => onNodeDoubleClick?.(id) ?? onNodeClick?.(id),
    onToggleLensMenu: handleToggleLensMenu,
    onSearchPalette,
  })

  return (
    /* role="application" is the canonical ARIA pattern for a custom
     * keyboard-interactive widget (W3C WAI-ARIA APG). jsx-a11y rules
     * are overly conservative here. */
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
      <div
        ref={containerRef}
        className="h-full w-full"
        aria-hidden="true"
      />
      <AtlasA11yTree
        graph={graphInstance}
        selectedNodeId={selectedNodeId}
        onSelect={setSelectedNodeId}
        focalEntity={entity}
      />
      <AtlasLensPanel
        active={activeLenses}
        onToggle={handleLensToggle}
        visible={lensPanelVisible}
      />
      <AtlasContextMenu
        target={contextMenuTarget}
        onClose={() => setContextMenuTarget(null)}
        onCite={(id, name) => onCiteInChat?.(id, name)}
        onOpenWiki={(id) => onOpenInWiki?.(id)}
      />
      <AtlasSavedViews
        focalEntity={entity}
        hops={hops}
        filter={filter}
        activeLenses={activeLenses}
        getCameraState={() => {
          const sigma = sigmaInstance
          if (!sigma) return null
          const cam = sigma.getCamera().getState()
          return { x: cam.x, y: cam.y, ratio: cam.ratio, angle: cam.angle }
        }}
        onRestore={(view) => {
          // Restore lens state immediately; focal entity restore is owned
          // by the parent (SubjectsPane) since it controls navigation.
          setActiveLenses(new Set(view.lenses as LensId[]))
          onRestoreView?.(view)
        }}
      />
      {renderedStatus.state === "fetching" && (
        <div className="absolute inset-x-0 top-0 px-3 py-2 text-label-xs text-muted-foreground">
          Loading graph…
        </div>
      )}
      {renderedStatus.state === "laying-out" && (
        <div className="absolute inset-x-0 top-0 px-3 py-2 text-label-xs text-muted-foreground">
          Computing layout… {renderedStatus.progressPercent ?? 0}%
        </div>
      )}
      {renderedStatus.state === "error" && (
        <div className="absolute inset-0 flex items-center justify-center p-6">
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {renderedStatus.message ?? "Atlas failed to load"}
          </div>
        </div>
      )}
    </div>
  )
}
