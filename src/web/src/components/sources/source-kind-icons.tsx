// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Single source of truth for the icon + display label of each of the
 * 22 source kinds. Used by the F1 gallery, F2 FAB radial menu, and
 * source-detail pane.
 */

import {
  Bookmark,
  Calendar,
  Clipboard,
  FileAudio,
  Files,
  GitBranch,
  Image,
  Inbox,
  Library,
  Mail,
  MessageCircle,
  Mic,
  Notebook,
  Plug,
  Rss,
  Smartphone,
  Webhook,
  type LucideIcon,
} from "lucide-react"

export interface SourceKindDescriptor {
  kind: string
  label: string
  icon: LucideIcon
  blurb: string
}

export const KIND_DESCRIPTORS: Record<string, SourceKindDescriptor> = {
  // Core (11)
  folder: { kind: "folder", label: "Folder", icon: Files, blurb: "Watch a local directory" },
  bookmarks: { kind: "bookmarks", label: "Bookmarks", icon: Bookmark, blurb: "One-shot import from browser exports" },
  rss: { kind: "rss", label: "RSS / Atom", icon: Rss, blurb: "Polled feed" },
  url_watch: { kind: "url_watch", label: "URL Watch", icon: GitBranch, blurb: "Diff a single page on a cadence" },
  webhook: { kind: "webhook", label: "Webhook", icon: Webhook, blurb: "Inbound HTTP receiver" },
  chat_capture: { kind: "chat_capture", label: "Chat Capture", icon: MessageCircle, blurb: "Slack / Discord / Teams / Matrix" },
  dev_events: { kind: "dev_events", label: "Dev Events", icon: GitBranch, blurb: "GitHub / Linear / Sentry / Stripe" },
  clipboard: { kind: "clipboard", label: "Clipboard", icon: Clipboard, blurb: "Background clipboard daemon" },
  voice_note: { kind: "voice_note", label: "Voice Note", icon: Mic, blurb: "Quick capture from microphone" },
  external_adapter: { kind: "external_adapter", label: "External Adapter", icon: Plug, blurb: "Custom Python or HTTP source" },
  knowledge_pack: { kind: "knowledge_pack", label: "Knowledge Pack", icon: Library, blurb: "Curated topical bundles" },

  // Pro (11)
  gmail: { kind: "gmail", label: "Gmail", icon: Mail, blurb: "Inbox sync" },
  outlook: { kind: "outlook", label: "Outlook", icon: Mail, blurb: "Inbox sync" },
  google_calendar: { kind: "google_calendar", label: "Google Calendar", icon: Calendar, blurb: "Event sync" },
  outlook_calendar: { kind: "outlook_calendar", label: "Outlook Calendar", icon: Calendar, blurb: "Event sync" },
  meeting_audio: { kind: "meeting_audio", label: "Meeting Audio", icon: FileAudio, blurb: "Recording → transcript → KB" },
  apple_notes: { kind: "apple_notes", label: "Apple Notes", icon: Notebook, blurb: "Sync from local Notes.app" },
  apple_mail: { kind: "apple_mail", label: "Apple Mail", icon: Inbox, blurb: "Local Mail.app archive" },
  imessage: { kind: "imessage", label: "iMessage", icon: Smartphone, blurb: "Local conversation archive" },
  apple_calendar: { kind: "apple_calendar", label: "Apple Calendar", icon: Calendar, blurb: "Local EventKit calendar" },
  apple_photos: { kind: "apple_photos", label: "Apple Photos", icon: Image, blurb: "Local PhotoKit library" },
  apple_reminders: { kind: "apple_reminders", label: "Apple Reminders", icon: Calendar, blurb: "Local EventKit reminders" },
}

export function descriptorFor(kind: string): SourceKindDescriptor {
  return KIND_DESCRIPTORS[kind] ?? {
    kind,
    label: kind,
    icon: Plug,
    blurb: "Source",
  }
}
