// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"

import type {
  ChatMessage,
  ChatModelInfo,
  MemoryExtractionResult,
  Memory,
  Conversation,
} from "../types"

// --- Feedback Loop ---

export async function ingestFeedback(
  userMessage: string,
  assistantResponse: string,
  model: string,
  conversationId: string,
  inputTokens = 0,
  outputTokens = 0,
  latencyMs = 0,
): Promise<void> {
  const res = await fetch(`${MCP_BASE}/ingest/feedback`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      user_message: userMessage,
      assistant_response: assistantResponse,
      model,
      conversation_id: conversationId,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      latency_ms: latencyMs,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Feedback ingest failed: ${res.status}`))
}

// --- Chat ---

export async function streamChat(
  messages: Pick<ChatMessage, "role" | "content">[],
  model: string,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
  onModelInfo?: (info: ChatModelInfo) => void,
  chatSettings?: { temperature?: number; top_p?: number },
): Promise<void> {
  const url = `${MCP_BASE}/chat/stream`
  const payload: Record<string, unknown> = {
    model,
    messages: messages.map((m) => ({ role: m.role, content: m.content })),
    temperature: chatSettings?.temperature ?? 0.7,
    stream: true,
  }
  if (chatSettings?.top_p != null) {
    payload.top_p = chatSettings.top_p
  }
  const res = await fetch(url, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Chat request failed (${res.status}): ${text}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error("No response body")

  const decoder = new TextDecoder()
  let buffer = ""
  let lastModelInfo: ChatModelInfo | undefined

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || !trimmed.startsWith("data: ")) continue
        const data = trimmed.slice(6)
        if (data === "[DONE]") return

        try {
          const parsed = JSON.parse(data)
          if (parsed.cerid_meta) {
            lastModelInfo = parsed.cerid_meta as ChatModelInfo
            onModelInfo?.(lastModelInfo)
            continue
          }
          // OpenRouter may substitute a different model — update if so
          if (parsed.cerid_meta_update) {
            if (parsed.cerid_meta_update.actual_model) {
              onModelInfo?.({ ...lastModelInfo!, actual_model: parsed.cerid_meta_update.actual_model })
            }
            if (parsed.cerid_meta_update.fallback_model) {
              onModelInfo?.({
                ...lastModelInfo!,
                resolved_model: parsed.cerid_meta_update.fallback_model,
                fallback_model: parsed.cerid_meta_update.fallback_model,
                original_error: parsed.cerid_meta_update.original_error,
              })
              console.warn(
                `[chat] Model fallback: original failed (${parsed.cerid_meta_update.original_error}), using ${parsed.cerid_meta_update.fallback_model}`,
              )
            }
            continue
          }
          if (parsed.error) {
            const code = parsed.error.code
            const msg = parsed.error.message || "Upstream error"
            const err = new Error(msg)
            ;(err as Error & { code?: number }).code = code
            throw err
          }
          const content = parsed.choices?.[0]?.delta?.content
          if (content) onChunk(content)
        } catch (e) {
          if (e instanceof SyntaxError) {
            console.warn("[streamChat] malformed SSE chunk:", data)
          } else {
            throw e
          }
        }
      }
    }

    // Flush any remaining data in the buffer after stream ends
    if (buffer.trim()) {
      const trimmed = buffer.trim()
      if (trimmed.startsWith("data: ")) {
        const data = trimmed.slice(6)
        if (data !== "[DONE]") {
          try {
            const parsed = JSON.parse(data)
            if (!parsed.cerid_meta && !parsed.error) {
              const content = parsed.choices?.[0]?.delta?.content
              if (content) onChunk(content)
            }
          } catch { /* malformed trailing chunk */ }
        }
      }
    }
  } finally {
    reader.cancel()
  }
}

/**
 * Summarize conversation history using the current model via Bifrost.
 * The summary preserves key facts, decisions, code, and action items.
 */
export async function summarizeConversation(
  messages: Pick<ChatMessage, "role" | "content">[],
  model: string,
  signal?: AbortSignal,
): Promise<string> {
  const conversationText = messages
    .map((m) => `${m.role === "user" ? "User" : m.role === "assistant" ? "Assistant" : "System"}: ${m.content}`)
    .join("\n\n")

  const summaryMessages: Pick<ChatMessage, "role" | "content">[] = [
    {
      role: "system",
      content:
        "You are a conversation summarizer. Produce a concise summary of the conversation below. " +
        "Preserve all key facts, decisions, code snippets, and action items. " +
        "The summary will be used as context for a new model, so include everything needed to continue the conversation seamlessly. " +
        "Do not add commentary.",
    },
    {
      role: "user",
      content: `Summarize this conversation:\n\n${conversationText}`,
    },
  ]

  let summary = ""
  await streamChat(summaryMessages, model, (chunk) => {
    summary += chunk
  }, signal)

  return summary
}

/**
 * Compress conversation history to fit a target token budget via backend LLM.
 * Returns compressed messages and token counts.
 */
export async function compressConversation(
  messages: Pick<ChatMessage, "role" | "content">[],
  targetTokens: number,
): Promise<{ messages: { role: string; content: string }[]; original_tokens: number; compressed_tokens: number }> {
  const res = await fetch(`${MCP_BASE}/chat/compress`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ messages, target_tokens: targetTokens }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Compress failed: ${res.status}`))
  return res.json()
}

