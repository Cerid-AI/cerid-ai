// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useQuery } from "@tanstack/react-query"
import { Users, AlertCircle, BookOpen } from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { EmptyState } from "@/components/ui/empty-state"
import { fetchWikiConcept } from "@/lib/api/wiki-browse"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso: string): string {
  try {
    const seconds = Math.floor((Date.now() - Date.parse(iso)) / 1000)
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return ""
  }
}

function entityTypeLabel(t: string): string {
  const map: Record<string, string> = {
    ORG: "Organization",
    PERSON: "Person",
    OTHER: "Other",
    CONCEPT: "Concept",
    EVENT: "Event",
    PLACE: "Place",
  }
  return map[t.toUpperCase()] ?? t
}

// ---------------------------------------------------------------------------
// Loading skeleton matched to article shape
// ---------------------------------------------------------------------------

function ConceptPageSkeleton() {
  return (
    <div className="space-y-4 p-4" role="status" aria-label="Loading concept page">
      <Skeleton className="h-7 w-1/2" />
      <Skeleton className="h-4 w-1/4" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-5/6" />
      <Skeleton className="h-3 w-4/6" />
      <div className="mt-4 space-y-2">
        <Skeleton className="h-4 w-1/4" />
        {[...Array(6)].map((_, i) => (
          <Skeleton key={i} className="h-8 w-full rounded-md" />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ConceptPage
// ---------------------------------------------------------------------------

export interface ConceptPageProps {
  /** Community id, e.g. "concept:0:2625" or bare "0:2625" */
  conceptId: string
  onSelectEntity: (slug: string) => void
}

export function ConceptPage({ conceptId, onSelectEntity }: ConceptPageProps) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["wiki-concept", conceptId],
    queryFn: () => fetchWikiConcept(conceptId),
    staleTime: 5 * 60_000,
    retry: 1,
  })

  if (isLoading) {
    return (
      <div className="flex h-full flex-col overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl px-4 py-6">
          <ConceptPageSkeleton />
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full flex-col overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl px-4 py-6">
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            <AlertTitle>Failed to load concept</AlertTitle>
            <AlertDescription className="flex items-center justify-between gap-2">
              <span>{error instanceof Error ? error.message : "An error occurred."}</span>
              <button
                type="button"
                onClick={() => void refetch()}
                className="shrink-0 text-xs underline underline-offset-2 hover:no-underline"
              >
                Retry
              </button>
            </AlertDescription>
          </Alert>
        </div>
      </div>
    )
  }

  if (data === null) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <EmptyState
          icon={BookOpen}
          title="Concept not found"
          description="This community concept page does not exist or has not yet been indexed."
        />
      </div>
    )
  }

  if (!data) return null

  // Detect placeholder name from backend (pre-Agent-A label fix)
  const isPlaceholderName = /^Concept \d+:\d+$/.test(data.name)
  const displayName = isPlaceholderName
    ? `Community ${data.slug.replace(/^concept:/, "")}`
    : data.name

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6">

        {/* Page title */}
        <header>
          <h1 className="text-2xl font-bold text-foreground">{displayName}</h1>
          <div className="mt-1 flex items-center gap-3 text-sm text-muted-foreground">
            <Badge variant="secondary" className="text-label-xs">
              Community · level {data.level}
            </Badge>
            <span className="tabular-nums">{data.member_count} members</span>
            {data.last_updated_at && (
              <span className="text-xs">
                Updated {relativeTime(data.last_updated_at)}
              </span>
            )}
          </div>
        </header>

        {/* Summary */}
        <section aria-labelledby="concept-summary-heading">
          <Card>
            <CardContent className="pt-4 pb-4 px-4">
              {data.summary ? (
                <p className="text-sm leading-relaxed text-foreground/90">{data.summary}</p>
              ) : (
                <p className="text-sm text-muted-foreground italic">
                  Community summary not yet generated — coverage grows nightly.
                </p>
              )}
            </CardContent>
          </Card>
        </section>

        {/* Member entities */}
        <section aria-labelledby="concept-members-heading">
          <Card>
            <CardHeader className="pb-2 pt-3 px-3">
              <h2
                id="concept-members-heading"
                className="flex items-center gap-1.5 text-label-xs font-semibold uppercase tracking-wider text-muted-foreground"
              >
                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                Members
                <span className="ml-1 font-normal tabular-nums">({data.member_count})</span>
              </h2>
            </CardHeader>
            <CardContent className="px-0 pb-2">
              {data.members.length === 0 ? (
                <EmptyState
                  icon={Users}
                  title="No members indexed"
                  description="Members are populated by the nightly community detection job."
                />
              ) : (
                <ul className="divide-y text-sm">
                  {data.members.map((member) => (
                    <li key={member.slug} className="flex items-center gap-3 px-3 py-2">
                      <button
                        type="button"
                        onClick={() => onSelectEntity(member.slug)}
                        className="flex-1 text-left font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                      >
                        {member.name}
                      </button>
                      <Badge variant="outline" className="text-label-xs shrink-0">
                        {entityTypeLabel(member.entity_type)}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </section>

      </div>
    </div>
  )
}
