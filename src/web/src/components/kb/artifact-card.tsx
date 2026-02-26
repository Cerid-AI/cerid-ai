import { useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ChevronDown, ChevronUp, PlusCircle, GitBranch, Globe } from "lucide-react"
import { DomainBadge } from "./domain-filter"
import type { KBQueryResult } from "@/lib/types"
import { cn } from "@/lib/utils"

interface ArtifactCardProps {
  result: KBQueryResult
  isSelected: boolean
  onSelect: () => void
  onInject: () => void
}

export function ArtifactCard({ result, isSelected, onSelect, onInject }: ArtifactCardProps) {
  const [expanded, setExpanded] = useState(false)
  const relevancePct = Math.round(result.relevance * 100)
  const showRelevance = result.relevance > 0
  const cleanContent = result.content.replace(/\s+/g, " ").trim()
  const dateStr = result.ingested_at
    ? new Date(result.ingested_at).toLocaleDateString()
    : null

  return (
    <Card
      className={cn(
        "cursor-pointer overflow-hidden transition-colors",
        isSelected && "ring-2 ring-primary",
      )}
      onClick={onSelect}
    >
      <CardContent className="min-w-0 p-3">
        {/* Header row */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{result.filename}</p>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <DomainBadge domain={result.domain} />
              {dateStr && (
                <span className="text-[10px] text-muted-foreground">{dateStr}</span>
              )}
              {result.graph_source && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <GitBranch className="h-3 w-3" />
                  graph
                </Badge>
              )}
              {result.cross_domain && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <Globe className="h-3 w-3" />
                  cross
                </Badge>
              )}
              {result.sub_category && result.sub_category !== "general" && (
                <Badge variant="secondary" className="text-[10px]">
                  {result.sub_category}
                </Badge>
              )}
            </div>
            {result.tags && result.tags.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {result.tags.slice(0, 4).map((tag) => (
                  <span key={tag} className="inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {tag}
                  </span>
                ))}
                {result.tags.length > 4 && (
                  <span className="text-[10px] text-muted-foreground">+{result.tags.length - 4}</span>
                )}
              </div>
            )}
          </div>
          {showRelevance && (
            <div className="flex flex-col items-end gap-1">
              <span className="text-xs font-medium tabular-nums">{relevancePct}%</span>
              <div className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${relevancePct}%` }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Content */}
        <p className={cn(
          "mt-2 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]",
          !expanded && "line-clamp-3",
        )}>
          {expanded ? result.content : cleanContent}
        </p>

        {/* Actions */}
        <div className="mt-2 flex items-center gap-1">
          {cleanContent.length > 150 && (
            <Button
              variant="ghost"
              size="xs"
              onClick={(e) => {
                e.stopPropagation()
                setExpanded(!expanded)
              }}
            >
              {expanded ? (
                <><ChevronUp className="mr-1 h-3 w-3" />Less</>
              ) : (
                <><ChevronDown className="mr-1 h-3 w-3" />More</>
              )}
            </Button>
          )}
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="xs"
            className="text-primary"
            onClick={(e) => {
              e.stopPropagation()
              onInject()
            }}
          >
            <PlusCircle className="mr-1 h-3 w-3" />
            Inject
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
