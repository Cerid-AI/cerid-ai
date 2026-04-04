// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { HardDrive, AlertTriangle } from "lucide-react"

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
          <Input
            value={config.archivePath}
            onChange={(e) => onChange({ ...config, archivePath: e.target.value })}
            placeholder="~/cerid-archive"
            className="font-mono text-xs"
          />
          <p className="text-[11px] text-muted-foreground">
            Where Cerid stores and watches for your documents. Domain subfolders are created automatically.
          </p>
          <p className="text-[10px] text-muted-foreground/70">
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

        {/* Watch Folder */}
        <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2.5">
          <div>
            <p className="text-xs font-medium">Watch for new files</p>
            <p className="text-[10px] text-muted-foreground">
              Auto-ingest files dropped into your archive folder
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
