// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Webhook share card.
 *
 * Liquid Glass surface. Shows the receiver URL (with copy button +
 * brand-mark pulse on copy), a pre-filled curl example, and the
 * require-HMAC toggle status.
 *
 * Mounts inside the wizard's result step when the freshly-created
 * source has kind=webhook.
 */

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { AlertCircle, Check, Copy, Lock, RefreshCw, Webhook } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { mcpUrl, mcpHeaders } from "@/lib/api/common"

interface WebhookUrlPayload {
  url: string
  require_hmac: boolean
  curl_example: string
}

async function fetchWebhookUrl(sourceId: string): Promise<WebhookUrlPayload> {
  const r = await fetch(mcpUrl(`/sources/${sourceId}/webhook-url`).toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(`webhook-url fetch failed: ${r.status}`)
  return r.json()
}

interface WebhookShareCardProps {
  sourceId: string
}

export function WebhookShareCard({ sourceId }: WebhookShareCardProps) {
  const { data, isLoading, isError, refetch } = useQuery<WebhookUrlPayload>({
    queryKey: ["webhook-url", sourceId],
    queryFn: () => fetchWebhookUrl(sourceId),
    staleTime: 5 * 60_000,
  })

  // Checked before the loading guard: once retries exhaust, isLoading goes
  // false while data stays undefined, and `isLoading || !data` alone rendered
  // the skeleton shimmer forever with no way out.
  if (isError) {
    return (
      <div className="liquid-glass rounded-lg px-4 py-3" role="alert">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
          <span className="text-sm font-medium">Couldn&apos;t load the receiver URL</span>
        </div>
        <p className="mt-1 text-label-xs text-muted-foreground">
          The webhook source was created, but fetching its receiver URL failed.
        </p>
        <Button
          size="sm"
          variant="outline"
          className="cerid-press mt-2"
          onClick={() => void refetch()}
        >
          <RefreshCw className="mr-1.5 h-3 w-3" aria-hidden="true" />
          Retry
        </Button>
      </div>
    )
  }

  if (isLoading || !data) {
    return (
      <div className="liquid-glass rounded-lg px-4 py-3">
        <div className="h-16 animate-pulse rounded-md bg-muted/30" />
      </div>
    )
  }

  return (
    <div className="liquid-glass rounded-lg px-4 py-3">
      <div className="mb-3 flex items-center gap-2">
        <Webhook className="h-4 w-4 text-foreground/80" />
        <span className="text-sm font-medium">Receiver URL</span>
        {data.require_hmac && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-label-xs text-emerald-500">
            <Lock className="h-2.5 w-2.5" />
            HMAC required
          </span>
        )}
      </div>

      <CopyableRow value={data.url} label="URL" />
      <CopyableRow value={data.curl_example} label="curl" className="mt-2" />

      <p className="mt-3 text-label-xs text-muted-foreground">
        Send any JSON payload to this URL — it'll arrive in your knowledge base.
        {data.require_hmac && " Requests must carry an X-Cerid-Signature header."}
      </p>
    </div>
  )
}

function CopyableRow({
  value,
  label,
  className,
}: {
  value: string
  label: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // best-effort
    }
  }

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <code className="flex-1 truncate rounded bg-background/40 px-2 py-1 font-mono text-label-sm text-foreground">
        {value}
      </code>
      <Button
        size="sm"
        variant="ghost"
        onClick={onCopy}
        className="cerid-press h-7 px-2"
        aria-label={`Copy ${label}`}
        title={`Copy ${label}`}
      >
        {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
      </Button>
    </div>
  )
}
