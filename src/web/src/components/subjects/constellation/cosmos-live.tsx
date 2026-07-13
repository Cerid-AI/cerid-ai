// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// cosmos.gl "Live" mode (B8) — the self-organizing wow scene. Feeds the corpus
// into @cosmos.gl/graph, a GPU force-directed layout that runs live: nodes
// repel, links pull, and the graph settles into shape in front of you. Its own
// canvas + WebGL context, created inside a div we hand it — never shared with
// sigma (2D map) or R3F (3D scene). Lazy-loaded (vendor-cosmos chunk): only
// users who switch to Live pay for luma.gl.
//
// Camera framing contract (beta triage P1):
// - Never fit/render against a zero-sized GL viewport (flex/drawer layouts
//   measure 0 first); a ResizeObserver re-frames on the first real size.
// - Auto-fit happens at discrete moments only (first sized frame, data
//   seed, simulation settle, big bang) — never per-tick, so the user can
//   hold their own zoom/pan mid-run.
// - Once the user moves the camera (userDriven zoom/pan), auto-fitting
//   stops until the next big bang explicitly re-frames.
//
// jsdom can't run WebGL, so this component is verified by build + real Chrome,
// not vitest; the data marshalling it relies on is unit-tested in cosmos-data.ts.

import { useCallback, useEffect, useRef } from "react"
import { Graph } from "@cosmos.gl/graph"
import type { EntityEmbedding3D } from "@/lib/api/embeddings-3d"
import { positionsFromEntities, randomPositions, linksToPairs, colorsFromRgb } from "./cosmos-data"

const SPACE_SIZE = 4096
const POINT_ALPHA = 0.9

export interface CosmosLiveProps {
  entities: EntityEmbedding3D[]
  /** [sourceIdx, targetIdx, weight, kind] tuples indexing into entities. */
  links: [number, number, number, string][]
  /** Lens colors (n×3 RGB) — the same buffer the 3D scene uses. */
  colors?: Float32Array
  /** Running the GPU simulation vs frozen. */
  playing: boolean
  /** Repulsion strength (simulationRepulsion). */
  repulsion: number
  /** Bump to scatter all points and re-run the "big bang". */
  bigBangNonce: number
  /** Reduced motion: seed from server positions and start frozen. */
  reducedMotion: boolean
  /** Scene background (theme-routed). */
  background: string
  /** Fires with the entity id when a point is clicked. */
  onNodeClick: (entityId: string) => void
}

