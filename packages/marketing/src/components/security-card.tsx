"use client"

import { useState } from "react"
import {
  Lock, Key, Shield, Eye, Server, Database, HardDrive, Wifi, ShieldCheck,
} from "lucide-react"
import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  Lock, Key, Shield, Eye, Server, Database, HardDrive, Wifi, ShieldCheck,
}

export function SecurityCard({
  iconName,
  title,
  description,
  detail,
}: {
  iconName: string
  title: string
  description: string
  detail: string
}) {
  const [open, setOpen] = useState(false)
  const Icon = ICON_MAP[iconName] ?? Shield

  return (
    <div
      className={`transition-all duration-500 ease-in-out cursor-pointer ${
        open ? "sm:col-span-2 lg:col-span-3" : ""
      }`}
      onClick={() => setOpen((v) => !v)}
    >
      <Card className={`h-full border-border bg-card transition-all hover:border-brand/30 hover:shadow-md hover:shadow-brand/5 ${open ? "border-brand/30" : ""}`}>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors ${open ? "bg-brand/20 text-brand" : "bg-brand/10 text-brand"}`}>
              <Icon className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <CardTitle className="text-base">{title}</CardTitle>
              <CardDescription className="mt-1">{description}</CardDescription>
            </div>
          </div>
          {/* Expanded detail */}
          <div className={`grid transition-all duration-400 ease-in-out ${open ? "grid-rows-[1fr] opacity-100 mt-4" : "grid-rows-[0fr] opacity-0"}`}>
            <div className="overflow-hidden">
              <div className="border-t border-border/40 pt-4">
                <p className="text-sm leading-relaxed text-muted-foreground">{detail}</p>
              </div>
            </div>
          </div>
        </CardHeader>
      </Card>
    </div>
  )
}
