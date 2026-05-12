// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vault configuration UI (Workstream RAG Cycle C2.3).
 *
 * Inline panel for a watched folder: lets the user flip it into "vault"
 * mode and tune the per-subfolder semantics (MOCs, daily notes, templates,
 * attachments, skip).  These UI values are the FALLBACK — a
 * .cerid-vault.yaml file at the vault root overrides them.  When a vault
 * is active we read the effective profile from the backend
 * (/watched-folders/{id}/vault-profile) and surface which source is
 * currently winning.
 */

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Switch } from "@/components/ui/switch"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Info, FileText } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { fetchVaultProfile, updateWatchedFolder } from "@/lib/api/settings"
import type { VaultConfig, WatchedFolder } from "@/lib/api/settings"
import { logSwallowedError } from "@/lib/log-swallowed"

interface VaultConfigSectionProps {
  folder: WatchedFolder
  onChanged: () => void | Promise<void>
}

interface FormState {
  mocsFolder: string
  dailyFolder: string
  templatesFolder: string
  attachmentsFolder: string
  skipFolders: string
}

const DEFAULT_FORM: FormState = {
  mocsFolder: "mocs",
  dailyFolder: "daily",
  templatesFolder: "templates",
  attachmentsFolder: "attachments",
  skipFolders: ".obsidian, .trash, .git",
}

function configToForm(cfg: VaultConfig | null | undefined): FormState {
  if (!cfg) return { ...DEFAULT_FORM }
  return {
    mocsFolder: cfg.mocs_folders?.[0] ?? DEFAULT_FORM.mocsFolder,
    dailyFolder: cfg.daily_folders?.[0] ?? DEFAULT_FORM.dailyFolder,
    templatesFolder: cfg.templates_folders?.[0] ?? DEFAULT_FORM.templatesFolder,
    attachmentsFolder: cfg.attachments_folders?.[0] ?? DEFAULT_FORM.attachmentsFolder,
    skipFolders: (cfg.skip_folders ?? []).join(", ") || DEFAULT_FORM.skipFolders,
  }
}

function formToConfig(form: FormState): VaultConfig {
  return {
    mocs_folders: form.mocsFolder.trim() ? [form.mocsFolder.trim()] : [],
    daily_folders: form.dailyFolder.trim() ? [form.dailyFolder.trim()] : [],
    templates_folders: form.templatesFolder.trim() ? [form.templatesFolder.trim()] : [],
    attachments_folders: form.attachmentsFolder.trim() ? [form.attachmentsFolder.trim()] : [],
    skip_folders: form.skipFolders
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  }
}