export function CosmosLive({
  entities,
  links,
  colors,
  playing,
  repulsion,
  bigBangNonce,
  reducedMotion,
  background,
  onNodeClick,
}: CosmosLiveProps) {
  const divRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const destroyedRef = useRef(false)
  const seededCountRef = useRef(-1)
  const firstBangRef = useRef(true)
  // Container has had a non-zero measurement (fits against a 0×0 GL viewport
  // compute garbage and stick — the frozen path never re-fits on its own).
  const sizedRef = useRef(false)
  // User grabbed the camera (userDriven zoom/pan) — stop auto-fitting.
  const userMovedCameraRef = useRef(false)
  // Data arrived while the container measured 0×0; replay the seed frame on
  // the first real measurement.
  const pendingSeedFrameRef = useRef(false)

  // Live refs so the create-once onClick callback and effects read current
  // props without recreating the graph.
  const entitiesRef = useRef(entities)
  const onClickRef = useRef(onNodeClick)
  const playingRef = useRef(playing)
  const reducedMotionRef = useRef(reducedMotion)
  entitiesRef.current = entities
  onClickRef.current = onNodeClick
  playingRef.current = playing
  reducedMotionRef.current = reducedMotion

  const hasSize = useCallback(() => {
    const div = divRef.current
    return !!div && div.clientWidth > 0 && div.clientHeight > 0
  }, [])

  // Apply pending data + first frame. Ordering matters (cosmos.gl quirk):
  // render() applies pending data and draws, but the draw loop only keeps
  // running while the sim alpha > 0 — a fresh graph's alpha is 0, so a lone
  // render() draws one frame then freezes. So: render() to apply data,
  // fitView(0) to frame it instantly, then start() (sets alpha > 0) BEFORE
  // the final render() so the loop stays alive. Paused/reduced motion draws
  // a single static frame.
  const drawSeedFrame = useCallback((g: Graph) => {
    g.render()
    if (!userMovedCameraRef.current) g.fitView(0)
    if (!reducedMotionRef.current && playingRef.current) {
      g.start()
      g.render()
    } else {
      g.render(0)
    }
  }, [])

  // Create the cosmos graph once — it owns its own canvas + WebGL context.
  useEffect(() => {
    const div = divRef.current
    if (!div) return
    destroyedRef.current = false
    sizedRef.current = false
    userMovedCameraRef.current = false
    pendingSeedFrameRef.current = false
    const graph = new Graph(div, {
      backgroundColor: background,
      spaceSize: SPACE_SIZE,
      simulationRepulsion: repulsion,
      simulationGravity: 0.25,
      simulationLinkSpring: 1.0,
      simulationLinkDistance: 10,
      simulationFriction: 0.85,
      simulationDecay: 1000,
      renderLinks: true,
      enableDrag: true,
      // We manage framing ourselves (see camera contract in the header):
      // one fit per discrete moment, none per tick, none while unsized,
      // none after the user takes the camera.
      fitViewOnInit: false,
      pointDefaultSize: 6,
      scalePointsOnZoom: true,
      onZoomStart: (_e, userDriven) => {
        // Fires for pan AND wheel-zoom (cosmos camera is d3-zoom); fitView's
        // own transforms come through with userDriven=false.
        if (userDriven) userMovedCameraRef.current = true
      },
      onSimulationEnd: () => {
        if (!userMovedCameraRef.current && hasSize()) graphRef.current?.fitView(400)
      },
      onClick: (index) => {
        if (index === undefined) return
        const ent = entitiesRef.current[index]
        if (ent) onClickRef.current(ent.id)
      },
    })
    graphRef.current = graph

    // Re-frame when the container gets its first real size (flex/drawer
    // layouts measure 0 at mount — a fit taken then is permanently wrong for
    // the frozen path) and on later resizes while the camera is still ours.
    const observer = new ResizeObserver(() => {
      if (destroyedRef.current || !hasSize()) return
      const g = graphRef.current
      if (!g) return
      const first = !sizedRef.current
      sizedRef.current = true
      g.ready.then(() => {
        if (destroyedRef.current || !hasSize()) return
        if (pendingSeedFrameRef.current) {
          pendingSeedFrameRef.current = false
          drawSeedFrame(g)
          return
        }
        if (seededCountRef.current <= 0) return
        if (first || !userMovedCameraRef.current) g.fitView(0)
        // Keep a frame on screen: the loop is dead when paused/settled.
        if (playingRef.current) g.render()
        else g.render(0)
      })
    })
    observer.observe(div)

    return () => {
      destroyedRef.current = true
      observer.disconnect()
      graph.destroy()
      graphRef.current = null
      seededCountRef.current = -1
    }
    // Created once; background/repulsion/data flow in via their own effects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Seed / refresh data. Positions + links only re-seed when the point count
  // changes (first load or corpus growth) so a routine refetch never jolts an
  // in-flight simulation; colors always refresh (lens/theme swaps).
  useEffect(() => {
    const g = graphRef.current
    if (!g) return
    let cancelled = false
    g.ready.then(() => {
      if (cancelled || destroyedRef.current) return
      const n = entities.length
      const countChanged = seededCountRef.current !== n
      if (countChanged) {
        g.setPointPositions(positionsFromEntities(entities))
        g.setLinks(linksToPairs(links, n))
        seededCountRef.current = n
      }
      g.setPointColors(colorsFromRgb(colors, n, POINT_ALPHA))
      if (!hasSize()) {
        // Zero-sized GL viewport: defer the frame + fit to the
        // ResizeObserver's first non-zero measurement.
        pendingSeedFrameRef.current = true
        return
      }
      if (countChanged) {
        drawSeedFrame(g)
      } else {
        // Color-only refresh: repaint without re-framing the camera.
        if (!reducedMotion && playingRef.current) {
          g.start()
          g.render()
        } else {
          g.render(0)
        }
      }
    })
    return () => {
      cancelled = true
    }
  }, [entities, links, colors, reducedMotion, hasSize, drawSeedFrame])

  // Play / pause the simulation.
  useEffect(() => {
    const g = graphRef.current
    if (!g) return
    g.ready.then(() => {
      if (destroyedRef.current || !hasSize()) return
      if (playing) {
        // start() sets alpha > 0, then render() draws + keeps the loop alive.
        g.start()
        g.render()
      } else {
        // Pause the sim, then draw one static frame so the last state stays
        // visible (the loop would otherwise freeze once alpha decays).
        g.pause()
        g.render(0)
      }
    })
  }, [playing, hasSize])

  // Repulsion slider.
  useEffect(() => {
    const g = graphRef.current
    if (!g) return
    g.ready.then(() => {
      if (destroyedRef.current) return
      g.setConfig({ simulationRepulsion: repulsion })
      if (playingRef.current) g.start()
    })
  }, [repulsion])

  // Theme background.
  useEffect(() => {
    const g = graphRef.current
    if (!g) return
    g.ready.then(() => {
      if (!destroyedRef.current) g.setConfig({ backgroundColor: background })
    })
  }, [background])

  // Re-run the big bang: scatter every point, then let the GPU re-settle.
  // An explicit re-frame request — it reclaims the camera from the user.
  useEffect(() => {
    if (firstBangRef.current) {
      firstBangRef.current = false
      return
    }
    const g = graphRef.current
    if (!g) return
    g.ready.then(() => {
      if (destroyedRef.current || !hasSize()) return
      g.setPointPositions(randomPositions(entitiesRef.current.length, SPACE_SIZE))
      userMovedCameraRef.current = false
      g.render()
      g.fitView(0)
      g.start(1)
      g.render()
    })
  }, [bigBangNonce, hasSize])

  return <div ref={divRef} className="h-full w-full" aria-hidden="true" />
}

export default CosmosLive
