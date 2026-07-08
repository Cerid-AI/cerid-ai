// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Atlas lens contract. A lens is a pair of pure transforms applied
// during sigma's nodeReducer / edgeReducer phase — i.e. on every
// frame, before WebGL upload. Lenses stack: each one receives the
// output of the previous lens. Order is determined by the active
// list, which is a LIFO chip stack in the UI.

import type Graph from "graphology"
import type { AtlasEdgeAttributes, AtlasNodeAttributes } from "@/lib/types/graph"

export type LensId =
  | "contradiction"
  | "open-question"
  | "provenance"
  | "quality"
  | "domain"
  | "bridges"

export interface Lens {
  id: LensId
  /** Display label for chip + legend */
  label: string
  /** Short tooltip / explainer (one sentence) */
  description: string
  /** Hex color for the legend swatch */
  legendColor: string
  /** Per-node transform — runs every frame. Return unchanged if no-op. */
  transformNode: (
    node: string,
    attrs: AtlasNodeAttributes,
    graph: Graph<AtlasNodeAttributes, AtlasEdgeAttributes>,
  ) => AtlasNodeAttributes
  /** Per-edge transform — runs every frame. Return unchanged if no-op. */
  transformEdge: (
    edge: string,
    attrs: AtlasEdgeAttributes,
    graph: Graph<AtlasNodeAttributes, AtlasEdgeAttributes>,
  ) => AtlasEdgeAttributes
}

/**
 * Compose multiple lenses into single node + edge reducers.
 * The reducers are stable references suitable for direct use as
 * sigma `nodeReducer` / `edgeReducer` settings.
 */
export function composeLenses(
  lenses: Lens[],
  graph: Graph<AtlasNodeAttributes, AtlasEdgeAttributes>,
) {
  return {
    nodeReducer: (node: string, attrs: AtlasNodeAttributes) => {
      let out = attrs
      for (const lens of lenses) {
        out = lens.transformNode(node, out, graph)
      }
      return out
    },
    edgeReducer: (edge: string, attrs: AtlasEdgeAttributes) => {
      let out = attrs
      for (const lens of lenses) {
        out = lens.transformEdge(edge, out, graph)
      }
      return out
    },
  }
}
