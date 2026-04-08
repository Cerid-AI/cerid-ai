// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { CeridWidgetConfig, ChatMessage, SSEChunk } from "./types";

const SESSION_KEY = "cerid-widget-session-id";
const HISTORY_KEY = "cerid-widget-history";
const MAX_HISTORY = 50;

/** Generate a crypto-safe UUID, with fallback for non-secure contexts. */
function generateId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for non-secure contexts (e.g. HTTP iframes)
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/**
 * Lightweight API client for communicating with the Cerid MCP server.
 *
 * - Sends queries via POST to `/sdk/v1/query`
 * - Streams responses via SSE (Server-Sent Events)
 * - Persists session ID and message history in localStorage
 */
export class CeridChatAPI {
  private readonly apiUrl: string;
  private readonly clientId: string;
  private readonly apiKey?: string;
  private sessionId: string;
  private conversationId: string;
  private messages: ChatMessage[] = [];
  private abortController: AbortController | null = null;

  constructor(config: CeridWidgetConfig) {
    this.apiUrl = config.apiUrl.replace(/\/+$/, "");
    this.clientId = config.clientId;
    this.apiKey = config.apiKey;

    // Restore or create session
    this.sessionId = this.loadSessionId();
    this.conversationId = this.sessionId;
    this.messages = this.loadHistory();
  }

  // ---------------------------------------------------------------------------
  // Session persistence
  // ---------------------------------------------------------------------------

  private loadSessionId(): string {
    try {
      const stored = localStorage.getItem(SESSION_KEY);
      if (stored) return stored;
    } catch {
      // localStorage unavailable (e.g. sandboxed iframe)
    }
    const id = generateId();
    try {
      localStorage.setItem(SESSION_KEY, id);
    } catch {
      // Silently ignore
    }
    return id;
  }

  private loadHistory(): ChatMessage[] {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as ChatMessage[];
        return parsed.slice(-MAX_HISTORY);
      }
    } catch {
      // Corrupt or unavailable
    }
    return [];
  }

  private persistHistory(): void {
    try {
      localStorage.setItem(
        HISTORY_KEY,
        JSON.stringify(this.messages.slice(-MAX_HISTORY)),
      );
    } catch {
      // Silently ignore
    }
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  /** Returns a copy of the current message history. */
  getMessages(): ChatMessage[] {
    return [...this.messages];
  }

  /** Returns the current session ID. */
  getSessionId(): string {
    return this.sessionId;
  }

  /** Clears message history and starts a fresh conversation. */
  clearHistory(): void {
    this.messages = [];
    this.conversationId = generateId();
    this.persistHistory();
  }

  /** Abort any in-flight SSE stream. */
  abort(): void {
    this.abortController?.abort();
    this.abortController = null;
  }

  /**
   * Send a message and stream the assistant's response.
   *
   * @param query   - The user's message text.
   * @param onToken - Called for each incremental token from the SSE stream.
   * @param onDone  - Called when the stream completes.
   * @param onError - Called on any error (network, parse, abort).
   * @returns The complete assistant message once done.
   */
  async sendMessage(
    query: string,
    onToken?: (token: string, accumulated: string) => void,
    onDone?: (fullText: string) => void,
    onError?: (error: Error) => void,
  ): Promise<ChatMessage> {
    // Push user message
    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content: query,
      timestamp: Date.now(),
    };
    this.messages.push(userMsg);
    this.persistHistory();

    // Prepare assistant message (will be built up from tokens)
    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      streaming: true,
    };
    this.messages.push(assistantMsg);

    // Abort any previous request
    this.abort();
    this.abortController = new AbortController();

    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "X-Client-ID": `widget-${this.sessionId}`,
      };
      if (this.apiKey) {
        headers["X-API-Key"] = this.apiKey;
      }

      const response = await fetch(`${this.apiUrl}/sdk/v1/query`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          query,
          conversation_id: this.conversationId,
        }),
        signal: this.abortController.signal,
      });

      if (!response.ok) {
        throw new Error(
          `Cerid API error: ${response.status} ${response.statusText}`,
        );
      }

      const contentType = response.headers.get("content-type") ?? "";

      if (contentType.includes("text/event-stream")) {
        // SSE streaming response
        await this.readSSEStream(response, assistantMsg, onToken);
      } else {
        // Fallback: JSON response (non-streaming)
        const json = (await response.json()) as {
          response?: string;
          answer?: string;
        };
        assistantMsg.content = json.response ?? json.answer ?? "";
        onToken?.(assistantMsg.content, assistantMsg.content);
      }

      assistantMsg.streaming = false;
      assistantMsg.timestamp = Date.now();
      this.persistHistory();
      onDone?.(assistantMsg.content);
    } catch (err) {
      assistantMsg.streaming = false;
      if (err instanceof DOMException && err.name === "AbortError") {
        // User-initiated abort, not an error
      } else {
        const error =
          err instanceof Error ? err : new Error(String(err));
        assistantMsg.content =
          assistantMsg.content || "Sorry, something went wrong.";
        onError?.(error);
      }
      this.persistHistory();
    }

    return assistantMsg;
  }

  // ---------------------------------------------------------------------------
  // SSE stream reader
  // ---------------------------------------------------------------------------

  private async readSSEStream(
    response: Response,
    msg: ChatMessage,
    onToken?: (token: string, accumulated: string) => void,
  ): Promise<void> {
    const reader = response.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        // Keep the last (possibly incomplete) line in the buffer
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") return;

          try {
            const chunk = JSON.parse(data) as SSEChunk;
            if (chunk.type === "token" && chunk.content) {
              msg.content += chunk.content;
              onToken?.(chunk.content, msg.content);
            } else if (chunk.type === "done") {
              return;
            } else if (chunk.type === "error") {
              throw new Error(chunk.error ?? "Stream error");
            }
          } catch (parseErr) {
            // Not valid JSON — treat the raw data as a text token.
            // Many SSE implementations just send raw text after "data:".
            if (data && data !== "[DONE]") {
              msg.content += data;
              onToken?.(data, msg.content);
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
}
