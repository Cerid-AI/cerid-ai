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
import { useMemo } from "react"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"
import { degreeRadius } from "./palette"

export interface HubLabelsProps {
  entities: EntityEmbedding3D[]
  /** Per-entity connection degree — ranking + label offset. */
  degrees?: Float32Array
  /** How many top-degree entities get a persistent label. */
  count?: number
  /** Hovered entity index — its label brightens. */
  hoveredIndex?: number | null
}

export function HubLabels({ entities, degrees, count = 18, hoveredIndex = null }: HubLabelsProps) {
  const hubs = useMemo(() => {
    return entities
      .map((ent, index) => ({ ent, index, degree: degrees?.[index] ?? 0 }))
      .sort((a, b) => b.degree - a.degree)
      .slice(0, count)
      .filter((h) => h.degree > 0)
  }, [entities, degrees, count])

  return (
    <group>
      {hubs.map(({ ent, index, degree }) => (
        <Billboard key={ent.id} position={[ent.x, ent.y + degreeRadius(degree) + 0.28, ent.z]}>
          <Text
            fontSize={0.34}
            color={index === hoveredIndex ? "#8CF5DC" : "#C8D4E6"}
            outlineWidth={0.012}
            outlineColor="#0A1F3D"
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
