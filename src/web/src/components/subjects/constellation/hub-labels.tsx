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
import { visibleLabelCount } from "./quality"
import { LABEL_HEX, SURFACE_HEX } from "@/theme/shader-tokens"

export interface HubLabelsProps {
  entities: EntityEmbedding3D[]
  /** Per-entity connection degree — ranking + label offset. */
  degrees?: Float32Array
  /** How many top-degree entities get a persistent label. */
  count?: number
  /** Hovered entity index — its label brightens. */
  hoveredIndex?: number | null
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

export function HubLabels({ entities, degrees, count = 18, hoveredIndex = null }: HubLabelsProps) {
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

  const hubs = useMemo(() => {
    return entities
      .map((ent, index) => ({ ent, index, degree: degrees?.[index] ?? 0 }))
      .sort((a, b) => b.degree - a.degree)
      .slice(0, count)
      .filter((h) => h.degree > 0)
  }, [entities, degrees, count])

  return (
    <group>
      {hubs.slice(0, visibleCount).map(({ ent, index, degree }) => (
        <Billboard key={ent.id} position={[ent.x, ent.y + degreeRadius(degree) + 0.28, ent.z]}>
          <Text
            fontSize={0.34}
            color={index === hoveredIndex ? LABEL_HEX.hover : LABEL_HEX.default}
            outlineWidth={0.012}
            outlineColor={SURFACE_HEX.vaultDeep}
            anchorX="center"
            anchorY="bottom"
            maxWidth={6}
            fillOpacity={index === hoveredIndex ? 1 : 0.78}
          >
            {ent.name}
          </Text>
        </Billboard>
      ))}
    </group>
  )
}
