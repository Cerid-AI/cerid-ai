// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Brief settings section (RAG C3.4) — operator-level toggle for whether
 * the daily-brief / weekly-synthesis cron jobs write their generated
 * markdown back to a registered vault.
 *
 * Three controls:
 *   1. Switch: "Write daily / weekly briefs to vault"
 *   2. Select: target vault (populated from /watched-folders, filtered
 *      to is_vault=true). Only enabled when the switch is on.
 *   3. Input: folder prefix under the vault root (default "_briefs").
 *
 * Persists to PUT /briefs/settings.  The scheduler reads this Redis
 * doc on every cron firing, so changes take effect on the next
 * scheduled run without a process restart.
 */

import { useCallback, useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { CalendarClock } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  fetchBriefSettings,
  updateBriefSettings,
  type BriefSettings,
} from "@/lib/api/settings"
import { fetchVaultsList } from "@/lib/api/wiki"

const DEFAULT_VAULT_FOLDER = "_briefs"

export function BriefSettingsSection() {
  const qc = useQueryClient()
  const { data: settings, isLoading } = useQuery<BriefSettings>({
    queryKey: ["briefs", "settings"],
    queryFn: fetchBriefSettings,
    staleTime: 60_000,
  })
  const { data: vaults } = useQuery({
    queryKey: ["save-to-vault", "vaults-list"],
    queryFn: fetchVaultsList,
    staleTime: 60_000,
  })

  const [writeToVault, setWriteToVault] = useState(false)
  const [vaultId, setVaultId] = useState<string>("")
  const [vaultFolder, setVaultFolder] = useState<string>(DEFAULT_VAULT_FOLDER)

  // Hydrate local state from server state once it arrives. Subsequent
  // edits stay local until the user clicks Save — that's the model the
  // rest of the settings pane uses.
  useEffect(() => {
    if (!settings) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional setState driven by external state (streaming / fetch / subscription); behavior validated in tests
    setWriteToVault(Boolean(settings.write_to_vault))
    setVaultId(settings.vault_id ?? "")
    setVaultFolder(settings.vault_folder || DEFAULT_VAULT_FOLDER)
  }, [settings])

  const mutation = useMutation({
    mutationFn: updateBriefSettings,
    onSuccess: (saved) => {
      qc.setQueryData(["briefs", "settings"], saved)
      toast.success("Brief settings saved")
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : "Failed to save brief settings"
      toast.error(msg)
    },
  })

  const handleSave = useCallback(() => {
    mutation.mutate({
      write_to_vault: writeToVault,
      vault_id: writeToVault ? (vaultId || null) : null,
      vault_folder: (vaultFolder.trim() || DEFAULT_VAULT_FOLDER),
    })
  }, [mutation, writeToVault, vaultId, vaultFolder])

  return (
    <Card className="mb-4">
      <CardHeader className="px-4 pb-2 pt-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <CalendarClock className="h-3.5 w-3.5 text-muted-foreground" />
          Daily &amp; weekly brief vault writeback
        </CardTitle>
        <CardDescription className="text-xs">
          When enabled, the daily brief and weekly synthesis cron jobs
          write their generated markdown to the selected vault. Briefs
          are excluded from future synthesis input by default.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 px-4 pb-4">
        <div className="flex items-center justify-between">
          <Label htmlFor="brief-write-to-vault" className="text-sm">
            Write briefs to vault
          </Label>
          <Switch
            id="brief-write-to-vault"
            checked={writeToVault}
            onCheckedChange={setWriteToVault}
            disabled={isLoading || mutation.isPending}
            aria-label="Write briefs to vault"
          />
        </div>

        {writeToVault && (
          <>
            <div className="grid gap-1.5">
              <Label htmlFor="brief-vault-id" className="text-xs">
                Target vault
              </Label>
              <Select
                value={vaultId}
                onValueChange={setVaultId}
                disabled={mutation.isPending}
              >
                <SelectTrigger id="brief-vault-id" aria-label="Target vault">
                  <SelectValue placeholder="Pick a vault" />
                </SelectTrigger>
                <SelectContent>
                  {(vaults ?? []).map((v) => (
                    <SelectItem key={v.id} value={v.id}>
                      {v.label || v.path}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {(!vaults || vaults.length === 0) && (
                <p className="text-xs text-muted-foreground">
                  No vaults registered. Add one in Watched folders with{" "}
                  <code className="rounded bg-muted px-1 py-0.5">is_vault=true</code>.
                </p>
              )}
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="brief-vault-folder" className="text-xs">
                Folder prefix
              </Label>
              <Input
                id="brief-vault-folder"
                value={vaultFolder}
                onChange={(e) => setVaultFolder(e.target.value)}
                disabled={mutation.isPending}
                placeholder={DEFAULT_VAULT_FOLDER}
              />
              <p className="text-xs text-muted-foreground">
                Path under the vault root. Defaults to{" "}
                <code className="rounded bg-muted px-1 py-0.5">{DEFAULT_VAULT_FOLDER}</code>.
              </p>
            </div>
          </>
        )}

        <div className="flex justify-end">
          <Button
            size="sm"
            onClick={handleSave}
            disabled={mutation.isPending || isLoading || (writeToVault && !vaultId)}
          >
            {mutation.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
