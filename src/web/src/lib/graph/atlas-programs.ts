// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Sigma program registry for Atlas — Meridian v2.
// Separated from identity.ts so unit tests (no WebGL) can import the
// pure color pipeline without triggering WebGL2RenderingContext errors.
//
// Node program:  @sigma/node-border compound (trust ring + community fill)
// Edge program:  @sigma/edge-curve (parallel fanning + curved edges)

import { createNodeBorderProgram } from "@sigma/node-border"
import EdgeCurveProgram from "@sigma/edge-curve"
import { NodeCircleProgram, createNodeCompoundProgram } from "sigma/rendering"
import type { NodeProgramType } from "sigma/rendering"

// ---------------------------------------------------------------------------
// Node: border shell (trust ring, 3px) + filled disc (community color)
// ---------------------------------------------------------------------------

const BorderProgram = createNodeBorderProgram({
  borders: [
    {
      size: { value: 3, mode: "pixels" },
      color: { attribute: "borderColor", defaultValue: "#888888" }, // drift-allowed: neutral fallback when borderColor attr missing
    },
    {
      size: { fill: true },
      color: { attribute: "color", defaultValue: "#5C6680" }, // drift-allowed: graphite fallback; never seen in production graph
    },
  ],
})

export const AtlasNodeBorderProgram: NodeProgramType = createNodeCompoundProgram([
  BorderProgram as unknown as NodeProgramType,
  NodeCircleProgram as unknown as NodeProgramType,
])

export const ATLAS_V2_NODE_PROGRAM_CLASSES: Record<string, NodeProgramType> = {
  bordered: AtlasNodeBorderProgram,
}
export const ATLAS_V2_DEFAULT_NODE_TYPE = "bordered"

// ---------------------------------------------------------------------------
// Edge: curved for parallel fanning; all domain types use the same renderer
// ---------------------------------------------------------------------------

export { EdgeCurveProgram }

export const ATLAS_V2_EDGE_PROGRAM_CLASSES = {
  curved:         EdgeCurveProgram,
  mentions:       EdgeCurveProgram,
  works_on:       EdgeCurveProgram,
  discussed_with: EdgeCurveProgram,
  contradicts:    EdgeCurveProgram,
  temporal:       EdgeCurveProgram,
} as const

export const ATLAS_V2_DEFAULT_EDGE_TYPE = "curved"
