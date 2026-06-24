// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SourceConfigForm — inline edit form for source configuration.
 *
 * Shared KindSpecificFields is the canonical UI for per-kind config fields.
 * Used by both the add-wizard (creation) and this inline edit form (update).
 *
 * Edit-mode constraints vs add-mode:
 *   - provider is read-only (immutable after creation)
 *   - path is read-only for folder sources (immutable after creation)
 *   - secrets stored as "***redacted***" are displayed as a placeholder;
 *     if left untouched, the mask is round-tripped so the backend drops
 *     it and preserves the real secret.
 */

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { patchSourceConfig, type SourceRecord } from "@/lib/api/sources"

const REDACTED_MASK = "***redacted***"
const REDACTED_PLACEHOLDER = "•••• (unchanged)"

/**
 * Kinds that have real editable fields in KindSpecificFields (edit mode).
 * Used to gate the Configuration section in the detail pane so non-editable
 * kinds (gmail, bookmarks, clipboard, etc.) do not show an empty placeholder.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const EDITABLE_CONFIG_KINDS = ["folder", "rss", "url_watch", "webhook"] as const

// ---------------------------------------------------------------------------
// KindSpecificFields — canonical, shared between add-wizard and edit form
// ---------------------------------------------------------------------------

export interface KindSpecificFieldsProps {
  kind: string
  /** Available recipe providers (webhook-backed kinds). Empty for others. */
  providers: string[]
  config: Record<string, unknown>
  onConfig: (v: Record<string, unknown>) => void
  /**
   * When true, provider is shown as a read-only label instead of a picker,
   * and folder path is rendered as static text (path is immutable post-create).
   */
  editMode?: boolean
}

