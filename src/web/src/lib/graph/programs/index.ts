// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Sigma program registry for Atlas. Centralizes the node + edge
// program-class wiring so both the production Atlas component and the
// dev perf harness share the exact same renderers.
//
// Meridian v2: node program = @sigma/node-border compound (trust ring +
// community fill). Edge program = @sigma/edge-curve (parallel fanning).

export {
  ATLAS_V2_NODE_PROGRAM_CLASSES as ATLAS_NODE_PROGRAM_CLASSES,
  ATLAS_V2_DEFAULT_NODE_TYPE as ATLAS_DEFAULT_NODE_TYPE,
  ATLAS_V2_EDGE_PROGRAM_CLASSES as ATLAS_EDGE_PROGRAM_CLASSES,
  ATLAS_V2_DEFAULT_EDGE_TYPE as ATLAS_DEFAULT_EDGE_TYPE,
  AtlasNodeBorderProgram,
  EdgeCurveProgram,
} from "@/lib/graph/atlas-programs"