// --- Memory Extraction ---

export async function extractMemories(
  responseText: string,
  conversationId: string,
  model = "",
): Promise<MemoryExtractionResult> {
  const res = await fetch(`${MCP_BASE}/agent/memory/extract`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      response_text: responseText,
      conversation_id: conversationId,
      model,
    }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory extraction failed: ${res.status}`))
  return res.json()
}

export async function archiveMemories(
  retentionDays = 180,
): Promise<{ archived: number; remaining: number }> {
  const res = await fetch(`${MCP_BASE}/agent/memory/archive`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ retention_days: retentionDays }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory archive failed: ${res.status}`))
  return res.json()
}

// --- Memories ---

export async function fetchMemories(
  opts: { type?: string; conversationId?: string; limit?: number; offset?: number } = {},
): Promise<{ memories: Memory[]; total: number }> {
  const params = new URLSearchParams()
  if (opts.type) params.set("type", opts.type)
  if (opts.conversationId) params.set("conversation_id", opts.conversationId)
  if (opts.limit !== undefined) params.set("limit", String(opts.limit))
  if (opts.offset !== undefined) params.set("offset", String(opts.offset))
  const res = await fetch(`${MCP_BASE}/memories?${params}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Memories fetch failed: ${res.status}`))
  return res.json()
}

export async function updateMemory(memoryId: string, summary: string): Promise<Memory> {
  const res = await fetch(`${MCP_BASE}/memories/${memoryId}`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ summary }),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory update failed: ${res.status}`))
  return res.json()
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/memories/${memoryId}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Memory delete failed: ${res.status}`))
}

// ── User State Sync ─────────────────────────────────────────────────────────

export async function fetchUserState(): Promise<{
  settings: Record<string, unknown>
  preferences: Record<string, unknown>
  conversation_ids: string[]
}> {
  const res = await fetch(`${MCP_BASE}/user-state`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch user state"))
  return res.json()
}

export async function fetchSyncedConversations(): Promise<Conversation[]> {
  const res = await fetch(`${MCP_BASE}/user-state/conversations`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch conversations"))
  const data = await res.json()
  return data.conversations ?? []
}

export async function syncConversation(conversation: Conversation): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(conversation),
  })
}

export async function syncConversationsBulk(conversations: Conversation[]): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations/bulk`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(conversations),
  })
}

export async function deleteConversationSync(convId: string): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/conversations/${convId}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
}

export async function syncPreferences(prefs: Record<string, unknown>): Promise<void> {
  await fetch(`${MCP_BASE}/user-state/preferences`, {
    method: "PATCH",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(prefs),
  })
}
