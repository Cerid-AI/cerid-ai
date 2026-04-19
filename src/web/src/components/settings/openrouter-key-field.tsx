// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  fetchOpenRouterKeyStatus,
  putOpenRouterKey,
  testOpenRouterKey,
} from "@/lib/api"

/**
 * Write-only OpenRouter key field. The actual key value never round-trips
 * through the client — this component only displays the last-4 digits and
 * "last updated" timestamp, and lets the user replace the key via
 * type=password input that is cleared after a successful PUT.
 */
export function OpenRouterKeyField() {
  const [draft, setDraft] = useState("")
  const qc = useQueryClient()

  const statusQuery = useQuery({
    queryKey: ["openrouter-key-status"],
    queryFn: fetchOpenRouterKeyStatus,
  })

  const putMutation = useMutation({
    mutationKey: ["put-openrouter-key"],
    mutationFn: putOpenRouterKey,
    onSuccess: () => {
      setDraft("") // Clear the draft — key is never re-shown
      toast.success("OpenRouter key saved")
      qc.invalidateQueries({ queryKey: ["openrouter-key-status"] })
    },
  })

  const testMutation = useMutation({
    mutationKey: ["test-openrouter-key"],
    mutationFn: (key?: string) => testOpenRouterKey(key),
    onSuccess: (data) => {
      if (data.valid) {
        const credits = data.credits_remaining
        toast.success(
          credits != null
            ? `Key valid — $${credits.toFixed(2)} credits remaining`
            : "Key valid",
        )
      } else {
        toast.error(data.error ?? "Key validation failed")
      }
    },
  })

  const status = statusQuery.data
  const configured = status?.configured ?? false
  const last4 = status?.last4
  const updated = status?.updated_at

  return (
    <div className="space-y-3">
      <Label htmlFor="openrouter-key" className="text-sm font-medium">
        OpenRouter API Key
      </Label>

      {configured ? (
        <p className="text-xs text-muted-foreground">
          Configured — ending in <code className="font-mono">{last4}</code>
          {updated && <> &middot; updated {new Date(updated).toLocaleString()}</>}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">Not configured</p>
      )}

      <div className="flex gap-2">
        <Input
          id="openrouter-key"
          type="password"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={configured ? "Enter new key to replace" : "sk-or-..."}
          autoComplete="off"
          spellCheck={false}
          aria-label="OpenRouter API key (write-only)"
          className="flex-1 font-mono"
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={() => testMutation.mutate(draft || undefined)}
          disabled={!draft && !configured}
        >
          Test
        </Button>
        <Button
          size="sm"
          onClick={() => putMutation.mutate(draft)}
          disabled={draft.length < 8 || putMutation.isPending}
        >
          {putMutation.isPending ? "Saving..." : "Save"}
        </Button>
      </div>

      <p className="text-[10px] text-muted-foreground">
        The key is stored on your machine. It is never sent to Cerid or returned
        in any API response after save.
      </p>
    </div>
  )
}
