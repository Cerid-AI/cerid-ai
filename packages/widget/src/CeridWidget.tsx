// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { CeridChatAPI } from "./api";
import type { ChatMessage, CeridWidgetConfig } from "./types";
import { DEFAULT_CONFIG } from "./types";

// ---------------------------------------------------------------------------
// SVG icons (inline to keep bundle dependency-free)
// ---------------------------------------------------------------------------

const SendIcon = () => (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
  </svg>
);

const CloseIcon = () => (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
  </svg>
);

const TrashIcon = () => (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
  </svg>
);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface CeridWidgetProps {
  config: CeridWidgetConfig;
  /** If true, render as full-page (no bubble, no outer container positioning). */
  fullPage?: boolean;
  /** Called when the user clicks the close button. */
  onClose?: () => void;
}

export function CeridWidget({ config, fullPage, onClose }: CeridWidgetProps) {
  const title = config.title ?? DEFAULT_CONFIG.title;
  const placeholder = config.placeholder ?? DEFAULT_CONFIG.placeholder;

  const apiRef = useRef<CeridChatAPI | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  // Initialise API client once
  useEffect(() => {
    const api = new CeridChatAPI(config);
    apiRef.current = api;
    setMessages(api.getMessages());

    return () => {
      api.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.apiUrl, config.clientId]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus the input when the widget opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = useCallback(async () => {
    const api = apiRef.current;
    const trimmed = input.trim();
    if (!api || !trimmed || sending) return;

    setInput("");
    setSending(true);

    // Add user message immediately for responsiveness
    const currentMessages = api.getMessages();
    setMessages([
      ...currentMessages,
      {
        id: "pending-user",
        role: "user",
        content: trimmed,
        timestamp: Date.now(),
      },
    ]);

    await api.sendMessage(
      trimmed,
      // onToken: update the last (assistant) message progressively
      (_token, accumulated) => {
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") {
            copy[copy.length - 1] = { ...last, content: accumulated };
          }
          return copy;
        });
      },
      // onDone
      () => {
        setMessages(api.getMessages());
        setSending(false);
        inputRef.current?.focus();
      },
      // onError
      () => {
        setMessages(api.getMessages());
        setSending(false);
        inputRef.current?.focus();
      },
    );

    // After sendMessage pushes both user + assistant messages, sync state
    setMessages(api.getMessages());
  }, [input, sending]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      handleSend();
    },
    [handleSend],
  );

  const handleClear = useCallback(() => {
    apiRef.current?.clearHistory();
    setMessages([]);
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const showWelcome =
    messages.length === 0 && config.initialMessage;

  return (
    <div className={`cerid-panel${fullPage ? " cerid-fullpage-panel" : ""}`}>
      {/* Header */}
      <div className="cerid-header">
        <h3 className="cerid-header-title">{title}</h3>
        <div className="cerid-header-actions">
          <button
            className="cerid-header-btn"
            onClick={handleClear}
            title="Clear conversation"
            type="button"
          >
            <TrashIcon />
          </button>
          {onClose && (
            <button
              className="cerid-header-btn"
              onClick={onClose}
              title="Close"
              type="button"
            >
              <CloseIcon />
            </button>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="cerid-messages">
        {showWelcome && (
          <div className="cerid-welcome">{config.initialMessage}</div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`cerid-message ${msg.role}${msg.streaming ? " streaming" : ""}`}
          >
            {msg.content}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form className="cerid-input-area" onSubmit={handleSubmit}>
        <textarea
          ref={inputRef}
          className="cerid-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          disabled={sending}
        />
        <button
          className="cerid-send-btn"
          type="submit"
          disabled={sending || !input.trim()}
          title="Send message"
        >
          <SendIcon />
        </button>
      </form>

      {/* Footer */}
      <div className="cerid-footer">
        Powered by{" "}
        <a href="https://cerid.ai" target="_blank" rel="noopener noreferrer">
          Cerid AI
        </a>
      </div>
    </div>
  );
}