export function VaultConfigSection({ folder, onChanged }: VaultConfigSectionProps) {
  const [form, setForm] = useState<FormState>(() => configToForm(folder.vault_config))
  const [savedConfig, setSavedConfig] = useState<VaultConfig | null | undefined>(folder.vault_config)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isVault = Boolean(folder.is_vault)

  // Re-sync the form when the parent re-fetches a folder (e.g. after
  // toggle).  Tracking ``savedConfig`` as state and comparing reference
  // identity lets us reset the form during render — React's idiomatic
  // alternative to ``useEffect(setState, [prop])`` (which triggers a
  // cascading render and a lint warning).
  if (folder.vault_config !== savedConfig) {
    setSavedConfig(folder.vault_config)
    setForm(configToForm(folder.vault_config))
  }

  const profileQuery = useQuery({
    queryKey: ["watched-folder-vault-profile", folder.id, isVault],
    queryFn: () => fetchVaultProfile(folder.id),
    enabled: isVault,
    staleTime: 15_000,
  })

  const handleToggleVault = async (checked: boolean) => {
    setSaving(true)
    setError(null)
    try {
      await updateWatchedFolder(folder.id, { is_vault: checked })
      await onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update vault setting")
      logSwallowedError(e, "settings.vault_config.toggle")
    } finally {
      setSaving(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      await updateWatchedFolder(folder.id, { vault_config: formToConfig(form) })
      await onChanged()
      await profileQuery.refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save vault config")
      logSwallowedError(e, "settings.vault_config.save")
    } finally {
      setSaving(false)
    }
  }

  const effective = profileQuery.data

  return (
    <div className="rounded-md border border-dashed border-muted-foreground/20 p-2 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Switch
            aria-label={`Vault mode — ${folder.label}`}
            checked={isVault}
            onCheckedChange={handleToggleVault}
            disabled={saving}
            className="scale-[0.6]"
          />
          <span className="text-label-xs font-medium">This folder is a vault</span>
          <Tooltip>
            <TooltipTrigger asChild>
              <Info className="h-3 w-3 text-muted-foreground/70" aria-label="Vault configuration info" />
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[260px] text-xs">
              These fields can be overridden by a <code>.cerid-vault.yaml</code> file at the vault root. YAML wins on key conflicts.
            </TooltipContent>
          </Tooltip>
        </div>
        {isVault && effective?.yaml_present && (
          <Badge variant="secondary" className="text-label-xxs flex items-center gap-1">
            <FileText className="h-2.5 w-2.5" />
            .cerid-vault.yaml
          </Badge>
        )}
      </div>

      {isVault && (
        <>
          <div className="grid grid-cols-2 gap-1.5">
            <label className="text-label-xs text-muted-foreground space-y-0.5">
              <span>MOCs folder</span>
              <input
                aria-label="MOCs folder name"
                className="w-full rounded border bg-background px-1.5 py-1 text-xs font-mono"
                placeholder={DEFAULT_FORM.mocsFolder}
                value={form.mocsFolder}
                onChange={(e) => setForm((f) => ({ ...f, mocsFolder: e.target.value }))}
              />
            </label>
            <label className="text-label-xs text-muted-foreground space-y-0.5">
              <span>Daily notes folder</span>
              <input
                aria-label="Daily notes folder name"
                className="w-full rounded border bg-background px-1.5 py-1 text-xs font-mono"
                placeholder={DEFAULT_FORM.dailyFolder}
                value={form.dailyFolder}
                onChange={(e) => setForm((f) => ({ ...f, dailyFolder: e.target.value }))}
              />
            </label>
            <label className="text-label-xs text-muted-foreground space-y-0.5">
              <span>Templates folder</span>
              <input
                aria-label="Templates folder name"
                className="w-full rounded border bg-background px-1.5 py-1 text-xs font-mono"
                placeholder={DEFAULT_FORM.templatesFolder}
                value={form.templatesFolder}
                onChange={(e) => setForm((f) => ({ ...f, templatesFolder: e.target.value }))}
              />
            </label>
            <label className="text-label-xs text-muted-foreground space-y-0.5">
              <span>Attachments folder</span>
              <input
                aria-label="Attachments folder name"
                className="w-full rounded border bg-background px-1.5 py-1 text-xs font-mono"
                placeholder={DEFAULT_FORM.attachmentsFolder}
                value={form.attachmentsFolder}
                onChange={(e) => setForm((f) => ({ ...f, attachmentsFolder: e.target.value }))}
              />
            </label>
          </div>
          <label className="text-label-xs text-muted-foreground space-y-0.5 block">
            <span>Skip folders (comma-separated)</span>
            <input
              aria-label="Skip folders"
              className="w-full rounded border bg-background px-1.5 py-1 text-xs font-mono"
              placeholder={DEFAULT_FORM.skipFolders}
              value={form.skipFolders}
              onChange={(e) => setForm((f) => ({ ...f, skipFolders: e.target.value }))}
            />
          </label>

          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" className="h-6 text-xs px-2" onClick={handleSave} disabled={saving}>
              Save vault config
            </Button>
            {error && <span className="text-label-xs text-destructive">{error}</span>}
          </div>

          {effective && (
            <div className="rounded bg-muted/40 p-1.5 space-y-0.5 text-label-xs text-muted-foreground">
              <p className="font-medium text-foreground/80">
                Effective profile{effective.yaml_present ? " (from .cerid-vault.yaml)" : " (from form)"}
              </p>
              <p>
                <span className="font-mono">mocs</span>: {effective.profile.mocs_folders.join(", ") || "—"}
              </p>
              <p>
                <span className="font-mono">daily</span>: {effective.profile.daily_folders.join(", ") || "—"}
              </p>
              <p>
                <span className="font-mono">templates</span>: {effective.profile.templates_folders.join(", ") || "—"}
              </p>
              <p>
                <span className="font-mono">attachments</span>: {effective.profile.attachments_folders.join(", ") || "—"}
              </p>
              <p>
                <span className="font-mono">skip</span>: {effective.profile.skip_folders.join(", ") || "—"}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
