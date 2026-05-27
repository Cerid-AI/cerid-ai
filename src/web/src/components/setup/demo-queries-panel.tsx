// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DemoQueriesPanel — shown after a sample pack installs successfully.
 *
 * Renders 3 canned queries for the installed pack. Clicking a query runs it
 * against /agent/query (via queryKB) scoped to the just-installed pack via
 * ``metadata_filter: { pack_id }`` so retrieval can't bleed into the rest of
 * the KB. The top result's filename + relevance score is rendered alongside
 * the answer so the user can verify provenance. "Continue to chat" calls the
 * completion callback so the wizard can advance.
 *
 * Design constraints:
 * - shadcn/ui + lucide icons only; no hex literals; no inline style={{}}
 * - aria-live on loading state; keyboard-reachable buttons
 * - React 19 + tanstack-react-query (mutation per click)
 */

import { useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Loader2, ChevronRight, MessagesSquare, Check, AlertCircle, FileText } from "lucide-react"
import { cn } from "@/lib/utils"
import { queryKB } from "@/lib/api/kb"
import type { KnowledgePackSummary } from "@/lib/api/knowledge-packs"
import type { KBQueryResult } from "@/lib/types"

// ---------------------------------------------------------------------------
// Per-pack canned demo queries
// ---------------------------------------------------------------------------

/** Query set per pack id. Falls back to GENERIC_QUERIES when the pack is not listed. */
const DEMO_QUERIES: Record<string, readonly [string, string, string]> = {
  "python-stdlib-docs": [
    "How do I read a file with Python's pathlib?",
    "What is the difference between a list and a tuple in Python?",
    "How does the itertools module work?",
  ],
  "irs-publications-curated": [
    "What is the standard deduction for a single filer?",
    "How do I report freelance income on my taxes?",
    "What is a W-2 form used for?",
  ],
  "18f-methods-guides": [
    "What is a design sprint and how do you run one?",
    "How should I write a research plan for user interviews?",
    "What is card sorting and when is it useful?",
  ],
  "cfpb-ask": [
    "What is an APR and how does it affect my credit card balance?",
    "What rights do I have if a debt collector contacts me?",
    "How can I dispute an error on my credit report?",
  ],
  "mdn-web-docs": [
    "How do I use the Fetch API in the browser?",
    "What is the CSS box model?",
    "How do JavaScript Promises work?",
  ],
  "rust-book": [
    "What is Rust's ownership model?",
    "How does borrowing work in Rust?",
    "What are Rust traits?",
  ],
  "typescript-handbook": [
    "What is the difference between an interface and a type alias in TypeScript?",
    "How do generics work in TypeScript?",
    "What is TypeScript's strict mode?",
  ],
}

/** Fallback when the installed pack has no curated queries. */
const GENERIC_QUERIES: readonly [string, string, string] = [
  // TODO: fetch demo queries from pack metadata once the registry schema includes them
  "What are the main topics covered in this pack?",
  "Give me a summary of the key concepts.",
  "What would be a good first thing to learn from this pack?",
]

