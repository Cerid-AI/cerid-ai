// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { fetchRelatedArtifacts } from "@/lib/api"
import { DomainBadge } from "./domain-filter"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { GitBranch, Loader2, ArrowLeft } from "lucide-react"

interface GraphPreviewProps {
  artifactId: string
}

export function GraphPreview({ artifactId }: GraphPreviewProps) {
  const [stack, setStack] = useState<string[]>([])
  const currentId = stack.length > 0 ? stack[stack.length - 1] : artifactId

  // Reset stack when root artifact changes
  useEffect(() => {
    setStack([])
  }, [artifactId])

  const { data: related, isLoading } = useQuery({
    queryKey: ["related", currentId],
    queryFn: () => fetchRelatedArtifacts(currentId),
    staleTime: 120_000,
  })

  const navigateTo = useCallback((targetId: string) => {
    setStack((prev) => [...prev, targetId])
  }, [])

  const goBack = useCallback(() => {
    setStack((prev) => prev.slice(0, -1))
  }, [])

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading connections...
      </div>
    )
  }

  if (!related || related.length === 0) {
    return (
      <div className="p-3">
        {stack.length > 0 && (
          <Button variant="ghost" size="sm" className="mb-1 h-6 px-1.5 text-xs" onClick={goBack}>
            <ArrowLeft className="mr-1 h-3 w-3" /> Back
          </Button>
        )}
        <p className="text-xs text-muted-foreground">No graph connections found</p>
      </div>
    )
  }

  return (
    <div className="space-y-1 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {stack.length > 0 && (
          <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={goBack} aria-label="Go back">
            <ArrowLeft className="h-3 w-3" />
          </Button>
        )}
        <GitBranch className="h-3 w-3" />
        <span>Connected ({related.length})</span>
        {stack.length > 0 && (
          <Badge variant="secondary" className="text-[10px]">
            depth {stack.length}
          </Badge>
        )}
      </div>
      {related.map((item) => (
        <div
          key={item.id}
          className="flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/50"
          role="button"
          tabIndex={0}
          aria-label={`Explore ${item.filename} connections`}
          onClick={() => navigateTo(item.id)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigateTo(item.id) } }}
        >
          <DomainBadge domain={item.domain} />
          <span className="min-w-0 flex-1 truncate">{item.filename}</span>
          <Badge variant="outline" className="text-[10px]">
            {item.relationship_type}
          </Badge>
        </div>
      ))}
    </div>
  )
}