// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Wiki → Atlas contradiction-lens deep link — Phase M Day 5.
//
// Small affordance that turns the existing wiki contradiction list
// into a Atlas-mode jump-off. Clicking the link opens Subjects →
// Atlas with the contradiction lens pre-activated + focal entity set.

import { AlertTriangle, ArrowRightCircle } from "lucide-react"
import { Button } from "@/components/ui/button"

interface ContradictionLinkProps {
  entitySlug: string
  contradictionCount: number
  onOpenAtlas?: (slug: string) => void
}

export function ContradictionLink({
  entitySlug,
  contradictionCount,
  onOpenAtlas,
}: ContradictionLinkProps) {
  if (contradictionCount <= 0) return null
  return (
    <Button
      variant="outline"
      size="sm"
      className="gap-2 border-amber-500/40 text-amber-700 hover:bg-amber-500/10 dark:text-amber-400"
      onClick={() => onOpenAtlas?.(entitySlug)}
      data-testid="contradiction-link"
    >
      <AlertTriangle className="w-3.5 h-3.5" />
      <span>
        {contradictionCount} contradiction{contradictionCount !== 1 && "s"} —
        view in Atlas
      </span>
      <ArrowRightCircle className="w-3.5 h-3.5" />
    </Button>
  )
}
