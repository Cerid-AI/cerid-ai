// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { ChevronRight, ChevronDown, FolderOpen, Folder, Loader2 } from "lucide-react"
import { fetchTaxonomy } from "@/lib/api"
import { cn } from "@/lib/utils"

const DOMAIN_COLORS: Record<string, string> = {
  coding: "text-blue-600 dark:text-blue-400",
  finance: "text-green-600 dark:text-green-400",
  projects: "text-purple-600 dark:text-purple-400",
  personal: "text-orange-600 dark:text-orange-400",
  general: "text-zinc-600 dark:text-zinc-400",
  conversations: "text-cyan-600 dark:text-cyan-400",
}

interface TaxonomyFilter {
  domain: string | null
  subCategory: string | null
}

interface TaxonomyTreeProps {
  filter: TaxonomyFilter
  onFilterChange: (filter: TaxonomyFilter) => void
  artifactCounts?: Map<string, number>
}

export function TaxonomyTree({ filter, onFilterChange, artifactCounts }: TaxonomyTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const { data: taxonomy, isLoading } = useQuery({
    queryKey: ["taxonomy"],
    queryFn: fetchTaxonomy,
    staleTime: 300_000,
  })

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading taxonomy...
      </div>
    )
  }

  if (!taxonomy?.domains) return null

  const toggleExpand = (domain: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(domain)) next.delete(domain)
      else next.add(domain)
      return next
    })
  }

  const handleDomainClick = (domain: string) => {
    if (filter.domain === domain && !filter.subCategory) {
      onFilterChange({ domain: null, subCategory: null })
    } else {
      onFilterChange({ domain, subCategory: null })
    }
    if (!expanded.has(domain)) toggleExpand(domain)
  }

  const handleSubCategoryClick = (domain: string, subCategory: string) => {
    if (filter.domain === domain && filter.subCategory === subCategory) {
      onFilterChange({ domain, subCategory: null })
    } else {
      onFilterChange({ domain, subCategory })
    }
  }

  const clearFilter = () => {
    onFilterChange({ domain: null, subCategory: null })
  }

  const hasFilter = filter.domain !== null

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between px-2">
        <span className="text-xs font-medium text-muted-foreground">Taxonomy</span>
        {hasFilter && (
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-[10px]"
            onClick={clearFilter}
          >
            Clear
          </Button>
        )}
      </div>
      <ScrollArea className="max-h-[280px]">
        <div className="space-y-0.5 px-1">
          {Object.entries(taxonomy.domains).map(([domain, info]) => {
            const isExpanded = expanded.has(domain)
            const isActive = filter.domain === domain
            const domainCount = artifactCounts?.get(domain) ?? (info.artifact_count > 0 ? info.artifact_count : undefined)

            return (
              <div key={domain}>
                <button
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs transition-colors",
                    "hover:bg-muted/50",
                    isActive && !filter.subCategory && "bg-primary/10 font-medium",
                  )}
                  onClick={() => handleDomainClick(domain)}
                  aria-expanded={isExpanded}
                >
                  <span
                    className="shrink-0 cursor-pointer"
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleExpand(domain)
                    }}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); toggleExpand(domain) } }}
                    aria-label={isExpanded ? "Collapse" : "Expand"}
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-3 w-3 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-3 w-3 text-muted-foreground" />
                    )}
                  </span>
                  {isExpanded ? (
                    <FolderOpen className={cn("h-3.5 w-3.5 shrink-0", DOMAIN_COLORS[domain])} />
                  ) : (
                    <Folder className={cn("h-3.5 w-3.5 shrink-0", DOMAIN_COLORS[domain])} />
                  )}
                  <span className="flex-1 truncate capitalize">{domain}</span>
                  {domainCount !== undefined && (
                    <Badge variant="secondary" className="h-4 px-1 text-[9px]">
                      {domainCount}
                    </Badge>
                  )}
                </button>

                {isExpanded && Array.isArray(info.sub_categories) && info.sub_categories.length > 0 && (
                  <div className="ml-5 space-y-0.5 border-l pl-2">
                    {info.sub_categories.map((subCat) => {
                      const label = typeof subCat === "string" ? subCat : subCat.name
                      const count = typeof subCat === "string" ? undefined : subCat.artifact_count
                      const isSubActive = filter.domain === domain && filter.subCategory === label
                      return (
                        <button
                          key={label}
                          className={cn(
                            "flex w-full items-center gap-1.5 rounded px-2 py-0.5 text-left text-[11px] transition-colors",
                            "hover:bg-muted/50",
                            isSubActive && "bg-primary/10 font-medium",
                          )}
                          onClick={() => handleSubCategoryClick(domain, label)}
                        >
                          <span className="flex-1 truncate capitalize">{label}</span>
                          {count !== undefined && count > 0 && (
                            <Badge variant="secondary" className="h-3.5 px-1 text-[8px]">
                              {count}
                            </Badge>
                          )}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </ScrollArea>
    </div>
  )
}
