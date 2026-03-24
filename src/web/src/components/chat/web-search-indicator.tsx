// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Globe, ExternalLink } from "lucide-react"
import { cn } from "@/lib/utils"

/** Extract hostname from a URL for compact display. */
function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "")
  } catch {
    return url
  }
}

interface WebSearchIndicatorProps {
  active: boolean
  query?: string
  sourceUrls?: string[]
}

/**
 * Compact inline indicator shown during verification when web search is active.
 * Renders inside the verification overlay area beneath a message bubble.
 *
 * - When `active`: animated globe icon + "Searching the web..." text
 * - When source URLs arrive: small link pills below the indicator
 * - Fades in/out via CSS transition
 */
export function WebSearchIndicator({ active, query, sourceUrls }: WebSearchIndicatorProps) {
  const hasUrls = sourceUrls && sourceUrls.length > 0

  if (!active && !hasUrls) return null

  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-lg border border-teal-500/20 bg-teal-500/5 px-3 py-2 transition-opacity duration-300",
        active ? "opacity-100" : "opacity-80",
      )}
    >
      {/* Search status line */}
      <div className="flex items-center gap-2">
        <Globe
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-teal-400",
            active && "animate-spin",
          )}
          style={active ? { animationDuration: "2s" } : undefined}
        />
        <span className="text-xs text-teal-300/90">
          {active ? "Searching the web\u2026" : "Web search complete"}
        </span>
        {query && (
          <span className="ml-1 truncate text-[11px] text-muted-foreground italic max-w-[200px]">
            {query}
          </span>
        )}
      </div>

      {/* Source URL pills */}
      {hasUrls && (
        <div className="flex flex-wrap gap-1.5">
          {sourceUrls.slice(0, 5).map((url, i) => (
            <a
              key={i}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-full bg-teal-500/10 px-2 py-0.5 text-[10px] text-teal-400 transition-colors hover:bg-teal-500/20 hover:text-teal-300"
            >
              <ExternalLink className="h-2.5 w-2.5 shrink-0" />
              {hostname(url)}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
