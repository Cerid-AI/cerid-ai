// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Inline mini-graph widget for the Wiki entity page. Reuses the Atlas
// renderer at smaller scale to show the entity's 1-hop neighborhood
// without leaving the Wiki view. Per viz-spec §5.
//
// Behavior:
//   - Renders only when expanded (opt-in toggle) so Wiki pages don't
//     mount a full sigma instance for every visit. Spinning up sigma +
//     fetching neighborhood + running layout is real work; users who
//     are reading text shouldn't pay for the graph they didn't ask for.
//   - Always 1-hop (mini context, not exploration); for deeper analysis
//     the "Open in Atlas" button hands off to the full Subjects/Atlas
//     mode via NavigationProvider.
//
// Color sourcing: all node/edge colors flow through communitySlot +
// resolveMapTokens + normalizeColor inside the Atlas renderer (Agent A).
// The mini-graph passes no override colors; it inherits the family
// color vocabulary from Atlas's nodeReducer/edgeReducer.

import { lazy, Suspense, useEffect, useState } from "react"
import { ChevronDown, ChevronRight, Compass, ExternalLink, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useNavigation } from "@/contexts/navigation-context"
import { fetchNeighborhood } from "@/lib/api/graph"
import type { GraphNode } from "@/lib/types/graph"

// Lazy-load Atlas so its WebGL deps (sigma) don't hit the wiki page
// bundle — most wiki visits won't expand the mini-graph, and the
// import would otherwise pull sigma into every wiki page load. Also
// keeps jsdom-based tests of the entity detail view clean (sigma
// references WebGL2RenderingContext at module load).
const Atlas = lazy(() =>
  import("@/components/subjects/atlas/Atlas").then((m) => ({ default: m.Atlas })),
)

export interface MiniGraphProps {
  /** Focal entity slug / canonical id */
  entitySlug: string
  /** Entity display name (for ARIA labels) */
  entityName: string
  /**
   * Render the graph panel expanded on mount instead of behind the
   * disclosure toggle. Used by the article infobox, where the graph is
   * a permanent card rather than an opt-in widget. The user can still
   * collapse it via the toggle.
   */
  defaultExpanded?: boolean
}

export function MiniGraph({ entitySlug, entityName, defaultExpanded = false }: MiniGraphProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [hops, setHops] = useState<1 | 2 | 3>(1)
  const [neighbors, setNeighbors] = useState<GraphNode[]>([])
  const navigation = useNavigation()

  const handleOpenAtlas = () => {
    navigation.goTo("subjects", { mode: "atlas", entity: entitySlug })
  }

  // Reset neighbors when entity or hops changes so the sr-only list stays fresh.
  useEffect(() => {
    setNeighbors([])
  }, [entitySlug, hops])

  // Fetch neighbors once expanded so we can provide an sr-only list for AT.
  useEffect(() => {
    if (!expanded || neighbors.length > 0) return
    fetchNeighborhood(entitySlug, hops)
      .then((resp) => {
        // Exclude the focal entity itself from the neighbor list.
        setNeighbors(resp.nodes.filter((n) => !n.focused))
      })
      .catch(() => {
        // Silent: AT neighbor list is a progressive enhancement, not a hard requirement.
      })
  }, [expanded, entitySlug, hops, neighbors.length])

  return (
    <section aria-labelledby="wiki-minigraph-heading">
      <div className="mb-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
          aria-expanded={expanded}
          aria-controls="wiki-minigraph-panel"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3 w-3" aria-hidden="true" />
          )}
          <span id="wiki-minigraph-heading">Graph context</span>
        </button>
        {expanded && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleOpenAtlas}
            className="h-7 gap-1 px-2 text-label-xs"
            aria-label={`Open ${entityName} in Atlas`}
          >
            <Compass className="h-3 w-3" aria-hidden="true" />
            Open in Atlas
            <ExternalLink className="h-2.5 w-2.5 opacity-60" aria-hidden="true" />
          </Button>
        )}
      </div>
      {expanded && (
        <div
          id="wiki-minigraph-panel"
          data-testid="wiki-minigraph-panel"
          className="h-72 overflow-hidden rounded-lg border border-border bg-card/40"
        >
          {/* SR-only 1-hop neighbor list for assistive technology */}
          {neighbors.length > 0 && (
            <ul className="sr-only" aria-label={`1-hop neighbors of ${entityName}`}>
              {neighbors.map((node) => (
                <li key={node.id}>
                  {node.name} ({node.type}, {node.trust_state} trust,{" "}
                  {node.mention_count} mentions)
                </li>
              ))}
            </ul>
          )}
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading graph…
              </div>
            }
          >
            <Atlas entity={entitySlug} hops={hops} onHopsChange={setHops} />
          </Suspense>
        </div>
      )}
    </section>
  )
}
