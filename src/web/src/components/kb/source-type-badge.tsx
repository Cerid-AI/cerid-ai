// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Upload,
  Eye,
  Link,
  Mail,
  Rss,
  Bookmark,
  ClipboardPaste,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { ArtifactSourceType } from "@/lib/types"

const SOURCE_CONFIG: Record<
  ArtifactSourceType,
  { icon: typeof Upload; label: string; color: string }
> = {
  upload: {
    icon: Upload,
    label: "Upload",
    color: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
  },
  watcher: {
    icon: Eye,
    label: "Watcher",
    color: "bg-purple-500/15 text-purple-600 dark:text-purple-400 border-purple-500/30",
  },
  webhook: {
    icon: Link,
    label: "Webhook",
    color: "bg-green-500/15 text-green-600 dark:text-green-400 border-green-500/30",
  },
  email: {
    icon: Mail,
    label: "Email",
    color: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  },
  rss: {
    icon: Rss,
    label: "RSS",
    color: "bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/30",
  },
  bookmark: {
    icon: Bookmark,
    label: "Bookmark",
    color: "bg-teal-500/15 text-teal-600 dark:text-teal-400 border-teal-500/30",
  },
  clipboard: {
    icon: ClipboardPaste,
    label: "Clipboard",
    color: "bg-slate-500/15 text-slate-600 dark:text-slate-400 border-slate-500/30",
  },
}

interface SourceTypeBadgeProps {
  sourceType?: ArtifactSourceType | string
  className?: string
}

export function SourceTypeBadge({ sourceType, className }: SourceTypeBadgeProps) {
  const key = (sourceType ?? "upload") as ArtifactSourceType
  const config = SOURCE_CONFIG[key] ?? SOURCE_CONFIG.upload
  const Icon = config.icon

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            className={cn(
              "gap-0.5 px-1.5 py-0 text-[9px] font-medium",
              config.color,
              className,
            )}
          >
            <Icon className="h-2.5 w-2.5" />
            {config.label}
          </Badge>
        </TooltipTrigger>
        <TooltipContent side="top">Source: {config.label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
