// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Configuration for the Cerid AI chat widget.
 *
 * Required fields: `apiUrl` and `clientId`.
 * All other fields have sensible defaults.
 */
export interface CeridWidgetConfig {
  /** Base URL of the Cerid MCP server (e.g. "https://cerid.example.com"). */
  apiUrl: string;

  /** Client identifier sent as X-Client-ID header prefix. */
  clientId: string;

  /** Optional API key sent as X-API-Key header. */
  apiKey?: string;

  /** Widget position on the page. @default "bottom-right" */
  position?: "bottom-right" | "bottom-left";

  /** Color theme. "auto" follows prefers-color-scheme. @default "auto" */
  theme?: "light" | "dark" | "auto";

  /** Title shown in the widget header. @default "Cerid AI" */
  title?: string;

  /** Input placeholder text. @default "Ask anything..." */
  placeholder?: string;

  /** Message displayed when the widget first opens. */
  initialMessage?: string;
}

/** A single chat message in the conversation. */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  /** True while the assistant is still streaming this message. */
  streaming?: boolean;
}

/** SSE event data from /agent/query. */
export interface SSEChunk {
  type: "token" | "done" | "error";
  content?: string;
  error?: string;
}

/**
 * Default configuration values applied when the user does not specify them.
 */
export const DEFAULT_CONFIG: Required<
  Pick<CeridWidgetConfig, "position" | "theme" | "title" | "placeholder">
> = {
  position: "bottom-right",
  theme: "auto",
  title: "Cerid AI",
  placeholder: "Ask anything...",
};
