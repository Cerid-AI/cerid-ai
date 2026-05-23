// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Sigma program registry for Atlas. Centralizes the node + edge
// program-class wiring so both the production Atlas component and the
// dev-mode perf harness use the exact same renderers (so perf budgets
// reflect real production behavior, not a degenerate code path).
//
// Domain edge types ("mentions", "works_on", "discussed_with",
// "contradicts", "temporal") all route to sigma's bundled
// EdgeRectangleProgram for now — they differ in color (handled by the
// adapter via per-edge `color` attribute), not in geometry. Custom
// edge shaders (animated dashes for "contradicts", thicker arrows for
// "works_on") arrive in a future iteration if visual design calls for
// per-type differentiation.

import { createNodeCompoundProgram, EdgeRectangleProgram, NodeCircleProgram } from "sigma/rendering"
import type { NodeProgramType } from "sigma/rendering"
import NodeHaloProgram from "./node-halo.program"

// We thread `as unknown as` through the default-Attributes form here.
// Sigma's `nodeProgramClasses` is typed against the default Attributes
// generic; the AtlasNodeAttributes generic narrows on the Sigma instance
// side, not the program-class registry side.
type LooseNodeProgramType = NodeProgramType

// Halo (behind) + filled disc (on top) compound program. Order is
// back-to-front so the disc occludes the halo's interior.
export const HaloedNodeProgram: LooseNodeProgramType = createNodeCompoundProgram([
  NodeHaloProgram as unknown as LooseNodeProgramType,
  NodeCircleProgram as unknown as LooseNodeProgramType,
])

export const ATLAS_NODE_PROGRAM_CLASSES: { [type: string]: LooseNodeProgramType } = {
  haloed: HaloedNodeProgram,
}

// All domain edge types map to EdgeRectangleProgram (sigma's standard
// straight-line edge renderer). Per-edge color/thickness still flows
// from the adapter's edgeAttrs() output.
export const ATLAS_EDGE_PROGRAM_CLASSES = {
  mentions: EdgeRectangleProgram,
  works_on: EdgeRectangleProgram,
  discussed_with: EdgeRectangleProgram,
  contradicts: EdgeRectangleProgram,
  temporal: EdgeRectangleProgram,
} as const

export const ATLAS_DEFAULT_NODE_TYPE = "haloed"
export const ATLAS_DEFAULT_EDGE_TYPE = "mentions"
