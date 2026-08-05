// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Static map of the 12 canonical taxonomy domain lucide icon names
// (kebab-case as stored in taxonomy.py) → imported lucide-react components.
//
// Static imports only — no DynamicIcon, no lazy loading.
// The 800KB CI bundle cap rules out dynamic icon loading.
// Runtime-minted domains (e.g. "research") that have no entry fall back to File.

import type { LucideIcon } from "lucide-react"
import {
  CalendarDays,
  Code,
  DollarSign,
  File,
  Folder,
  Inbox,
  Mail,
  MessageCircle,
  MessageSquare,
  Mic,
  StickyNote,
  User,
} from "lucide-react"

// Map of lucide kebab-name → imported component for the 12 built-in domains.
// Icon values sourced from config/taxonomy.py icon fields.
const DOMAIN_ICON_MAP: Record<string, LucideIcon> = {
  "code":           Code,          // coding
  "dollar-sign":    DollarSign,    // finance
  "folder":         Folder,        // projects
  "user":           User,          // personal
  "file":           File,          // general
  "message-circle": MessageCircle, // conversations
  "sticky-note":    StickyNote,    // notes
  "mail":           Mail,          // mail
  "message-square": MessageSquare, // messages
  "mic":            Mic,           // meetings
  "inbox":          Inbox,         // inbox
  "calendar-days":  CalendarDays,  // digests
}

/**
 * Returns the lucide-react component for a taxonomy domain icon kebab-name.
 * Falls back to `File` for unknown icons (runtime-minted domains with icon:null,
 * or any icon name not in the built-in set).
 */
export function domainIcon(iconName: string | null | undefined): LucideIcon {
  if (!iconName) return File
  return DOMAIN_ICON_MAP[iconName] ?? File
}

export { File as DomainIconFallback }

/**
 * Converts a snake_case or kebab-case domain name to Title Case for display.
 * "canary_client_domain" → "Canary Client Domain"
 * "research" → "Research"
 */
export function titleCase(domain: string): string {
  return domain
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}
