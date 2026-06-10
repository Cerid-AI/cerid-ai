// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Voice-note client — multipart upload to /sdk/v1/ingest/voice-note.
 * Returns the transcript + artifact id so the F11 overlay can pulse
 * the result + offer "open the artifact".
 */

import { mcpUrl, mcpHeaders } from "./common"

export interface VoiceNoteResponse {
  status: "ingested"
  artifact_id: string
  transcript: string
  transcribe_ms: number
  word_count: number
}

export async function ingestVoiceNote(blob: Blob): Promise<VoiceNoteResponse> {
  const fd = new FormData()
  fd.append("audio", blob, "voice-note.webm")
  const r = await fetch(mcpUrl("/sdk/v1/ingest/voice-note").toString(), {
    method: "POST",
    headers: mcpHeaders(),
    body: fd,
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`voice-note upload failed: HTTP ${r.status}: ${text}`)
  }
  return r.json()
}
