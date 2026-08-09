// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * <cerid-chat> — verified-chat web component.
 *
 * Usage:
 *   <cerid-chat host="https://your-cerid-host.local" token="..."></cerid-chat>
 *
 * Observed attributes:
 *   host        (required) — Cerid server base URL
 *   token       — Bearer token for Authorization header
 *   placeholder — Input placeholder text (default: "Ask Cerid anything")
 *   theme       — "light" | "dark" | "auto" (default: "auto")
 *   max-claims  — Maximum claim badges rendered per message (default: 50)
 *
 * The component attaches a shadow root (mode: "open"), inlines its CSS,
 * and renders a full chat UI with per-claim verification badges.
 */

import { fetchQuery, ApiError } from "./api.js";
import { createClaimBadge } from "./claim-badge.js";
import type { ChatMessage, ClaimVerification, WidgetTheme } from "./types.js";
import { ICON_SEND } from "./icons.js";
// CSS is imported as a string and injected into the shadow DOM.
// Vite's ?inline query handles this at build time.
import rawStyles from "./styles.css?inline";

declare const __DEBUG__: boolean;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// ---------------------------------------------------------------------------
// Web Component
// ---------------------------------------------------------------------------

export class CeridChat extends HTMLElement {
  static readonly observedAttributes = [
    "host",
    "token",
    "placeholder",
    "theme",
    "max-claims",
  ] as const;

  // Shadow DOM root
  private readonly shadow: ShadowRoot;

  // DOM refs (populated in connectedCallback)
  private messagesEl!: HTMLElement;
  private inputEl!: HTMLTextAreaElement;
  private sendBtn!: HTMLButtonElement;

