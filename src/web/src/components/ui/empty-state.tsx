// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Card, CardContent } from "@/components/ui/card"
import type { LucideIcon } from "lucide-react"

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
}

export function EmptyState({ icon: Icon, title, description }: EmptyStateProps) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-10 text-center">
        <Icon className="mb-2 h-8 w-8 text-muted-foreground/50" />
        <p className="text-sm font-medium text-muted-foreground">{title}</p>
        {description && (
          <p className="mt-1 text-xs text-muted-foreground/70">{description}</p>
        )}
      </CardContent>
    </Card>
  )
}