// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Workstream E Phase E.6.3 — Inference Models card.
// Surfaces the CERID_PRELOAD_MODELS choice in the Settings UI:
// shows whether the reranker + embedder ONNX models are cached,
// offers a one-click "Download now" to warm the cache so users
// on lean Docker images don't hit a silent 5-15s stall on their
// first semantic query.

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, Download, Loader2, AlertTriangle, RefreshCw } from "lucide-react"

import {
  fetchModelsStatus,
  preloadModels,
  type ModelCacheStatus,
  type ModelsPreloadResponse,
} from "@/lib/api/settings"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

function ModelRow({
  label,
  description,
  status,
}: {
  label: string
  description: string
  status: ModelCacheStatus | undefined
}) {
  if (status === undefined) {
    return (
      <div className="flex items-center justify-between py-2">
        <div>
          <div className="text-sm font-medium">{label}</div>
          <div className="text-xs text-muted-foreground">{description}</div>
        </div>
        <Badge variant="outline" className="text-xs">unknown</Badge>
      </div>
    )
  }
  return (
    <div className="flex items-center justify-between py-2">
      <div className="min-w-0">
        <div className="text-sm font-medium truncate">{label}</div>
        <div className="text-xs text-muted-foreground truncate" title={status.repo}>
          {status.repo}
        </div>
      </div>
      {status.cached ? (
        <Badge variant="default" className="gap-1 text-xs">
          <CheckCircle2 className="h-3 w-3" /> cached
        </Badge>
      ) : (
        <Badge variant="outline" className="gap-1 text-xs">
          <Download className="h-3 w-3" /> not cached
        </Badge>
      )}
    </div>
  )
}

function PreloadResultLine({ result }: { result: ModelsPreloadResponse }) {
  const ok = result.status === "ok"
  return (
    <div
      role="status"
      className={`mt-3 rounded-md border px-3 py-2 text-xs ${
        ok ? "border-green-500/30 bg-green-500/5" : "border-amber-500/30 bg-amber-500/5"
      }`}
    >
      {ok ? (
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-3 w-3" />
          <span>
            Models loaded. Reranker {result.reranker_ms?.toFixed(0) ?? "?"}ms ·
            Embedder {result.embedder_ms?.toFixed(0) ?? "?"}ms ·
            Total {result.total_ms.toFixed(0)}ms.
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-3 w-3" />
            <span className="font-medium">Partial — one or both loaders failed.</span>
          </div>
          {result.reranker_status === "failed" && (
            <div className="text-muted-foreground">
              Reranker: {result.reranker_error ?? "unknown error"}
            </div>
          )}
          {result.embedder_status === "failed" && (
            <div className="text-muted-foreground">
              Embedder: {result.embedder_error ?? "unknown error"}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function InferenceModelsCard() {
  const queryClient = useQueryClient()
  const [preloading, setPreloading] = useState(false)
  const [preloadResult, setPreloadResult] = useState<ModelsPreloadResponse | null>(null)
  const [preloadError, setPreloadError] = useState<string | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["inference-models-status"],
    queryFn: fetchModelsStatus,
    staleTime: 30_000,
  })

  const handlePreload = async () => {
    setPreloading(true)
    setPreloadError(null)
    setPreloadResult(null)
    try {
      const result = await preloadModels()
      setPreloadResult(result)
      // Refresh status query so badges flip to "cached"
      await queryClient.invalidateQueries({ queryKey: ["inference-models-status"] })
    } catch (err) {
      setPreloadError(err instanceof Error ? err.message : String(err))
    } finally {
      setPreloading(false)
    }
  }

  // Optional chain through both levels — when the API mock in a sibling
  // test isn't installed, `data` may be undefined OR its inner shape may
  // be incomplete. The card's render gate handles `!data`; these flags
  // just need to coerce to booleans cleanly.
  const allCached = data?.reranker?.cached === true && data?.embedder?.cached === true
  const noneCached = data?.reranker?.cached === false && data?.embedder?.cached === false

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Inference Models</CardTitle>
        <CardDescription>
          ONNX reranker + embedder used for retrieval. Download once to skip the
          first-query stall on lean Docker images (CERID_PRELOAD_MODELS=false).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Checking model cache…
          </div>
        )}

        {data && (
          <>
            <div className="divide-y">
              <ModelRow
                label="Reranker"
                description="Cross-encoder for top-N rerank"
                status={data.reranker}
              />
              <ModelRow
                label="Embedder"
                description="Sentence embedder (768-dim ONNX)"
                status={data.embedder}
              />
            </div>

            <div className="mt-3 flex items-center gap-2">
              <Button
                variant={allCached ? "outline" : "default"}
                size="sm"
                onClick={handlePreload}
                disabled={preloading}
              >
                {preloading ? (
                  <>
                    <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                    Downloading…
                  </>
                ) : allCached ? (
                  <>
                    <RefreshCw className="mr-2 h-3 w-3" />
                    Re-warm cache
                  </>
                ) : (
                  <>
                    <Download className="mr-2 h-3 w-3" />
                    Download models (~38 MB)
                  </>
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => refetch()}
                disabled={preloading}
              >
                Refresh status
              </Button>
            </div>

            {noneCached && !preloading && !preloadResult && (
              <p className="mt-3 text-xs text-muted-foreground">
                Neither model is cached. The first semantic query after startup
                will block ~5-15s while they download from HuggingFace. Click
                <span className="mx-1 font-medium">Download models</span>
                to warm the cache now.
              </p>
            )}

            {preloadError && (
              <div
                role="alert"
                className="mt-3 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs"
              >
                <AlertTriangle className="mr-1 inline h-3 w-3" />
                {preloadError}
              </div>
            )}

            {preloadResult && <PreloadResultLine result={preloadResult} />}
          </>
        )}
      </CardContent>
    </Card>
  )
}
