// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// EntityAnalysisDrawer — CN2: deep multi-layer analysis of a constellation
// node, shown in a right-anchored side drawer WITHOUT navigating away from
// the Constellation surface.
//
// Why a Sheet (Radix Dialog) rather than an in-flow side panel:
//   The Sheet renders into a Radix Portal (document.body), so the drawer
//   DOM lives entirely outside the Constellation's container subtree. This
//   is load-bearing for the sigma v3 bug: the Cartographer map must NEVER
//   remount or have its container resized, or it crashes with "could not
//   find a suitable program for node type circle" / "Container has no
//   width". An overlay drawer (fixed, portalled) opens on top of the map
//   without touching the map container's box, so sigma keeps its instance.
//
// Content = EntityDetailView (the existing rich multi-layer analysis:
//   header + trust band + infobox + MiniGraph neighborhood + mention
//   sparkline + provenance + contradictions + external refs + history).
//   At the drawer's < lg width EntityDetailView collapses to its single
//   reading column, which fits the drawer. MiniGraph only spins up its own
//   sigma instance when the user expands "Graph context" — that instance is
//   independent of the constellation map's sigma instance, so no conflict.

import { Suspense, lazy } from "react"
import { Loader2 } from "lucide-react"
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { VisuallyHidden } from "radix-ui"

// Lazy-load the heavy detail view (markdown + chart deps) so the
// Constellation bundle doesn't grow for users who never click a node.
const EntityDetailView = lazy(() =>
  import("@/components/wiki/entity-detail-view").then((m) => ({
    default: m.EntityDetailView,
  })),
)

export interface EntityAnalysisDrawerProps {
  /** Slug of the clicked entity. `null` keeps the drawer closed. */
  slug: string | null
  /** Close request (overlay click / Esc / Close button). */
  onClose: () => void
  /**
   * Re-target the drawer to a related entity (e.g. a "Mentioned together"
   * chip) without leaving the Constellation surface.
   */
  onSelectRelated: (slug: string) => void
}

export function EntityAnalysisDrawer({
  slug,
  onClose,
  onSelectRelated,
}: EntityAnalysisDrawerProps) {
  const open = slug !== null
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <SheetContent
        side="right"
        // Wider than the default max-w-sm so the analysis breathes, but
        // still well under the `lg` breakpoint so EntityDetailView renders
        // its single reading column rather than the 3-column article grid.
        className="flex w-full flex-col gap-0 p-0 sm:max-w-md"
        data-testid="entity-analysis-drawer"
        aria-label="Entity analysis"
      >
        <VisuallyHidden.Root>
          <SheetTitle>Entity analysis</SheetTitle>
          <SheetDescription>
            Deep multi-layer analysis of the selected entity.
          </SheetDescription>
        </VisuallyHidden.Root>
        {slug && (
          <div className="min-h-0 flex-1 overflow-hidden">
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                  Loading analysis…
                </div>
              }
            >
              <EntityDetailView
                slug={slug}
                onSelectRelated={onSelectRelated}
              />
            </Suspense>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
