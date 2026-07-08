// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Persistent name labels for the constellation's hub entities — the
// top-N by connection degree (graph centrality, matching how the force
// layout organizes space and how node size is encoded). Like Obsidian's
// graph view, the biggest junctions are always named so the map is
// legible without hovering; everything else reveals its name via the
// hover tooltip.
//
// Perf: N is small (default 18) and each label is one troika SDF text
// draw. Billboarding keeps them camera-facing for free.

import { Billboard, Text } from "@react-three/drei"
import { useMemo, useRef, useState } from "react"
import { useFrame } from "@react-three/fiber"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"
import { degreeRadius } from "./palette"
import { visibleLabelCount, labelFillOpacity } from "./quality"
import { LABEL_HEX, LABEL_HEX_LIGHT, SURFACE_HEX } from "@/theme/shader-tokens"

export interface HubLabelsProps {
  entities: EntityEmbedding3D[]
  /** Per-entity connection degree — ranking + label offset. */
  degrees?: Float32Array
  /** How many top-degree entities get a persistent label. */
  count?: number
  /** Hovered entity index — its label brightens. */
  hoveredIndex?: number | null
  /** Pinned entity index — its label is always drawn (B2), even below the LOD count cull. */
  pinnedIndex?: number | null
  /** Resolved theme (B2): dark labels use the light-on-dark palette, light labels the dark-on-light one. */
  dark?: boolean
}

// Distance bucket boundaries — a re-render is only triggered when the camera
// crosses one of these thresholds, not on every frame.
const DISTANCE_BUCKETS = [28, 40, 55]

function distanceBucket(d: number): number {
  for (let i = 0; i < DISTANCE_BUCKETS.length; i++) {
    if (d < DISTANCE_BUCKETS[i]) return i
  }
  return DISTANCE_BUCKETS.length
}

export function HubLabels({
  entities,
  degrees,
  count = 18,
  hoveredIndex = null,
  pinnedIndex = null,
  dark = true,
}: HubLabelsProps) {
  // Camera distance sampled per-frame, only triggers a re-render when the
  // bucket (zoom level) changes — avoids per-frame React reconciliation.
  const [cameraDist, setCameraDist] = useState(30)
  const lastBucketRef = useRef(distanceBucket(30))
  useFrame(({ camera }) => {
    const d = camera.position.length()
    const bucket = distanceBucket(d)
    if (bucket !== lastBucketRef.current) {
      lastBucketRef.current = bucket
      setCameraDist(d)
    }
  })

  const visibleCount = visibleLabelCount(cameraDist, count)
  // Surviving labels soften as the camera pulls back — a gentle LOD fade
  // rather than a hard pop when a label crosses the count cull.
  const baseOpacity = labelFillOpacity(cameraDist)
  // Theme-routed palette: light-on-dark vs dark-on-light with a bright halo.
  const palette = dark
    ? { default: LABEL_HEX.default, hover: LABEL_HEX.hover, outline: SURFACE_HEX.vaultDeep }
    : { default: LABEL_HEX_LIGHT.default, hover: LABEL_HEX_LIGHT.hover, outline: LABEL_HEX_LIGHT.outline }

  const hubs = useMemo(() => {
    return entities
      .map((ent, index) => ({ ent, index, degree: degrees?.[index] ?? 0 }))
      .sort((a, b) => b.degree - a.degree)
      .slice(0, count)
      .filter((h) => h.degree > 0)
  }, [entities, degrees, count])

  // The pinned node's label is always drawn (B2) — force it in even when it
  // ranks below the LOD count cull, so the current selection is never nameless.
  const visible = useMemo(() => {
    const chosen = hubs.slice(0, visibleCount)
    if (pinnedIndex !== null && pinnedIndex >= 0 && !chosen.some((h) => h.index === pinnedIndex)) {
      const ent = entities[pinnedIndex]
      if (ent) chosen.push({ ent, index: pinnedIndex, degree: degrees?.[pinnedIndex] ?? 0 })
    }
    return chosen
  }, [hubs, visibleCount, pinnedIndex, entities, degrees])

  return (
    <group>
      {visible.map(({ ent, index, degree }) => {
        const focused = index === hoveredIndex || index === pinnedIndex
        return (
          <Billboard key={ent.id} position={[ent.x, ent.y + degreeRadius(degree) + 0.28, ent.z]}>
            <Text
              fontSize={0.34}
              color={focused ? palette.hover : palette.default}
              outlineWidth={0.012}
              outlineColor={palette.outline}
              anchorX="center"
              anchorY="bottom"
              maxWidth={6}
              fillOpacity={focused ? 1 : baseOpacity}
            >
              {ent.name}
            </Text>
          </Billboard>
        )
      })}
    </group>
  )
}