export function KindSpecificFields({
  kind,
  providers,
  config,
  onConfig,
  editMode = false,
}: KindSpecificFieldsProps) {
  // In edit mode, provider is always read-only even if providers list is present
  if (providers.length > 0 && !editMode) {
    return (
      <div className="space-y-2">
        <label className="text-xs font-medium text-foreground" htmlFor="provider">
          Provider
        </label>
        <select
          id="provider"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={String(config.provider ?? "")}
          onChange={(e) => onConfig({ ...config, provider: e.target.value })}
        >
          <option value="" disabled>
            Select a provider…
          </option>
          {providers.map((p) => (
            <option key={p} value={p}>
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">
          A unique webhook token is minted automatically. The receiver URL
          appears after the source is created — point {String(config.provider) || "the provider"}
          &apos;s outgoing webhook at it.
        </p>
      </div>
    )
  }

  if (kind === "folder") {
    const rawPath = config.path != null ? String(config.path) : ""
    const rawExclude = Array.isArray(config.exclude_patterns)
      ? (config.exclude_patterns as string[]).join(", ")
      : String(config.exclude_patterns ?? "")

    return (
      <div className="space-y-3">
        {/* Path — read-only always (immutable) */}
        <div>
          <span className="text-xs font-medium text-foreground">Path</span>
          <div className="mt-1 rounded-md border border-border/50 bg-muted/30 px-3 py-1.5 text-sm text-muted-foreground">
            {rawPath || <em className="opacity-60">not set</em>}
          </div>
          {editMode && (
            <p className="mt-0.5 text-label-xs text-muted-foreground">
              Path cannot be changed after creation.
            </p>
          )}
        </div>

        {/* Label */}
        <div>
          <label className="text-xs font-medium text-foreground" htmlFor="folder-label">
            Label
          </label>
          <input
            id="folder-label"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="e.g. Project Notes"
            value={String(config.label ?? "")}
            onChange={(e) => onConfig({ ...config, label: e.target.value })}
          />
        </div>

        {/* Exclude patterns */}
        <div>
          <label className="text-xs font-medium text-foreground" htmlFor="folder-exclude">
            Exclude patterns
          </label>
          <input
            id="folder-exclude"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="*.tmp, .git/, node_modules/"
            value={rawExclude}
            onChange={(e) => {
              const patterns = e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean)
              onConfig({ ...config, exclude_patterns: patterns })
            }}
          />
          <p className="mt-0.5 text-label-xs text-muted-foreground">
            Comma-separated glob patterns to skip.
          </p>
        </div>

        {/* Optional toggles */}
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={Boolean(config.search_enabled ?? true)}
            onChange={(e) => onConfig({ ...config, search_enabled: e.target.checked })}
          />
          Enable in search
        </label>

        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={Boolean(config.is_vault)}
            onChange={(e) => onConfig({ ...config, is_vault: e.target.checked })}
          />
          Mark as vault (private)
        </label>

        {/* Domain override */}
        <div>
          <label className="text-xs font-medium text-foreground" htmlFor="folder-domain">
            Domain override
          </label>
          <input
            id="folder-domain"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="e.g. work, personal"
            value={String(config.domain_override ?? "")}
            onChange={(e) => onConfig({ ...config, domain_override: e.target.value || undefined })}
          />
        </div>
      </div>
    )
  }

  if (kind === "rss" || kind === "url_watch") {
    return (
      <div>
        <label className="text-xs font-medium text-foreground" htmlFor="url">
          Feed URL
        </label>
        <input
          id="url"
          type="url"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="https://example.com/feed.xml"
          value={String(config.url ?? "")}
          onChange={(e) => onConfig({ ...config, url: e.target.value })}
        />
      </div>
    )
  }

  if (kind === "webhook") {
    const hmacSecret = config.hmac_secret != null ? String(config.hmac_secret) : ""
    const isRedacted = hmacSecret === REDACTED_MASK

    return (
      <div className="space-y-2">
        {editMode ? (
          <p className="text-xs text-muted-foreground">
            Webhook token is immutable. To rotate it, disconnect and reconnect the source.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            A unique token is minted automatically. The receiver URL appears
            after the source is created.
          </p>
        )}
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={Boolean(config.require_hmac)}
            onChange={(e) =>
              onConfig({ ...config, require_hmac: e.target.checked })
            }
          />
          Require HMAC signature on inbound requests
        </label>
        {(editMode || hmacSecret) && (
          <div>
            <label className="text-xs font-medium text-foreground" htmlFor="hmac-secret">
              HMAC secret
            </label>
            <input
              id="hmac-secret"
              type="password"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder={isRedacted ? REDACTED_PLACEHOLDER : ""}
              value={isRedacted ? "" : hmacSecret}
              onChange={(e) => {
                // When the user types, replace the mask with the new value
                onConfig({ ...config, hmac_secret: e.target.value || REDACTED_MASK })
              }}
            />
            {isRedacted && (
              <p className="mt-0.5 text-label-xs text-muted-foreground">
                Leave blank to keep the existing secret.
              </p>
            )}
          </div>
        )}
      </div>
    )
  }

  // Provider read-only display (edit mode for webhook-backed kinds)
  if (editMode && config.provider) {
    return (
      <div className="space-y-2">
        <span className="text-xs font-medium text-foreground">Provider</span>
        <div className="mt-1 rounded-md border border-border/50 bg-muted/30 px-3 py-1.5 text-sm text-muted-foreground capitalize">
          {String(config.provider)}
        </div>
        <p className="text-label-xs text-muted-foreground">
          Provider cannot be changed after creation.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-md border border-dashed border-border/60 px-3 py-3 text-xs text-muted-foreground">
      Configuration for this source kind ships in a follow-up phase. The
      source will be created with default settings.
    </div>
  )
}

// ---------------------------------------------------------------------------
// SourceConfigForm — inline edit form (for use in detail pane)
// ---------------------------------------------------------------------------

interface SourceConfigFormProps {
  source: SourceRecord
  onSaved: () => void
}

export function SourceConfigForm({ source, onSaved }: SourceConfigFormProps) {
  const [config, setConfig] = useState<Record<string, unknown>>({ ...source.config })
  const queryClient = useQueryClient()

  const saveMut = useMutation({
    mutationFn: () => {
      // Only send fields that changed from the seeded source.config.
      // Untouched redacted secrets stay equal to the seed value ("***redacted***")
      // and are correctly excluded — the backend preserves the stored secret.
      const seed = source.config as Record<string, unknown>
      const diff = Object.fromEntries(
        Object.entries(config).filter(([k, v]) => v !== seed[k]),
      )
      return patchSourceConfig(source.id, diff)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingestion-sources"] })
      onSaved()
    },
  })

  return (
    <div className="space-y-4">
      <KindSpecificFields
        kind={source.kind}
        providers={[]}
        config={config}
        onConfig={setConfig}
        editMode
      />

      {saveMut.error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {saveMut.error.message}
        </div>
      )}

      <div className="flex justify-end pt-1">
        <Button
          size="sm"
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending}
          className="cerid-press"
        >
          {saveMut.isPending ? (
            <>
              <Loader2 className="mr-2 h-3 w-3 animate-spin" aria-hidden="true" />
              Saving…
            </>
          ) : (
            "Save"
          )}
        </Button>
      </div>
    </div>
  )
}
