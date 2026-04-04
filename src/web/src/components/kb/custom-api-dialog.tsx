// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Loader2, Check, X } from "lucide-react"

interface CustomApiConfig {
  name: string
  baseUrl: string
  authType: "bearer" | "custom_header" | "query_param"
  authKey: string
  authValue: string
  responsePath: string
  titleField: string
  contentField: string
}

interface CustomApiDialogProps {
  open: boolean
  onClose: () => void
  onSave: (config: CustomApiConfig) => Promise<void>
}

export function CustomApiDialog({ open, onClose, onSave }: CustomApiDialogProps) {
  const [config, setConfig] = useState<CustomApiConfig>({
    name: "",
    baseUrl: "",
    authType: "bearer",
    authKey: "Authorization",
    authValue: "",
    responsePath: "data",
    titleField: "title",
    contentField: "content",
  })
  const [testing, setTesting] = useState(false)
  const [testStatus, setTestStatus] = useState<"idle" | "pass" | "fail">("idle")
  const [testError, setTestError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const patch = useCallback((updates: Partial<CustomApiConfig>) => {
    setConfig((prev) => ({ ...prev, ...updates }))
    setTestStatus("idle")
  }, [])

  const handleTest = useCallback(async () => {
    setTesting(true)
    setTestError(null)
    try {
      const headers: Record<string, string> = { Accept: "application/json" }
      if (config.authType === "bearer") {
        headers.Authorization = `Bearer ${config.authValue}`
      } else if (config.authType === "custom_header") {
        headers[config.authKey] = config.authValue
      }
      const url = new URL(config.baseUrl)
      if (config.authType === "query_param") {
        url.searchParams.set(config.authKey, config.authValue)
      }
      const res = await fetch(url.toString(), {
        headers,
        signal: AbortSignal.timeout(5000),
      })
      if (res.ok) {
        setTestStatus("pass")
      } else {
        setTestStatus("fail")
        setTestError(`Server returned ${res.status}`)
      }
    } catch {
      setTestStatus("fail")
      setTestError("Connection failed — check URL and credentials")
    } finally {
      setTesting(false)
    }
  }, [config])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      await onSave(config)
      onClose()
    } finally {
      setSaving(false)
    }
  }, [config, onSave, onClose])

  const canSave = config.name.trim() && config.baseUrl.trim() && config.authValue.trim()

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Add Custom API Source</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-xs">Source Name</Label>
            <Input
              value={config.name}
              onChange={(e) => patch({ name: e.target.value })}
              placeholder="My API"
              className="h-8 text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Base URL</Label>
            <Input
              value={config.baseUrl}
              onChange={(e) => patch({ baseUrl: e.target.value })}
              placeholder="https://api.example.com/v1"
              className="h-8 font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Auth Type</Label>
              <Select value={config.authType} onValueChange={(v) => patch({ authType: v as CustomApiConfig["authType"] })}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="bearer">Bearer Token</SelectItem>
                  <SelectItem value="custom_header">Custom Header</SelectItem>
                  <SelectItem value="query_param">Query Parameter</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {config.authType !== "bearer" && (
              <div className="space-y-1.5">
                <Label className="text-xs">
                  {config.authType === "custom_header" ? "Header Name" : "Param Name"}
                </Label>
                <Input
                  value={config.authKey}
                  onChange={(e) => patch({ authKey: e.target.value })}
                  placeholder={config.authType === "custom_header" ? "X-API-Key" : "api_key"}
                  className="h-8 font-mono text-xs"
                />
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">API Key / Token</Label>
            <Input
              type="password"
              value={config.authValue}
              onChange={(e) => patch({ authValue: e.target.value })}
              placeholder="sk-..."
              className="h-8 font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="space-y-1.5">
              <Label className="text-[10px] text-muted-foreground">Response Path</Label>
              <Input
                value={config.responsePath}
                onChange={(e) => patch({ responsePath: e.target.value })}
                placeholder="data.results"
                className="h-7 font-mono text-[10px]"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-[10px] text-muted-foreground">Title Field</Label>
              <Input
                value={config.titleField}
                onChange={(e) => patch({ titleField: e.target.value })}
                placeholder="title"
                className="h-7 font-mono text-[10px]"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-[10px] text-muted-foreground">Content Field</Label>
              <Input
                value={config.contentField}
                onChange={(e) => patch({ contentField: e.target.value })}
                placeholder="content"
                className="h-7 font-mono text-[10px]"
              />
            </div>
          </div>

          {testError && <p className="text-[10px] text-destructive">{testError}</p>}

          <div className="flex justify-between">
            <Button variant="outline" size="sm" onClick={handleTest} disabled={!config.baseUrl.trim() || testing}>
              {testing ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : testStatus === "pass" ? <Check className="mr-1 h-3 w-3 text-green-500" /> : testStatus === "fail" ? <X className="mr-1 h-3 w-3 text-destructive" /> : null}
              Test Connection
            </Button>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
              <Button size="sm" onClick={handleSave} disabled={!canSave || saving}>
                {saving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                Add Source
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