  // State
  private messages: ChatMessage[] = [];
  private abortController: AbortController | null = null;
  private mediaQuery: MediaQueryList | null = null;
  private mqListener: ((e: MediaQueryListEvent) => void) | null = null;

  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: "open" });
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  connectedCallback(): void {
    this.render();
    this.applyTheme();
  }

  disconnectedCallback(): void {
    this.abortController?.abort();
    if (this.mqListener && this.mediaQuery) {
      this.mediaQuery.removeEventListener("change", this.mqListener);
    }
  }

  attributeChangedCallback(
    name: string,
    oldVal: string | null,
    newVal: string | null,
  ): void {
    if (oldVal === newVal) return;
    if (name === "theme") {
      this.applyTheme();
    }
    if (name === "placeholder" && this.inputEl) {
      this.inputEl.placeholder = newVal ?? "Ask Cerid anything";
    }
  }

  // ---------------------------------------------------------------------------
  // Attribute accessors
  // ---------------------------------------------------------------------------

  get host(): string {
    return this.getAttribute("host") ?? "";
  }

  get token(): string | undefined {
    return this.getAttribute("token") ?? undefined;
  }

  get placeholder(): string {
    return this.getAttribute("placeholder") ?? "Ask Cerid anything";
  }

  get theme(): WidgetTheme {
    const val = this.getAttribute("theme");
    if (val === "dark" || val === "light" || val === "auto") return val;
    return "auto";
  }

  get maxClaims(): number {
    const val = parseInt(this.getAttribute("max-claims") ?? "50", 10);
    return isNaN(val) || val < 1 ? 50 : Math.min(val, 200);
  }

  // ---------------------------------------------------------------------------
  // Initial render (called once in connectedCallback)
  // ---------------------------------------------------------------------------

  private render(): void {
    // Inject styles
    const styleEl = document.createElement("style");
    styleEl.textContent = rawStyles;
    this.shadow.appendChild(styleEl);

    const panel = document.createElement("div");
    panel.className = "cerid-panel";
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "Cerid AI Chat");

    // Header
    panel.appendChild(this.buildHeader());

    // Messages area
    this.messagesEl = document.createElement("div");
    this.messagesEl.className = "cerid-messages";
    this.messagesEl.setAttribute("role", "log");
    this.messagesEl.setAttribute("aria-live", "polite");
    this.messagesEl.setAttribute("aria-label", "Chat messages");
    panel.appendChild(this.messagesEl);

    // Show empty state if no host
    if (!this.host) {
      this.showErrorBanner(
        "Missing required attribute: host. Add host=\"https://your-cerid-server\" to the element.",
      );
    } else {
      this.renderEmptyState();
    }

    // Input area
    panel.appendChild(this.buildInputArea());

    // Footer
    panel.appendChild(this.buildFooter());

    this.shadow.appendChild(panel);
  }

  private buildHeader(): HTMLElement {
    const header = document.createElement("div");
    header.className = "cerid-header";

    const title = document.createElement("h3");
    title.className = "cerid-header__title";
    title.textContent = "Cerid AI";
    header.appendChild(title);

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "cerid-header__clear";
    clearBtn.textContent = "Clear";
    clearBtn.setAttribute("aria-label", "Clear conversation");
    clearBtn.addEventListener("click", () => this.clearConversation());
    header.appendChild(clearBtn);

    return header;
  }

  private buildInputArea(): HTMLElement {
    const form = document.createElement("form");
    form.className = "cerid-input-area";
    form.setAttribute("role", "search");
    form.setAttribute("aria-label", "Chat input");

    this.inputEl = document.createElement("textarea");
    this.inputEl.className = "cerid-input";
    this.inputEl.placeholder = this.placeholder;
    this.inputEl.rows = 1;
    this.inputEl.setAttribute("aria-label", "Message input");
    this.inputEl.setAttribute("aria-multiline", "true");
    this.inputEl.addEventListener("keydown", (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void this.handleSubmit();
      }
    });
    form.appendChild(this.inputEl);

    this.sendBtn = document.createElement("button");
    this.sendBtn.type = "button";
    this.sendBtn.className = "cerid-send-btn";
    this.sendBtn.setAttribute("aria-label", "Send message");
    this.sendBtn.disabled = true;
    this.sendBtn.innerHTML = ICON_SEND;
    this.sendBtn.addEventListener("click", () => void this.handleSubmit());
    form.appendChild(this.sendBtn);

    // Enable send button only when input has content and host is set
    this.inputEl.addEventListener("input", () => {
      this.sendBtn.disabled = !this.inputEl.value.trim() || !this.host;
    });

    // Disable input area if no host
    if (!this.host) {
      this.inputEl.disabled = true;
      this.sendBtn.disabled = true;
    }

    return form;
  }

  private buildFooter(): HTMLElement {
    const footer = document.createElement("div");
    footer.className = "cerid-footer";
    footer.innerHTML = `Powered by <a href="https://cerid.ai" target="_blank" rel="noopener noreferrer">Cerid AI</a>`;
    return footer;
  }

  // ---------------------------------------------------------------------------
  // Theme
  // ---------------------------------------------------------------------------

  private applyTheme(): void {
    // Remove existing media query listener
    if (this.mqListener && this.mediaQuery) {
      this.mediaQuery.removeEventListener("change", this.mqListener);
      this.mqListener = null;
    }

    const theme = this.theme;

    if (theme === "dark") {
      this.classList.add("cerid-dark");
    } else if (theme === "light") {
      this.classList.remove("cerid-dark");
    } else {
      // "auto": follow prefers-color-scheme
      this.mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      const update = (e: MediaQueryList | MediaQueryListEvent) => {
        if (e.matches) {
          this.classList.add("cerid-dark");
        } else {
          this.classList.remove("cerid-dark");
        }
      };
      update(this.mediaQuery);
      this.mqListener = update as (e: MediaQueryListEvent) => void;
      this.mediaQuery.addEventListener("change", this.mqListener);
    }
  }

  // ---------------------------------------------------------------------------
  // Message rendering
  // ---------------------------------------------------------------------------

  private renderEmptyState(): void {
    const empty = document.createElement("div");
    empty.className = "cerid-empty";
    empty.setAttribute("role", "status");
    empty.textContent = "Ask Cerid anything";
    this.messagesEl.appendChild(empty);
  }

  private showErrorBanner(text: string): void {
    const banner = document.createElement("div");
    banner.className = "cerid-error-banner";
    banner.setAttribute("role", "alert");
    banner.textContent = text;
    this.messagesEl.appendChild(banner);
  }

  private appendMessage(msg: ChatMessage): HTMLElement {
    // Remove empty state on first message
    const empty = this.messagesEl.querySelector(".cerid-empty");
    if (empty) empty.remove();

    const wrapper = document.createElement("div");
    wrapper.className = `cerid-message cerid-message--${msg.role}`;
    wrapper.setAttribute("data-msg-id", msg.id);
    if (msg.streaming) wrapper.classList.add("cerid-message--streaming");

    const bubble = document.createElement("div");
    bubble.className = "cerid-message__bubble";
    bubble.textContent = msg.content;
    wrapper.appendChild(bubble);

    if (msg.role === "assistant") {
      const claimsEl = document.createElement("div");
      claimsEl.className = "cerid-message__claims";
      claimsEl.setAttribute("aria-label", "Claim verification results");
      claimsEl.setAttribute("role", "list");
      wrapper.appendChild(claimsEl);
    }

    this.messagesEl.appendChild(wrapper);
    this.scrollToBottom();
    return wrapper;
  }

  private updateMessageBubble(id: string, content: string, streaming: boolean): void {
    const wrapper = this.messagesEl.querySelector(`[data-msg-id="${id}"]`);
    if (!wrapper) return;
    const bubble = wrapper.querySelector(".cerid-message__bubble");
    if (bubble) bubble.textContent = content;
    if (streaming) {
      wrapper.classList.add("cerid-message--streaming");
    } else {
      wrapper.classList.remove("cerid-message--streaming");
    }
  }

  private renderClaims(msgId: string, claims: ClaimVerification[]): void {
    const wrapper = this.messagesEl.querySelector(`[data-msg-id="${msgId}"]`);
    if (!wrapper) return;
    const claimsEl = wrapper.querySelector(".cerid-message__claims");
    if (!claimsEl) return;

    claimsEl.innerHTML = "";
    const capped = claims.slice(0, this.maxClaims);

    for (const claim of capped) {
      const badge = createClaimBadge(claim);
      const li = document.createElement("div");
      li.setAttribute("role", "listitem");
      li.appendChild(badge);
      claimsEl.appendChild(li);
    }
  }

  private scrollToBottom(): void {
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // Submit / send
  // ---------------------------------------------------------------------------

  private async handleSubmit(): Promise<void> {
    const query = this.inputEl.value.trim();
    if (!query || !this.host) return;

    // Abort any in-flight request
    this.abortController?.abort();
    this.abortController = new AbortController();

    this.inputEl.value = "";
    this.sendBtn.disabled = true;
    this.inputEl.disabled = true;

    // User message
    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content: query,
      timestamp: Date.now(),
    };
    this.messages.push(userMsg);
    this.appendMessage(userMsg);

    // Assistant placeholder
    const assistantMsg: ChatMessage = {
      id: generateId(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      streaming: true,
    };
    this.messages.push(assistantMsg);
    const assistantEl = this.appendMessage(assistantMsg);

    // Show a simple loading indicator
    const bubble = assistantEl.querySelector(".cerid-message__bubble");
    if (bubble) {
      bubble.textContent = "";
    }

    try {
      const conversationMessages = this.messages
        .slice(0, -1) // Exclude the empty assistant placeholder
        .map((m) => ({ role: m.role, content: m.content }));

      const result = await fetchQuery(
        this.host,
        {
          query,
          conversation_messages: conversationMessages.slice(-20), // Last 20 for context
        },
        {
          signal: this.abortController.signal,
          token: this.token,
        },
      );

      // Prefer answer > context (SDK query returns context, not a generated answer;
      // if the Self-RAG pipeline ran, result.answer is the LLM response)
      const responseText = result.answer ?? result.context ?? "";
      assistantMsg.content = responseText;
      assistantMsg.streaming = false;
      assistantMsg.claims = result.claims;

      this.updateMessageBubble(assistantMsg.id, responseText, false);

      if (result.claims?.length) {
        this.renderClaims(assistantMsg.id, result.claims);
      }

      if (__DEBUG__) {
        console.debug("[cerid-widget] Query result:", result);
      }
    } catch (err) {
      assistantMsg.streaming = false;

      if (err instanceof DOMException && err.name === "AbortError") {
        // User navigated away — suppress
        this.updateMessageBubble(assistantMsg.id, "", false);
        return;
      }

      const text =
        err instanceof ApiError
          ? `Error ${err.status}: ${err.message}`
          : "Something went wrong. Please try again.";

      assistantMsg.content = text;
      assistantMsg.error = true;
      this.updateMessageBubble(assistantMsg.id, text, false);
    } finally {
      this.inputEl.disabled = false;
      this.sendBtn.disabled = false;
      this.inputEl.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Clear
  // ---------------------------------------------------------------------------

  private clearConversation(): void {
    this.messages = [];
    this.messagesEl.innerHTML = "";
    this.renderEmptyState();
    this.inputEl.value = "";
    this.sendBtn.disabled = true;
    this.inputEl.focus();
  }
}
