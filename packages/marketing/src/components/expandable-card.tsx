"use client"

import { useState } from "react"
import { ChevronDown, Layers, ShieldCheck, Bot, Brain, Lock, FolderOpen } from "lucide-react"
import { Card, CardHeader, CardTitle } from "@/components/ui/card"

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  Layers, ShieldCheck, Bot, Brain, Lock, FolderOpen,
}

export function ExpandableCard({
  iconName,
  title,
  summary,
  detail,
  link,
  image,
}: {
  iconName: string
  title: string
  summary: string
  detail?: string
  link?: { href: string; label: string }
  image?: string
}) {
  const [open, setOpen] = useState(false)
  const Icon = ICON_MAP[iconName] ?? Layers

  return (
    <Card
      className="group cursor-pointer border-border bg-card transition-all hover:border-brand/30 hover:shadow-lg hover:shadow-brand/5"
      onClick={() => setOpen((v) => !v)}
    >
      <CardHeader className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand/10 text-brand transition-colors group-hover:bg-brand/20">
              <Icon className="h-5 w-5" />
            </div>
            <CardTitle className="text-base">{title}</CardTitle>
          </div>
          <ChevronDown
            className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          />
        </div>
        <p className="text-sm leading-relaxed text-muted-foreground">{summary}</p>

        {/* Expandable detail section */}
        <div
          className={`grid transition-all duration-300 ease-in-out ${open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}
        >
          <div className="overflow-hidden">
            {image && (
              <img
                src={image}
                alt={title}
                className="mt-2 w-full rounded-lg border border-border/30"
                loading="lazy"
              />
            )}
            {detail && (
              <p className="pt-2 text-xs leading-relaxed text-muted-foreground/80 border-t border-border/50 mt-1">
                {detail}
              </p>
            )}
            {link && (
              <a
                href={link.href}
                className="mt-2 inline-flex items-center text-xs font-medium text-brand hover:text-brand/80 transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                {link.label} &rarr;
              </a>
            )}
          </div>
        </div>
      </CardHeader>
    </Card>
  )
}
