// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tag, Loader2, Merge, Trash2, X } from "lucide-react"
import { fetchAllTags, mergeTags } from "@/lib/api"
import type { TagInfo } from "@/lib/api"

interface TagManagerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function TagManager({ open, onOpenChange }: TagManagerProps) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState("")
  const [mergeSource, setMergeSource] = useState<string | null>(null)
  const [mergeTarget, setMergeTarget] = useState("")
  const [merging, setMerging] = useState(false)
  const [result, setResult] = useState("")

  const { data: tags, isLoading } = useQuery({
    queryKey: ["all-tags"],
    queryFn: fetchAllTags,
    enabled: open,
    staleTime: 30_000,
  })

  const filteredTags = (tags ?? []).filter(
    (t) => !filter || t.name.toLowerCase().includes(filter.toLowerCase()),
  )

  const handleMerge = useCallback(async () => {
    if (!mergeSource || !mergeTarget.trim()) return
    setMerging(true)
    setResult("")
    try {
      const res = await mergeTags(mergeSource, mergeTarget.trim().toLowerCase())
      setResult(`Merged "${mergeSource}" → "${mergeTarget.trim().toLowerCase()}" (${res.artifacts_updated} artifacts updated)`)
      setMergeSource(null)
      setMergeTarget("")
      queryClient.invalidateQueries({ queryKey: ["all-tags"] })
      queryClient.invalidateQueries({ queryKey: ["tag-suggestions"] })
    } catch (e) {
      setResult(e instanceof Error ? e.message : "Merge failed")
    } finally {
      setMerging(false)
    }
  }, [mergeSource, mergeTarget, queryClient])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Tag className="h-4 w-4" />
            Manage Tags
          </DialogTitle>
          <DialogDescription>
            View all tags by usage count. Click a tag to merge it into another.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Input
            placeholder="Filter tags..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-8 text-sm"
          />

          {mergeSource && (
            <div className="flex items-center gap-2 rounded border bg-muted/30 p-2">
              <span className="text-xs text-muted-foreground">Merge</span>
              <Badge variant="secondary" className="text-xs">{mergeSource}</Badge>
              <span className="text-xs text-muted-foreground">into</span>
              <Input
                placeholder="target tag..."
                value={mergeTarget}
                onChange={(e) => setMergeTarget(e.target.value)}
                className="h-6 flex-1 text-xs"
                autoFocus
                onKeyDown={(e) => { if (e.key === "Enter") handleMerge() }}
              />
              <Button
                variant="default"
                size="sm"
                className="h-6 px-2 text-xs"
                disabled={merging || !mergeTarget.trim()}
                onClick={handleMerge}
              >
                {merging ? <Loader2 className="h-3 w-3 animate-spin" /> : <Merge className="h-3 w-3" />}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => { setMergeSource(null); setMergeTarget("") }}
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
          )}

          {result && (
            <p className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground">{result}</p>
          )}

          <ScrollArea className="h-[300px]">
            {isLoading && (
              <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Loading tags...
              </div>
            )}
            {!isLoading && filteredTags.length === 0 && (
              <p className="py-4 text-center text-xs text-muted-foreground">
                {filter ? "No matching tags" : "No tags found"}
              </p>
            )}
            <div className="flex flex-wrap gap-1.5 p-1">
              {filteredTags.map((tag) => (
                <button
                  key={tag.name}
                  type="button"
                  className="inline-flex items-center gap-1 rounded border bg-muted/30 px-2 py-0.5 text-xs transition-colors hover:bg-accent"
                  onClick={() => {
                    setMergeSource(tag.name)
                    setMergeTarget("")
                  }}
                  title={`${tag.usage_count} artifact${tag.usage_count !== 1 ? "s" : ""} — click to merge`}
                >
                  <span>{tag.name}</span>
                  <span className="text-[10px] text-muted-foreground">({tag.usage_count})</span>
                </button>
              ))}
            </div>
          </ScrollArea>
        </div>

        <DialogFooter>
          <p className="text-xs text-muted-foreground">
            {tags ? `${tags.length} tags total` : ""}
          </p>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
