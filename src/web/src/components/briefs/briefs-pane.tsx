// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * BriefsPane — daily/weekly briefs list + detail (Task 2.2).
 *
 * Kind switcher (Daily | Weekly — the backend only accepts those two
 * kinds; there is no "inbox" brief kind). Each kind panel's drill-down
 * selection is keyed by kind and lifted up into this component rather
 * than owned by `BriefsKindView` itself: Radix `TabsContent` unmounts
 * the inactive tab's subtree (no `forceMount`), so panel-local selection
 * state would be destroyed on every tab switch. Lifting it here — the
 * component that stays mounted for the pane's whole lifetime — is what
 * makes each kind's drill-down survive switching tabs.
 *
 * Detail is resolved by finding the selected id in the already-fetched
 * list rather than a second per-id fetch: GET /briefs returns fully
 * hydrated BriefView objects (sections + claims), identical in shape to
 * GET /briefs/{id}, so a second round-trip would only add a redundant
 * loading flicker.
 */

import { useState } from "react"
import { Newspaper } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { PaneError } from "@/components/ui/pane-error"
import { EmptyState } from "@/components/ui/empty-state"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useBriefs } from "@/hooks/use-briefs"
import { BriefsList } from "./briefs-list"
import { BriefDetail } from "./brief-detail"
import type { BriefKind } from "@/lib/types/brief"

const EMPTY_COPY: Record<BriefKind, { title: string; description: string }> = {
  daily: {
    title: "No daily briefs yet",
    description: "Your first daily brief arrives at 06:00",
  },
  weekly: {
    title: "No weekly briefs yet",
    description: "Your first weekly brief arrives Monday morning",
  },
}

function BriefsSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading briefs">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-28 w-full rounded-xl" />
      ))}
    </div>
  )
}

function BriefsKindView({
  kind,
  selectedId,
  onSelectId,
}: {
  kind: BriefKind
  selectedId: string | null
  onSelectId: (id: string | null) => void
}) {
  const { data: briefs, isLoading, isError, refetch } = useBriefs(kind)

  if (isLoading) return <BriefsSkeleton />

  if (isError) {
    return (
      <PaneError
        title="Failed to load briefs"
        description="Check that the backend is running, then retry."
        onRetry={() => void refetch()}
      />
    )
  }

  if (!briefs || briefs.length === 0) {
    const copy = EMPTY_COPY[kind]
    return <EmptyState icon={Newspaper} title={copy.title} description={copy.description} />
  }

  const selected = briefs.find((b) => b.id === selectedId) ?? null

  if (selected) {
    return <BriefDetail brief={selected} onBack={() => onSelectId(null)} />
  }

  return <BriefsList briefs={briefs} onSelect={onSelectId} />
}

export default function BriefsPane() {
  const [kind, setKind] = useState<BriefKind>("daily")
  const [selectedByKind, setSelectedByKind] = useState<{ daily: string | null; weekly: string | null }>({
    daily: null,
    weekly: null,
  })

  return (
    <div className="flex h-full flex-col">
      <header className="shrink-0 border-b px-6 py-5">
        <h1 className="text-xl font-semibold text-foreground">Briefs</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Daily and weekly summaries, with every claim traced to a source.
        </p>
      </header>

      <Tabs
        value={kind}
        onValueChange={(v) => setKind(v as BriefKind)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="shrink-0 border-b px-6 py-3">
          <TabsList>
            <TabsTrigger value="daily">Daily</TabsTrigger>
            <TabsTrigger value="weekly">Weekly</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="daily" className="min-h-0 flex-1">
          <ScrollArea className="h-full">
            <div className="mx-auto max-w-3xl px-6 py-6">
              <BriefsKindView
                kind="daily"
                selectedId={selectedByKind.daily}
                onSelectId={(id) => setSelectedByKind((prev) => ({ ...prev, daily: id }))}
              />
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="weekly" className="min-h-0 flex-1">
          <ScrollArea className="h-full">
            <div className="mx-auto max-w-3xl px-6 py-6">
              <BriefsKindView
                kind="weekly"
                selectedId={selectedByKind.weekly}
                onSelectId={(id) => setSelectedByKind((prev) => ({ ...prev, weekly: id }))}
              />
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  )
}
