// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { HardDrive, AlertTriangle, FolderOpen } from "lucide-react"

interface KBConfigState {
  archivePath: string
  domains: string[]
  lightweightMode: boolean
  watchFolder: boolean
}

interface KBConfigStepProps {
  config: KBConfigState
  onChange: (config: KBConfigState) => void
  lightweightRecommended: boolean
  ramGb: number
}

/** Browse button using the File System Access API (directory picker). */
function BrowseButton({ onSelect }: { onSelect: (name: string) => void }) {
  const handleBrowse = useCallback(async () => {
    try {
      // @ts-expect-error — showDirectoryPicker is not in all TS lib definitions
      const dirHandle = await window.showDirectoryPicker({ mode: "read" })
      onSelect(dirHandle.name)
    } catch {
      // User cancelled or API not supported — ignore
    }
  }, [onSelect])

  // Only show if File System Access API is available (Chromium browsers)
  if (typeof window === "undefined" || !("showDirectoryPicker" in window)) return null

  return (
    <Button variant="outline" size="sm" className="shrink-0 gap-1.5" onClick={handleBrowse}>
      <FolderOpen className="h-3.5 w-3.5" />
      Browse
    </Button>
  )
}

export function KBConfigStep({ config, onChange, lightweightRecommended, ramGb }: KBConfigStepProps) {
  return (
    <>
      <div className="mb-2 flex items-center justify-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-brand/10">
          <HardDrive className="h-5 w-5 text-brand" />
        </div>
      </div>
      <h3 className="mb-4 text-center text-lg font-semibold">Storage & Archive</h3>

      <div className="space-y-5">
        {/* Archive Path */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">Archive Folder</Label>
          <div className="flex gap-2">
            <Input
              value={config.archivePath}
              onChange={(e) => onChange({ ...config, archivePath: e.target.value })}
              placeholder="~/cerid-archive"
              className="font-mono text-xs flex-1"
            />
            <BrowseButton onSelect={(name) => onChange({ ...config, archivePath: name })} />
          </div>
          <p className="text-[11px] text-muted-foreground">
            Where Cerid stores and watches for your documents. Domain subfolders are created automatically.
          </p>
          <p className="text-[10px] text-muted-foreground/80">
            Tip: place this inside Dropbox or iCloud Drive to sync your knowledge base across machines.
          </p>
        </div>

        {/* Lightweight Mode (conditional) */}
        {lightweightRecommended && (
          <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/5 p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600 dark:text-yellow-400" />
              <div className="flex-1">
                <p className="text-xs font-medium text-yellow-600 dark:text-yellow-400">
                  {ramGb.toFixed(0)} GB RAM detected
                </p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  Lightweight mode disables Neo4j graph features for better performance on your system.
                </p>
                <div className="mt-2 flex items-center justify-between">
                  <Label className="text-xs">Enable lightweight mode</Label>
                  <Switch
                    checked={config.lightweightMode}
                    onCheckedChange={(checked) => onChange({ ...config, lightweightMode: checked })}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Watch Folder — opt-in. Default OFF so a fresh install never
            silently scoops up files the user didn't expect. They pick
            the folder above, then explicitly turn this on if they want
            new files auto-ingested. */}
        <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2.5">
          <div>
            <p className="text-xs font-medium">Auto-ingest new files</p>
            <p className="text-[10px] text-muted-foreground">
              Off by default. When on, files added to the archive folder
              are ingested automatically. You can change this any time in
              Settings.
            </p>
          </div>
          <Switch
            checked={config.watchFolder}
            onCheckedChange={(checked) => onChange({ ...config, watchFolder: checked })}
          />
        </div>
      </div>
    </>
  )
}
