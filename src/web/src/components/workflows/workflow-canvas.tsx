// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useRef, useState } from "react"
import type { WorkflowNode, WorkflowEdge, WorkflowRunStatus } from "@/lib/types"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CANVAS_MIN_WIDTH = 400
const NARROW_VIEWPORT_THRESHOLD = 640
const NARROW_SCALE = 0.85
const NODE_W = 160
const NODE_H = 56
const NODE_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  agent:     { bg: "fill-teal-500/15",    border: "stroke-teal-500",    text: "fill-teal-700 dark:fill-teal-300" },
  parser:    { bg: "fill-blue-500/15",    border: "stroke-blue-500",    text: "fill-blue-700 dark:fill-blue-300" },
  tool:      { bg: "fill-purple-500/15",  border: "stroke-purple-500",  text: "fill-purple-700 dark:fill-purple-300" },
  condition: { bg: "fill-amber-500/15",   border: "stroke-amber-500",   text: "fill-amber-700 dark:fill-amber-300" },
}

const STATUS_COLORS: Record<string, string> = {
  pending:   "fill-zinc-400",
  running:   "fill-teal-400 animate-pulse",
  completed: "fill-emerald-500",
  failed:    "fill-red-500",
}

const TYPE_ICONS: Record<string, string> = {
  agent: "A",
  parser: "P",
  tool: "T",
  condition: "?",
}

// ---------------------------------------------------------------------------
// Edge path calculation
// ---------------------------------------------------------------------------

function edgePath(
  source: WorkflowNode,
  target: WorkflowNode,
): string {
  const sx = source.position.x + NODE_W
  const sy = source.position.y + NODE_H / 2
  const tx = target.position.x
  const ty = target.position.y + NODE_H / 2
  const mx = (sx + tx) / 2
  return `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${ty}, ${tx} ${ty}`
}

// ---------------------------------------------------------------------------
// Component Props
// ---------------------------------------------------------------------------

interface WorkflowCanvasProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  selectedNodeId: string | null
  nodeStatuses?: Record<string, WorkflowRunStatus>
  onNodeClick?: (nodeId: string) => void
  onNodeMove?: (nodeId: string, x: number, y: number) => void
  className?: string
}

// ---------------------------------------------------------------------------
// WorkflowCanvas
// ---------------------------------------------------------------------------

