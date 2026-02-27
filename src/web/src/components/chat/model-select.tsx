// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { MODELS } from "@/lib/types"

interface ModelSelectProps {
  value: string
  onChange: (model: string) => void
}

export function ModelSelect({ value, onChange }: ModelSelectProps) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-48">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {MODELS.map((m) => (
          <SelectItem key={m.id} value={m.id}>
            <span>{m.label}</span>
            <span className="ml-2 text-xs text-muted-foreground">{m.provider}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}