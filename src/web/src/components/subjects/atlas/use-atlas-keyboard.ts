// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Keyboard navigation hook for the Atlas mode. Wires the Atlas spec's
// keymap (design-system-v2 §3.5; impl-plan Phase A Day 6) onto a
// container ref. Owns the "selected node" state cycled by Tab/N, and
// the camera operations triggered by Arrow/+/-/H/R.
//
// Returns:
//   - selectedNodeId: currently focused node (drives focus indicator)
//   - handlers: { onKeyDown } to spread onto the Atlas wrapper div
//   - cycleSelection: imperative cycle for external triggers (e.g. ⌘K)
//
// Keymap:
//   Tab / Shift-Tab   — next / previous node (graphology iteration order)
//   N                 — alias for Tab
//   ↑ ↓ ← →           — pan camera (sigma camera.x / camera.y)
//   = / +             — zoom in
//   -                 — zoom out
//   Enter             — activate selected (calls onActivate)
//   H                 — home (recenter on focal entity)
//   R                 — reset zoom + position
//   L                 — toggle lens menu (Day 7 — surfaced via onToggleLensMenu)
//   ⌘K / Ctrl-K       — open search palette (onSearchPalette)

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type Graph from "graphology"
import type Sigma from "sigma"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

type AtlasGraph = Graph<AtlasNodeAttributes, AtlasEdgeAttributes>
type AtlasSigma = Sigma<AtlasNodeAttributes, AtlasEdgeAttributes>

export interface UseAtlasKeyboardOptions {
  /** Sigma instance — null while the graph hasn't mounted yet */
  sigma: AtlasSigma | null
  /** Graphology graph — kept in sync with sigma. */
  graph: AtlasGraph | null
  /** Focal entity (home position) */
  focalEntity: string
  /** Called when user presses Enter on a selected node */
  onActivate?: (nodeId: string) => void
  /** Called when user presses L */
  onToggleLensMenu?: () => void
  /** Called when user presses ⌘K / Ctrl-K */
  onSearchPalette?: () => void
}

const PAN_STEP = 0.08      // fraction of viewport per arrow press
const ZOOM_STEP = 1.2      // multiplicative
const ZOOM_DURATION = 150  // ms — sigma camera animation

export function useAtlasKeyboard({
  sigma,
  graph,
  focalEntity,
  onActivate,
  onToggleLensMenu,
  onSearchPalette,
}: UseAtlasKeyboardOptions) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const selectedRef = useRef<string | null>(null)
  useEffect(() => {
    selectedRef.current = selectedNodeId
  }, [selectedNodeId])

  // Visible-node list, recomputed lazily; iteration order is stable
  // across renders because graphology preserves insertion order.
  const nodeIds = useMemo(() => {
    if (!graph) return [] as string[]
    return graph.nodes()
  }, [graph])

  // Reflect selectedNodeId into the node's `focused` attribute so the
  // halo shader can amplify its intensity (×1.4 via pulseIntensity).
  // We also reset every other node to its original focused value.
  useEffect(() => {
    if (!graph || !sigma) return
    const focalSet = new Set<string>([focalEntity, ...(selectedNodeId ? [selectedNodeId] : [])])
    graph.forEachNode((id) => {
      const shouldFocus = focalSet.has(id)
      const current = graph.getNodeAttribute(id, "focused")
      if (current !== shouldFocus) {
        graph.setNodeAttribute(id, "focused", shouldFocus)
        // Recompute pulse intensity to match.
        const recency = graph.getNodeAttribute(id, "recency_score") ?? 0.5
        const base = Math.max(0.25, Math.min(1, recency))
        const next = shouldFocus ? Math.min(1, base * 1.4) : base
        graph.setNodeAttribute(id, "pulseIntensity", next)
      }
    })
    sigma.refresh()
  }, [graph, sigma, selectedNodeId, focalEntity])

  const cycleSelection = useCallback(
    (direction: 1 | -1) => {
      if (nodeIds.length === 0) return
      const currentIdx = selectedRef.current ? nodeIds.indexOf(selectedRef.current) : -1
      let nextIdx: number
      if (currentIdx === -1) {
        // From no selection, forward picks the first node and backward
        // picks the last — matches OS-level Tab/Shift-Tab UX.
        nextIdx = direction === 1 ? 0 : nodeIds.length - 1
      } else {
        nextIdx = (currentIdx + direction + nodeIds.length) % nodeIds.length
      }
      setSelectedNodeId(nodeIds[nextIdx])
    },
    [nodeIds],
  )

  const panCamera = useCallback(
    (dx: number, dy: number) => {
      if (!sigma) return
      const camera = sigma.getCamera()
      const ratio = camera.getState().ratio
      camera.animate(
        {
          x: camera.getState().x + dx * PAN_STEP * ratio,
          y: camera.getState().y + dy * PAN_STEP * ratio,
        },
        { duration: ZOOM_DURATION },
      )
    },
    [sigma],
  )

  const zoomCamera = useCallback(
    (factor: number) => {
      if (!sigma) return
      const camera = sigma.getCamera()
      camera.animatedZoom({ factor, duration: ZOOM_DURATION })
    },
    [sigma],
  )

  const recenter = useCallback(() => {
    if (!sigma || !graph) return
    if (!graph.hasNode(focalEntity)) return
    const camera = sigma.getCamera()
    const nodeDisplay = sigma.getNodeDisplayData(focalEntity)
    if (!nodeDisplay) return
    camera.animate(
      { x: nodeDisplay.x, y: nodeDisplay.y, ratio: 1 },
      { duration: 300 },
    )
  }, [sigma, graph, focalEntity])

  const reset = useCallback(() => {
    if (!sigma) return
    sigma.getCamera().animatedReset({ duration: 300 })
  }, [sigma])

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      // Skip if focus is inside an input/textarea (avoid swallowing typing)
      const target = event.target as HTMLElement
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return
      }

      // ⌘K / Ctrl-K — search palette
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        onSearchPalette?.()
        return
      }

      switch (event.key) {
        case "Tab":
          event.preventDefault()
          cycleSelection(event.shiftKey ? -1 : 1)
          break
        case "n":
        case "N":
          event.preventDefault()
          cycleSelection(1)
          break
        case "Enter":
          if (selectedRef.current) {
            event.preventDefault()
            onActivate?.(selectedRef.current)
          }
          break
        case "ArrowUp":
          event.preventDefault()
          panCamera(0, -1)
          break
        case "ArrowDown":
          event.preventDefault()
          panCamera(0, 1)
          break
        case "ArrowLeft":
          event.preventDefault()
          panCamera(-1, 0)
          break
        case "ArrowRight":
          event.preventDefault()
          panCamera(1, 0)
          break
        case "+":
        case "=":
          event.preventDefault()
          zoomCamera(1 / ZOOM_STEP)  // sigma "factor < 1" → closer
          break
        case "-":
        case "_":
          event.preventDefault()
          zoomCamera(ZOOM_STEP)
          break
        case "h":
        case "H":
          event.preventDefault()
          recenter()
          break
        case "r":
        case "R":
          event.preventDefault()
          reset()
          break
        case "l":
        case "L":
          event.preventDefault()
          onToggleLensMenu?.()
          break
      }
    },
    [cycleSelection, panCamera, zoomCamera, recenter, reset, onActivate, onToggleLensMenu, onSearchPalette],
  )

  return {
    selectedNodeId,
    setSelectedNodeId,
    cycleSelection,
    onKeyDown,
  }
}