export default function WorkflowCanvas({
  nodes,
  edges,
  selectedNodeId,
  nodeStatuses = {},
  onNodeClick,
  onNodeMove,
  className,
}: WorkflowCanvasProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState<{ id: string; offsetX: number; offsetY: number } | null>(null)
  const [containerWidth, setContainerWidth] = useState(800)

  // Track container width for responsive scaling
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width)
      }
    })
    observer.observe(el)
    setContainerWidth(el.clientWidth)
    return () => observer.disconnect()
  }, [])

  const isNarrow = containerWidth < NARROW_VIEWPORT_THRESHOLD

  // Build node map for edge lookups
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))

  // Calculate canvas bounds
  const maxX = Math.max(400, ...nodes.map((n) => n.position.x + NODE_W + 40))
  const maxY = Math.max(300, ...nodes.map((n) => n.position.y + NODE_H + 40))

  // ── Drag handlers ──────────────────────────────────────────────────────

  const handleMouseDown = useCallback(
    (e: React.MouseEvent, nodeId: string) => {
      e.stopPropagation()
      const node = nodeMap.get(nodeId)
      if (!node || !svgRef.current) return
      const pt = svgRef.current.createSVGPoint()
      pt.x = e.clientX
      pt.y = e.clientY
      const svgP = pt.matrixTransform(svgRef.current.getScreenCTM()?.inverse())
      setDragging({ id: nodeId, offsetX: svgP.x - node.position.x, offsetY: svgP.y - node.position.y })
    },
    [nodeMap],
  )

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging || !svgRef.current || !onNodeMove) return
      const pt = svgRef.current.createSVGPoint()
      pt.x = e.clientX
      pt.y = e.clientY
      const svgP = pt.matrixTransform(svgRef.current.getScreenCTM()?.inverse())
      onNodeMove(dragging.id, Math.max(0, svgP.x - dragging.offsetX), Math.max(0, svgP.y - dragging.offsetY))
    },
    [dragging, onNodeMove],
  )

  const handleMouseUp = useCallback(() => {
    setDragging(null)
  }, [])

  // ── Touch handlers for mobile drag ─────────────────────────────────────

  const handleTouchStart = useCallback(
    (e: React.TouchEvent, nodeId: string) => {
      if (e.touches.length !== 1) return
      e.stopPropagation()
      const touch = e.touches[0]
      const node = nodeMap.get(nodeId)
      if (!node || !svgRef.current) return
      const pt = svgRef.current.createSVGPoint()
      pt.x = touch.clientX
      pt.y = touch.clientY
      const svgP = pt.matrixTransform(svgRef.current.getScreenCTM()?.inverse())
      setDragging({ id: nodeId, offsetX: svgP.x - node.position.x, offsetY: svgP.y - node.position.y })
    },
    [nodeMap],
  )

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!dragging || !svgRef.current || !onNodeMove || e.touches.length !== 1) return
      e.preventDefault()
      const touch = e.touches[0]
      const pt = svgRef.current.createSVGPoint()
      pt.x = touch.clientX
      pt.y = touch.clientY
      const svgP = pt.matrixTransform(svgRef.current.getScreenCTM()?.inverse())
      onNodeMove(dragging.id, Math.max(0, svgP.x - dragging.offsetX), Math.max(0, svgP.y - dragging.offsetY))
    },
    [dragging, onNodeMove],
  )

  const handleTouchEnd = useCallback(() => {
    setDragging(null)
  }, [])

  return (
    <div ref={containerRef} className={cn("w-full h-full", className)} style={{ minWidth: CANVAS_MIN_WIDTH }}>
    <svg
      ref={svgRef}
      viewBox={`0 0 ${maxX} ${maxY}`}
      className="w-full h-full min-h-[400px] bg-zinc-950/50 rounded-lg border border-zinc-800"
      style={isNarrow ? { transform: `scale(${NARROW_SCALE})`, transformOrigin: "top left" } : undefined}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onTouchCancel={handleTouchEnd}
    >
      {/* Arrowhead marker */}
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" className="fill-zinc-500" />
        </marker>
      </defs>

      {/* Grid dots */}
      <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
        <circle cx="10" cy="10" r="0.5" className="fill-zinc-700/40" />
      </pattern>
      <rect width="100%" height="100%" fill="url(#grid)" />

      {/* Edges */}
      {edges.map((edge) => {
        const source = nodeMap.get(edge.source_id)
        const target = nodeMap.get(edge.target_id)
        if (!source || !target) return null
        return (
          <g key={`${edge.source_id}-${edge.target_id}`}>
            <path
              d={edgePath(source, target)}
              className="stroke-zinc-500 dark:stroke-zinc-600"
              strokeWidth={2}
              fill="none"
              markerEnd="url(#arrowhead)"
            />
            {edge.label && (
              <text
                x={(source.position.x + NODE_W + target.position.x) / 2}
                y={(source.position.y + target.position.y) / 2 + NODE_H / 2 - 8}
                textAnchor="middle"
                className="fill-zinc-400 text-[10px]"
              >
                {edge.label}
              </text>
            )}
          </g>
        )
      })}

      {/* Nodes */}
      {nodes.map((node) => {
        const colors = NODE_COLORS[node.type] ?? NODE_COLORS.agent
        const isSelected = node.id === selectedNodeId
        const status = nodeStatuses[node.id]

        return (
          <g
            key={node.id}
            transform={`translate(${node.position.x}, ${node.position.y})`}
            className="cursor-pointer"
            onMouseDown={(e) => handleMouseDown(e, node.id)}
            onTouchStart={(e) => handleTouchStart(e, node.id)}
            onClick={() => onNodeClick?.(node.id)}
          >
            {/* Node body */}
            <rect
              width={NODE_W}
              height={NODE_H}
              rx={12}
              ry={12}
              className={cn(colors.bg, colors.border, isSelected ? "stroke-[2.5]" : "stroke-[1.5]")}
              strokeDasharray={node.type === "condition" ? "4 2" : undefined}
            />

            {/* Type icon circle */}
            <circle
              cx={20}
              cy={NODE_H / 2}
              r={12}
              className={cn(colors.border, "fill-none stroke-[1.5]")}
            />
            <text
              x={20}
              y={NODE_H / 2 + 4}
              textAnchor="middle"
              className={cn("text-[11px] font-bold", colors.text)}
            >
              {TYPE_ICONS[node.type] ?? "?"}
            </text>

            {/* Node name */}
            <text
              x={40}
              y={NODE_H / 2 + 4}
              className="fill-zinc-200 text-[12px] font-medium"
            >
              {node.name.length > 14 ? `${node.name.slice(0, 12)}...` : node.name}
            </text>

            {/* Status indicator */}
            {status && (
              <circle
                cx={NODE_W - 12}
                cy={12}
                r={5}
                className={STATUS_COLORS[status] ?? STATUS_COLORS.pending}
              />
            )}

            {/* Selection highlight */}
            {isSelected && (
              <rect
                width={NODE_W + 4}
                height={NODE_H + 4}
                x={-2}
                y={-2}
                rx={14}
                ry={14}
                className="fill-none stroke-teal-400/50 stroke-[1]"
              />
            )}
          </g>
        )
      })}
    </svg>
    </div>
  )
}
