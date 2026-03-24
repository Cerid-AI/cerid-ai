// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { ChevronRight, ChevronDown, FolderOpen, Folder, Loader2, Plus, X } from "lucide-react"
import { fetchTaxonomy, createDomain, createSubCategory } from "@/lib/api"
import { cn } from "@/lib/utils"
import { DOMAIN_TEXT_COLORS } from "@/lib/constants"

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
  const [addingDomain, setAddingDomain] = useState(false)
  const [newDomainName, setNewDomainName] = useState("")
  const [addingSubTo, setAddingSubTo] = useState<string | null>(null)
  const [newSubName, setNewSubName] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  const queryClient = useQueryClient()

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

  const handleCreateDomain = async () => {
    const name = newDomainName.trim().toLowerCase()
    if (!name) return
    setSaving(true)
    setError("")
    try {
      await createDomain(name)
      await queryClient.invalidateQueries({ queryKey: ["taxonomy"] })
      setNewDomainName("")
      setAddingDomain(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create domain")
    } finally {
      setSaving(false)
    }
  }

  const handleCreateSubCategory = async (domain: string) => {
    const name = newSubName.trim().toLowerCase()
    if (!name) return
    setSaving(true)
    setError("")
    try {
      await createSubCategory(domain, name)
      await queryClient.invalidateQueries({ queryKey: ["taxonomy"] })
      setNewSubName("")
      setAddingSubTo(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create sub-category")
    } finally {
      setSaving(false)
    }
  }

  const hasFilter = filter.domain !== null

  return (
    <div className="w-full space-y-1 rounded-lg border bg-card/50 p-2">
      <div className="flex items-center justify-between px-2">
        <span className="text-xs font-medium text-muted-foreground">Taxonomy</span>
        <div className="flex gap-0.5">
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
          <Button
            variant="ghost"
            size="sm"
            className="h-5 w-5 p-0"
            onClick={() => { setAddingDomain(!addingDomain); setError("") }}
            title="Add domain"
          >
            <Plus className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {error && (
        <div className="mx-2 rounded bg-destructive/10 px-2 py-1 text-[10px] text-destructive">
          {error}
        </div>
      )}

      {addingDomain && (
        <div className="mx-2 flex items-center gap-1">
          <input
            type="text"
            className="h-6 flex-1 rounded border bg-background px-2 text-[11px] focus:outline-none focus:ring-1 focus:ring-ring"
            placeholder="Domain name..."
            value={newDomainName}
            onChange={(e) => setNewDomainName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCreateDomain(); if (e.key === "Escape") setAddingDomain(false) }}
            autoFocus
            disabled={saving}
          />
          <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={handleCreateDomain} disabled={saving || !newDomainName.trim()}>
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
          </Button>
          <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={() => setAddingDomain(false)} disabled={saving}>
            <X className="h-3 w-3" />
          </Button>
        </div>
      )}

      <ScrollArea className="max-h-[280px] w-full">
        <div className="space-y-0.5 px-1 w-full">
          {Object.entries(taxonomy.domains).map(([domain, info]) => {
            const isExpanded = expanded.has(domain)
            const isActive = filter.domain === domain
            const domainCount = artifactCounts?.get(domain) ?? (info.artifact_count > 0 ? info.artifact_count : undefined)

            return (
              <div key={domain}>
                <div className="group flex items-center">
                  <button
                    className={cn(
                      "flex flex-1 items-center gap-1.5 rounded px-2 py-1 text-left text-xs transition-colors",
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
                        <ChevronDown className="h-3 w-3 text-amber-600 dark:text-yellow-400" />
                      ) : (
                        <ChevronRight className="h-3 w-3 text-amber-600 dark:text-yellow-400" />
                      )}
                    </span>
                    {isExpanded ? (
                      <FolderOpen className={cn("h-3.5 w-3.5 shrink-0", DOMAIN_TEXT_COLORS[domain])} />
                    ) : (
                      <Folder className={cn("h-3.5 w-3.5 shrink-0", DOMAIN_TEXT_COLORS[domain])} />
                    )}
                    <span className="flex-1 truncate capitalize">{domain}</span>
                    {domainCount !== undefined && (
                      <Badge variant="secondary" className="h-4 px-1 text-[9px]">
                        {domainCount}
                      </Badge>
                    )}
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-5 w-5 p-0 opacity-0 group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation()
                      setAddingSubTo(addingSubTo === domain ? null : domain)
                      setNewSubName("")
                      setError("")
                      if (!expanded.has(domain)) toggleExpand(domain)
                    }}
                    title="Add sub-category"
                  >
                    <Plus className="h-2.5 w-2.5" />
                  </Button>
                </div>

                {isExpanded && (
                  <div className="ml-5 space-y-0.5 border-l pl-2">
                    {Array.isArray(info.sub_categories) && info.sub_categories.map((subCat) => {
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
                    {addingSubTo === domain && (
                      <div className="flex items-center gap-1 py-0.5">
                        <input
                          type="text"
                          className="h-5 flex-1 rounded border bg-background px-1.5 text-[10px] focus:outline-none focus:ring-1 focus:ring-ring"
                          placeholder="Sub-category..."
                          value={newSubName}
                          onChange={(e) => setNewSubName(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") handleCreateSubCategory(domain); if (e.key === "Escape") setAddingSubTo(null) }}
                          autoFocus
                          disabled={saving}
                        />
                        <Button variant="ghost" size="sm" className="h-4 w-4 p-0" onClick={() => handleCreateSubCategory(domain)} disabled={saving || !newSubName.trim()}>
                          {saving ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <Plus className="h-2.5 w-2.5" />}
                        </Button>
                        <Button variant="ghost" size="sm" className="h-4 w-4 p-0" onClick={() => setAddingSubTo(null)} disabled={saving}>
                          <X className="h-2.5 w-2.5" />
                        </Button>
                      </div>
                    )}
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