function queriesForPack(packId: string): readonly [string, string, string] {
  return DEMO_QUERIES[packId] ?? GENERIC_QUERIES
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface DemoQueriesPanelProps {
  /** The pack that was just installed. */
  pack: KnowledgePackSummary
  /** Called when the user clicks "Continue to chat". */
  onComplete: () => void
}

interface QueryResult {
  query: string
  answer: string
  /** Top KB hit that produced ``answer`` — used to render source attribution
   *  under the answer card. ``undefined`` when the response had no results
   *  (e.g. the pack was empty or retrieval timed out into ``context`` only). */
  topSource?: KBQueryResult
}

export function DemoQueriesPanel({ pack, onComplete }: DemoQueriesPanelProps) {
  const queries = queriesForPack(pack.id)
  const [activeQuery, setActiveQuery] = useState<string | null>(null)
  const [loadingQuery, setLoadingQuery] = useState<string | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [queryError, setQueryError] = useState<string | null>(null)

  const handleRunQuery = useCallback(async (query: string) => {
    if (loadingQuery) return
    setLoadingQuery(query)
    setActiveQuery(query)
    setResult(null)
    setQueryError(null)

    try {
      const response = await queryKB(
        query,
        [pack.domain],
        3,
        undefined,
        {
          useReranking: false,
          skipCache: true,
          // Pin retrieval to the just-installed pack so the demo can't bleed
          // into pre-seeded eval corpora or other namespaces. ``pack_id`` is
          // stamped onto every chunk's chromadb metadata by
          // ``app.services.knowledge_packs._install_one``.
          metadataFilter: { pack_id: pack.id },
        },
      )
      const topResult = response.results?.[0]
      const answer = topResult?.content
        ?? response.context
        ?? `Found ${response.total_results} result(s) — query your knowledge base for details.`
      setResult({ query, answer, topSource: topResult })
    } catch {
      setQueryError("Query failed — the knowledge base may still be indexing. Try another question.")
    } finally {
      setLoadingQuery(null)
    }
  }, [loadingQuery, pack.domain, pack.id])

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2 rounded-lg border border-green-500/30 bg-green-500/5 p-2.5">
        <Check className="h-3.5 w-3.5 shrink-0 text-green-600 dark:text-green-400" aria-hidden="true" />
        <p className="text-xs text-green-600 dark:text-green-400">
          <span className="font-medium">{pack.name}</span> installed successfully
          {" "}({pack.artifact_count} articles)
        </p>
      </div>

      {/* Query cards */}
      <div>
        <p className="mb-2 text-center text-xs text-muted-foreground">
          Try a demo query to see your knowledge base in action:
        </p>

        <div
          className="space-y-2"
          aria-label="Demo queries"
          role="list"
        >
          {queries.map((query) => {
            const isActive = activeQuery === query
            const isLoading = loadingQuery === query
            return (
              <div key={query} role="listitem">
                <button
                  type="button"
                  className={cn(
                    "w-full rounded-lg border px-3 py-2.5 text-left text-xs transition-colors",
                    isActive
                      ? "border-brand/40 bg-brand/5 text-foreground"
                      : "border-muted-foreground/20 bg-card text-muted-foreground hover:border-muted-foreground/40 hover:text-foreground",
                  )}
                  onClick={() => handleRunQuery(query)}
                  disabled={!!loadingQuery}
                  aria-pressed={isActive}
                  aria-busy={isLoading}
                >
                  <span className="flex items-center gap-2">
                    {isLoading ? (
                      <Loader2
                        className="h-3 w-3 shrink-0 animate-spin text-brand"
                        aria-hidden="true"
                      />
                    ) : (
                      <MessagesSquare
                        className="h-3 w-3 shrink-0 text-brand/60"
                        aria-hidden="true"
                      />
                    )}
                    {query}
                  </span>
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Loading state */}
      {loadingQuery && (
        <div
          className="flex items-center justify-center gap-2 py-1 text-muted-foreground"
          role="status"
          aria-live="polite"
          aria-label="Querying knowledge base"
        >
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          <span className="text-xs">Querying knowledge base...</span>
        </div>
      )}

      {/* Answer */}
      {result && !loadingQuery && (
        <div
          className="rounded-lg border bg-card p-3"
          role="region"
          aria-label="Query result"
          aria-live="polite"
        >
          <p className="mb-1 text-label-xs font-medium uppercase tracking-wide text-muted-foreground/70">
            Answer
          </p>
          <p className="text-xs leading-relaxed text-foreground line-clamp-6">
            {result.answer}
          </p>

          {/* Source attribution — filename + relevance %.
              Mirrors the main chat's ``SourceAttribution`` (card variant) at
              a compact one-line scale so the wizard step stays under fold. */}
          {result.topSource && (
            <div
              className="mt-2 flex items-center gap-1.5 border-t pt-2 text-xs text-muted-foreground"
              aria-label="Source for this answer"
            >
              <FileText className="h-3 w-3 shrink-0" aria-hidden="true" />
              <span className="min-w-0 truncate font-medium text-foreground">
                {result.topSource.filename}
              </span>
              {result.topSource.relevance > 0 && (
                <span className="ml-auto shrink-0 tabular-nums">
                  {Math.round(result.topSource.relevance * 100)}%
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {queryError && (
        <Alert variant="destructive" role="alert" aria-live="assertive">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <AlertDescription className="text-xs">{queryError}</AlertDescription>
        </Alert>
      )}

      {/* Continue button */}
      <Button
        className="w-full"
        onClick={onComplete}
        aria-label="Continue to chat with your knowledge base"
      >
        Continue to chat
        <ChevronRight className="ml-1 h-3.5 w-3.5" aria-hidden="true" />
      </Button>
    </div>
  )
}
