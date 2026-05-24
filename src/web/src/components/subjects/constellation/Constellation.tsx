// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Constellation — 3D cinematic graph view (Phase B). React Three Fiber
// + drei. Phase B Day 1 ships the shell; Days 4-5 layer InstancedMesh
// + custom shaders.
//
// Architecture decisions baked in here:
//   - WHOLE COMPONENT IS LAZY (loaded only when Subjects mode === "constellation")
//     so the three.js bundle (~250KB gzipped) doesn't enter the
//     initial page load.
//   - SHADER PRELOAD: per the AMD-Mac Metal-via-ANGLE validation, first
//     frame can stall on shader compile. Day 5 will add an idle-time
//     warmup pass before the user enters this mode.
//   - InstancedMesh for nodes (Day 5) — sigma's per-node draw call cost
//     is what bounds Atlas at 5K; R3F+InstancedMesh validated for 2K@60fps.

import { Suspense, useState } from "react"
import { Canvas } from "@react-three/fiber"
import { OrbitControls, Stars } from "@react-three/drei"
import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { fetchEmbeddings3D } from "@/lib/api/embeddings-3d"
import { InstancedNodes } from "./instanced-nodes"
import { AmbientParticles } from "./ambient-particles"
import { TourCameraAnimator, TourControlPanel, useTourState } from "./tour-controller"

export interface ConstellationProps {
  /** Initial focal entity (optional — UMAP shows global view by default) */
  focalEntity?: string
  /** Optional entity-type filter */
  filter?: string | null
  /** Click handler — fires when user clicks a node */
  onNodeClick?: (entityId: string) => void
}

// ---------------------------------------------------------------------------
// Main component — InstancedMesh-backed (Phase B Day 5). All entities
// share one geometry + material; positions and colors are uploaded to
// the GPU once and stay there until the entity list changes.
// ---------------------------------------------------------------------------

export default function Constellation({ focalEntity, filter, onNodeClick }: ConstellationProps) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["constellation-embeddings-3d", focalEntity ?? null, filter ?? null],
    queryFn: ({ signal }) => fetchEmbeddings3D({ filter, signal }),
    staleTime: 60 * 60 * 1000,  // 1h — UMAP doesn't change between ingestion bursts
  })

  const [hovered, setHovered] = useState<string | null>(null)
  const tour = useTourState()

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading 3D projection…
      </div>
    )
  }
  if (isError) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error instanceof Error ? error.message : "Failed to load 3D embedding."}
        </div>
      </div>
    )
  }
  if (!data || data.entities.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-12">
        <div className="max-w-md rounded-xl border border-dashed border-border bg-card/40 p-8 text-center">
          <h2 className="text-lg font-semibold text-foreground">No 3D projection yet</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            UMAP projection runs nightly. Ingest more content to populate
            the constellation, then check back tomorrow.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className="cerid-stagger-fast relative h-full w-full bg-[#0A1F3D]"
      style={{ ["--i" as string]: 0 }}
      role="application"
      aria-roledescription="3D knowledge graph"
      aria-label={`Constellation view of ${data.count} entities`}
    >
      <Canvas
        camera={{ position: [0, 0, 8], fov: 60, near: 0.1, far: 1000 }}
        gl={{ antialias: true, alpha: false }}
        // dpr=[1, 2] caps render scale at 2× to keep AMD Mac in budget
        dpr={[1, 2]}
      >
        <color attach="background" args={["#0A1F3D"]} />
        <fog attach="fog" args={["#0A1F3D", 8, 30]} />

        {/* Ambient + key lights for material visibility */}
        <ambientLight intensity={0.35} />
        <directionalLight position={[5, 5, 5]} intensity={0.6} color="#5AECCB" />
        <directionalLight position={[-5, -5, -5]} intensity={0.3} color="#D4AF37" />

        {/* Starfield backdrop — drei's Stars is GPU-friendly */}
        <Stars
          radius={50}
          depth={50}
          count={2000}
          factor={3}
          saturation={0.2}
          fade
          speed={0.5}
        />

        <Suspense fallback={null}>
          <AmbientParticles count={Math.min(800, data.entities.length * 4)} radius={18} />
          <InstancedNodes
            entities={data.entities}
            onSelect={(id) => {
              setHovered(id)
              onNodeClick?.(id)
            }}
          />
          {/* TourCameraAnimator must be inside <Canvas> — it uses useFrame/useThree */}
          <TourCameraAnimator
            state={tour.state}
            onStopAdvance={tour.advance}
            onComplete={tour.complete}
          />
        </Suspense>

        <OrbitControls
          enablePan
          enableZoom
          enableRotate
          zoomSpeed={0.6}
          rotateSpeed={0.4}
          minDistance={2}
          maxDistance={60}
        />
      </Canvas>

      {/* Cached/projection-method overlay */}
      <div className="pointer-events-none absolute right-3 top-3 rounded-md bg-card/80 px-3 py-1.5 text-label-xs text-muted-foreground backdrop-blur">
        {data.count} entities · {data.entities[0]?.projection ?? "umap"}
        {data.cached && " · cached"}
        {hovered && <span className="ml-2 text-foreground">→ {hovered}</span>}
      </div>

      {/* Tour controls + subtitle overlay */}
      <TourControlPanel
        focalEntity={focalEntity}
        state={tour.state}
        onStart={tour.startTour}
        onPause={tour.pause}
        onResume={tour.resume}
        onStop={tour.stop}
      />
    </div>
  )
}
