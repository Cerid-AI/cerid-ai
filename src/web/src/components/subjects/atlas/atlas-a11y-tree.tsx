// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Screen-reader accessibility layer for Atlas. WebGL canvas content is
// invisible to assistive tech, so this component renders an off-screen
// (sr-only) tree of the visible nodes as a focusable list. Selecting an
// item in the list mirrors keyboard navigation.
//
// Two surfaces:
//   1. <ul role="listbox"> of nodes — per-node aria-label with name,
//      type, mention count, trust state, community.
//   2. <div role="status" aria-live="polite"> announcing the currently
//      selected node.

import type Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

type AtlasGraph = Graph<AtlasNodeAttributes, AtlasEdgeAttributes>

export interface AtlasA11yTreeProps {
  graph: AtlasGraph | null
  selectedNodeId: string | null
  onSelect: (nodeId: string) => void
  /** Focal entity, surfaced as the "current focus" anchor */
  focalEntity: string
}

function nodeAriaLabel(attrs: AtlasNodeAttributes): string {
  const parts = [
    attrs.name,
    attrs.type,
    `${attrs.mention_count} mentions`,
    `trust ${attrs.trust_state}`,
  ]
  if (attrs.community) parts.push(`community ${attrs.community}`)
  if (attrs.focused) parts.push("focal entity")
  return parts.join(", ")
}

export function AtlasA11yTree({
  graph,
  selectedNodeId,
  onSelect,
  focalEntity,
}: AtlasA11yTreeProps) {
  if (!graph) return null

  const selectedAttrs =
    selectedNodeId && graph.hasNode(selectedNodeId)
      ? graph.getNodeAttributes(selectedNodeId)
      : null

  return (
    <>
      {/* Off-screen status region — announces selection changes to AT */}
      <div role="status" aria-live="polite" className="sr-only">
        {selectedAttrs
          ? `Selected ${nodeAriaLabel(selectedAttrs)}`
          : `Focal entity ${focalEntity}. Press Tab to navigate nodes.`}
      </div>

      {/* Off-screen node list — provides screen-reader navigation parity
          with the WebGL canvas. role="listbox" for parity with the
          Tab-cycle selection model in use-atlas-keyboard.ts */}
      <ul
        role="listbox"
        aria-label="Atlas nodes"
        className="sr-only"
        tabIndex={-1}
      >
        {graph.mapNodes((id, attrs) => (
          <li
            key={id}
            role="option"
            aria-selected={id === selectedNodeId}
            aria-label={nodeAriaLabel(attrs)}
            tabIndex={id === selectedNodeId ? 0 : -1}
            onClick={() => onSelect(id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onSelect(id)
              }
            }}
          >
            {attrs.name}
          </li>
        ))}
      </ul>
    </>
  )
}
