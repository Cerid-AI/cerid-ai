const DEFAULT_TIMEOUT_MS = 3e4;
const RETRYABLE_STATUS = /* @__PURE__ */ new Set([503, 502, 504]);
class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}
async function fetchQuery(host, body, opts = {}) {
  const url = `${host.replace(/\/+$/, "")}/sdk/v1/query`;
  const { signal, token, timeoutMs = DEFAULT_TIMEOUT_MS } = opts;
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json"
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const attempt = async () => {
    return fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: combined
    });
  };
  let response;
  try {
    response = await attempt();
  } catch (err) {
    throw err;
  }
  if (RETRYABLE_STATUS.has(response.status)) {
    try {
      response = await attempt();
    } catch (err) {
      throw err;
    }
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      `Cerid API error: ${response.status} ${response.statusText}`
    );
  }
  return response.json();
}
const ICON_CHECK_CIRCLE = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`;
const ICON_MINUS = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
const ICON_CIRCLE_DOT = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/></svg>`;
const ICON_SEND = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;
const ICON_EXTERNAL_LINK = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
function deriveBand(claim) {
  const hasSource = !!(claim.source_artifact_id || (claim.source_urls?.length ?? 0) > 0);
  if (claim.status === "verified") {
    return hasSource ? "verified" : "partial";
  }
  if (claim.status === "uncertain") {
    return "partial";
  }
  return "unverified";
}
function sourceCount(claim) {
  const urlCount = claim.source_urls?.length ?? 0;
  const artifactCount = claim.source_artifact_id ? 1 : 0;
  return Math.max(urlCount, artifactCount);
}
const BAND_CONFIG = {
  verified: {
    label: (n) => n > 0 ? `Verified · ${n} source${n > 1 ? "s" : ""}` : "Verified",
    ariaLabel: (n) => n > 0 ? `Claim verified with ${n} source${n > 1 ? "s" : ""}` : "Claim verified",
    icon: ICON_CHECK_CIRCLE,
    cssClass: "cerid-badge--verified"
  },
  partial: {
    label: () => "Partial",
    ariaLabel: () => "Claim partially verified or uncertain",
    icon: ICON_MINUS,
    cssClass: "cerid-badge--partial"
  },
  unverified: {
    label: () => "Unverified",
    ariaLabel: () => "Claim unverified — no supporting source found",
    icon: ICON_CIRCLE_DOT,
    cssClass: "cerid-badge--unverified"
  }
};
function createClaimBadge(claim) {
  const band = deriveBand(claim);
  const n = sourceCount(claim);
  const cfg = BAND_CONFIG[band];
  const wrapper = document.createElement("span");
  wrapper.className = "cerid-badge-wrapper";
  const badge = document.createElement("button");
  badge.type = "button";
  badge.className = `cerid-badge ${cfg.cssClass}`;
  badge.setAttribute("aria-label", cfg.ariaLabel(n));
  badge.setAttribute("data-band", band);
  badge.setAttribute("aria-haspopup", "true");
  badge.setAttribute("aria-expanded", "false");
  const iconSpan = document.createElement("span");
  iconSpan.className = "cerid-badge__icon";
  iconSpan.innerHTML = cfg.icon;
  badge.appendChild(iconSpan);
  const labelSpan = document.createElement("span");
  labelSpan.className = "cerid-badge__label";
  labelSpan.textContent = cfg.label(n);
  badge.appendChild(labelSpan);
  wrapper.appendChild(badge);
  let popover = null;
  let open = false;
  badge.addEventListener("click", () => {
    open = !open;
    badge.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      if (!popover) {
        popover = createPopover(claim, band, n);
        wrapper.appendChild(popover);
      }
      popover.removeAttribute("hidden");
    } else {
      popover?.setAttribute("hidden", "");
    }
  });
  badge.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open) {
      open = false;
      badge.setAttribute("aria-expanded", "false");
      popover?.setAttribute("hidden", "");
    }
  });
  return wrapper;
}
function createPopover(claim, band, n) {
  const popover = document.createElement("div");
  popover.className = `cerid-badge-popover cerid-badge-popover--${band}`;
  popover.setAttribute("role", "tooltip");
  const claimEl = document.createElement("p");
  claimEl.className = "cerid-badge-popover__claim";
  claimEl.textContent = claim.claim;
  popover.appendChild(claimEl);
  if (claim.reason) {
    const reasonEl = document.createElement("p");
    reasonEl.className = "cerid-badge-popover__reason";
    reasonEl.textContent = claim.reason;
    popover.appendChild(reasonEl);
  }
  const confEl = document.createElement("p");
  confEl.className = "cerid-badge-popover__confidence";
  confEl.textContent = `Confidence: ${Math.round(claim.confidence * 100)}%`;
  popover.appendChild(confEl);
  if (n > 0) {
    const sourcesEl = document.createElement("div");
    sourcesEl.className = "cerid-badge-popover__sources";
    if (claim.source_filename) {
      const srcEl = document.createElement("span");
      srcEl.className = "cerid-badge-popover__source-file";
      srcEl.textContent = claim.source_filename;
      sourcesEl.appendChild(srcEl);
    }
    if (claim.source_urls?.length) {
      for (const url of claim.source_urls.slice(0, 3)) {
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.className = "cerid-badge-popover__source-link";
        link.setAttribute("aria-label", `Source: ${url}`);
        const linkIcon = document.createElement("span");
        linkIcon.innerHTML = ICON_EXTERNAL_LINK;
        linkIcon.className = "cerid-badge-popover__source-link-icon";
        link.appendChild(linkIcon);
        const linkText = document.createElement("span");
        linkText.textContent = url.length > 40 ? `${url.slice(0, 40)}…` : url;
        link.appendChild(linkText);
        sourcesEl.appendChild(link);
      }
    }
    popover.appendChild(sourcesEl);
  }
  return popover;
}
const rawStyles = '/* Copyright (c) 2026 Cerid AI. All rights reserved. */\n/* SPDX-License-Identifier: Apache-2.0 */\n\n/*\n * Cerid AI Chat Widget — Shadow DOM Styles\n *\n * All variables use CSS custom properties so operators can override them\n * on :host from outside the shadow root with ::part() or host params.\n *\n * No hex literals in rule values — all colors go through custom properties.\n * System font stack — no external font dependencies.\n * WCAG AA contrast enforced for all text/background pairings.\n */\n\n/* ============================================================\n   Host & CSS custom properties (light theme default)\n   ============================================================ */\n\n:host {\n  /* Typography */\n  --cerid-font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,\n    "Helvetica Neue", Arial, sans-serif;\n  --cerid-radius: 12px;\n  --cerid-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);\n  --cerid-transition: 180ms ease;\n\n  /* Surfaces */\n  --cerid-bg: #ffffff;\n  --cerid-bg-secondary: #f4f5f7;\n  --cerid-bg-hover: #eaecf0;\n\n  /* Text — WCAG AA: #1a1a2e on #ffffff = 14.8:1 */\n  --cerid-fg: #1a1a2e;\n  --cerid-fg-muted: #6b7280;\n\n  /* Borders */\n  --cerid-border: #e5e7eb;\n  --cerid-input-border: #d1d5db;\n\n  /* Accent (brand blue) */\n  --cerid-accent: #3b82f6;\n  --cerid-accent-hover: #2563eb;\n  --cerid-accent-fg: #ffffff;\n\n  /* Message bubbles */\n  --cerid-user-bg: #3b82f6;\n  --cerid-user-fg: #ffffff;\n  --cerid-assistant-bg: #f4f5f7;\n  --cerid-assistant-fg: #1a1a2e;\n\n  /* Verification band colours */\n  --cerid-badge-verified-bg: rgba(34, 197, 94, 0.12);\n  --cerid-badge-verified-border: rgba(34, 197, 94, 0.4);\n  --cerid-badge-verified-fg: #15803d;        /* WCAG AA on white */\n  --cerid-badge-partial-bg: rgba(245, 158, 11, 0.12);\n  --cerid-badge-partial-border: rgba(245, 158, 11, 0.4);\n  --cerid-badge-partial-fg: #92400e;\n  --cerid-badge-unverified-bg: rgba(239, 68, 68, 0.12);\n  --cerid-badge-unverified-border: rgba(239, 68, 68, 0.4);\n  --cerid-badge-unverified-fg: #991b1b;\n\n  font-family: var(--cerid-font);\n  font-size: 14px;\n  line-height: 1.5;\n  color: var(--cerid-fg);\n  display: block;\n}\n\n/* ============================================================\n   Dark theme\n   ============================================================ */\n\n:host([theme="dark"]),\n:host(.cerid-dark) {\n  --cerid-bg: #1a1a2e;\n  --cerid-bg-secondary: #232340;\n  --cerid-bg-hover: #2d2d50;\n  --cerid-fg: #e5e7eb;\n  --cerid-fg-muted: #9ca3af;\n  --cerid-border: #374151;\n  --cerid-input-border: #4b5563;\n  --cerid-accent: #60a5fa;\n  --cerid-accent-hover: #3b82f6;\n  --cerid-accent-fg: #ffffff;\n  --cerid-user-bg: #3b82f6;\n  --cerid-user-fg: #ffffff;\n  --cerid-assistant-bg: #232340;\n  --cerid-assistant-fg: #e5e7eb;\n  --cerid-badge-verified-fg: #4ade80;\n  --cerid-badge-partial-fg: #fbbf24;\n  --cerid-badge-unverified-fg: #f87171;\n}\n\n/* ============================================================\n   Panel layout\n   ============================================================ */\n\n.cerid-panel {\n  position: fixed;\n  z-index: 2147483647;\n  bottom: 24px;\n  right: 24px;\n  width: 380px;\n  max-width: calc(100vw - 32px);\n  height: 560px;\n  max-height: calc(100vh - 48px);\n  border-radius: var(--cerid-radius);\n  background: var(--cerid-bg);\n  box-shadow: var(--cerid-shadow);\n  border: 1px solid var(--cerid-border);\n  display: flex;\n  flex-direction: column;\n  overflow: hidden;\n}\n\n/* ============================================================\n   Header\n   ============================================================ */\n\n.cerid-header {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  padding: 14px 16px;\n  border-bottom: 1px solid var(--cerid-border);\n  background: var(--cerid-bg);\n  flex-shrink: 0;\n}\n\n.cerid-header__title {\n  font-size: 15px;\n  font-weight: 600;\n  color: var(--cerid-fg);\n  margin: 0;\n}\n\n.cerid-header__clear {\n  background: none;\n  border: none;\n  cursor: pointer;\n  padding: 4px 6px;\n  border-radius: 6px;\n  color: var(--cerid-fg-muted);\n  font-size: 12px;\n  line-height: 1;\n  transition: background var(--cerid-transition), color var(--cerid-transition);\n}\n\n.cerid-header__clear:hover {\n  background: var(--cerid-bg-hover);\n  color: var(--cerid-fg);\n}\n\n/* ============================================================\n   Messages scroll area\n   ============================================================ */\n\n.cerid-messages {\n  flex: 1;\n  overflow-y: auto;\n  padding: 16px;\n  display: flex;\n  flex-direction: column;\n  gap: 14px;\n  scroll-behavior: smooth;\n}\n\n.cerid-messages::-webkit-scrollbar {\n  width: 6px;\n}\n\n.cerid-messages::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.cerid-messages::-webkit-scrollbar-thumb {\n  background: var(--cerid-border);\n  border-radius: 3px;\n}\n\n/* ============================================================\n   Empty / error state\n   ============================================================ */\n\n.cerid-empty {\n  flex: 1;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  padding: 32px 16px;\n  text-align: center;\n  color: var(--cerid-fg-muted);\n  font-size: 14px;\n}\n\n.cerid-error-banner {\n  background: var(--cerid-badge-unverified-bg);\n  border: 1px solid var(--cerid-badge-unverified-border);\n  color: var(--cerid-badge-unverified-fg);\n  border-radius: 8px;\n  padding: 10px 14px;\n  font-size: 13px;\n  margin: 0 0 4px 0;\n}\n\n/* ============================================================\n   Message bubbles\n   ============================================================ */\n\n.cerid-message {\n  max-width: 86%;\n  display: flex;\n  flex-direction: column;\n  gap: 6px;\n}\n\n.cerid-message--user {\n  align-self: flex-end;\n}\n\n.cerid-message--assistant {\n  align-self: flex-start;\n}\n\n.cerid-message__bubble {\n  padding: 10px 14px;\n  border-radius: 16px;\n  word-break: break-word;\n  white-space: pre-wrap;\n  font-size: 14px;\n  line-height: 1.5;\n}\n\n.cerid-message--user .cerid-message__bubble {\n  background: var(--cerid-user-bg);\n  color: var(--cerid-user-fg);\n  border-bottom-right-radius: 4px;\n}\n\n.cerid-message--assistant .cerid-message__bubble {\n  background: var(--cerid-assistant-bg);\n  color: var(--cerid-assistant-fg);\n  border-bottom-left-radius: 4px;\n}\n\n/* Streaming cursor */\n.cerid-message--streaming .cerid-message__bubble::after {\n  content: "\\25CF";\n  animation: cerid-blink 1s step-start infinite;\n  margin-left: 3px;\n  color: var(--cerid-accent);\n}\n\n@keyframes cerid-blink {\n  50% { opacity: 0; }\n}\n\n/* Claims area below an assistant bubble */\n.cerid-message__claims {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 6px;\n  padding: 0 2px;\n}\n\n.cerid-message__claims--loading {\n  display: flex;\n  gap: 6px;\n  align-items: center;\n}\n\n/* Skeleton shimmer for claims loading state */\n.cerid-skeleton {\n  border-radius: 9999px;\n  background: linear-gradient(\n    90deg,\n    var(--cerid-bg-secondary) 25%,\n    var(--cerid-bg-hover) 50%,\n    var(--cerid-bg-secondary) 75%\n  );\n  background-size: 200% 100%;\n  animation: cerid-shimmer 1.4s ease-in-out infinite;\n}\n\n@keyframes cerid-shimmer {\n  0% { background-position: 200% 0; }\n  100% { background-position: -200% 0; }\n}\n\n/* ============================================================\n   Claim badges\n   ============================================================ */\n\n.cerid-badge-wrapper {\n  position: relative;\n  display: inline-block;\n}\n\n.cerid-badge {\n  display: inline-flex;\n  align-items: center;\n  gap: 4px;\n  padding: 2px 8px;\n  border-radius: 9999px;\n  border: 1px solid;\n  font-size: 11px;\n  font-weight: 500;\n  font-family: var(--cerid-font);\n  cursor: pointer;\n  transition: opacity var(--cerid-transition);\n  white-space: nowrap;\n}\n\n.cerid-badge:focus-visible {\n  outline: 2px solid var(--cerid-accent);\n  outline-offset: 2px;\n}\n\n.cerid-badge--verified {\n  background: var(--cerid-badge-verified-bg);\n  border-color: var(--cerid-badge-verified-border);\n  color: var(--cerid-badge-verified-fg);\n}\n\n.cerid-badge--partial {\n  background: var(--cerid-badge-partial-bg);\n  border-color: var(--cerid-badge-partial-border);\n  color: var(--cerid-badge-partial-fg);\n}\n\n.cerid-badge--unverified {\n  background: var(--cerid-badge-unverified-bg);\n  border-color: var(--cerid-badge-unverified-border);\n  color: var(--cerid-badge-unverified-fg);\n}\n\n.cerid-badge__icon {\n  width: 12px;\n  height: 12px;\n  flex-shrink: 0;\n  display: flex;\n  align-items: center;\n}\n\n.cerid-badge__icon svg {\n  width: 12px;\n  height: 12px;\n}\n\n/* Popover */\n.cerid-badge-popover {\n  position: absolute;\n  top: calc(100% + 6px);\n  left: 0;\n  z-index: 10;\n  width: 260px;\n  background: var(--cerid-bg);\n  border: 1px solid var(--cerid-border);\n  border-radius: 8px;\n  box-shadow: var(--cerid-shadow);\n  padding: 10px 12px;\n  font-size: 12px;\n  color: var(--cerid-fg);\n}\n\n.cerid-badge-popover[hidden] {\n  display: none;\n}\n\n.cerid-badge-popover__claim {\n  font-weight: 500;\n  margin: 0 0 6px 0;\n  line-height: 1.4;\n  color: var(--cerid-fg);\n}\n\n.cerid-badge-popover__reason {\n  color: var(--cerid-fg-muted);\n  margin: 0 0 4px 0;\n  font-size: 11px;\n}\n\n.cerid-badge-popover__confidence {\n  color: var(--cerid-fg-muted);\n  margin: 0 0 6px 0;\n  font-size: 11px;\n}\n\n.cerid-badge-popover__sources {\n  display: flex;\n  flex-direction: column;\n  gap: 3px;\n  border-top: 1px solid var(--cerid-border);\n  padding-top: 6px;\n}\n\n.cerid-badge-popover__source-file {\n  font-size: 11px;\n  color: var(--cerid-fg-muted);\n  font-style: italic;\n}\n\n.cerid-badge-popover__source-link {\n  display: inline-flex;\n  align-items: center;\n  gap: 4px;\n  font-size: 11px;\n  color: var(--cerid-accent);\n  text-decoration: none;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.cerid-badge-popover__source-link:hover {\n  text-decoration: underline;\n}\n\n.cerid-badge-popover__source-link-icon {\n  width: 10px;\n  height: 10px;\n  flex-shrink: 0;\n}\n\n.cerid-badge-popover__source-link-icon svg {\n  width: 10px;\n  height: 10px;\n}\n\n/* ============================================================\n   Input area\n   ============================================================ */\n\n.cerid-input-area {\n  display: flex;\n  align-items: flex-end;\n  gap: 8px;\n  padding: 12px 16px;\n  border-top: 1px solid var(--cerid-border);\n  background: var(--cerid-bg);\n  flex-shrink: 0;\n}\n\n.cerid-input {\n  flex: 1;\n  resize: none;\n  border: 1px solid var(--cerid-input-border);\n  border-radius: 10px;\n  padding: 9px 13px;\n  font-family: var(--cerid-font);\n  font-size: 14px;\n  line-height: 1.4;\n  background: var(--cerid-bg);\n  color: var(--cerid-fg);\n  outline: none;\n  min-height: 38px;\n  max-height: 120px;\n  transition: border-color var(--cerid-transition);\n  field-sizing: content;\n}\n\n.cerid-input::placeholder {\n  color: var(--cerid-fg-muted);\n}\n\n.cerid-input:focus {\n  border-color: var(--cerid-accent);\n}\n\n.cerid-send-btn {\n  width: 38px;\n  height: 38px;\n  border-radius: 10px;\n  background: var(--cerid-accent);\n  color: var(--cerid-accent-fg);\n  border: none;\n  cursor: pointer;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  flex-shrink: 0;\n  transition: background var(--cerid-transition), opacity var(--cerid-transition);\n  padding: 0;\n}\n\n.cerid-send-btn:hover:not(:disabled) {\n  background: var(--cerid-accent-hover);\n}\n\n.cerid-send-btn:disabled {\n  opacity: 0.45;\n  cursor: not-allowed;\n}\n\n.cerid-send-btn svg {\n  width: 17px;\n  height: 17px;\n}\n\n/* ============================================================\n   Footer\n   ============================================================ */\n\n.cerid-footer {\n  text-align: center;\n  padding: 5px 0;\n  font-size: 11px;\n  color: var(--cerid-fg-muted);\n  border-top: 1px solid var(--cerid-border);\n  flex-shrink: 0;\n}\n\n.cerid-footer a {\n  color: var(--cerid-accent);\n  text-decoration: none;\n}\n\n.cerid-footer a:hover {\n  text-decoration: underline;\n}\n';
function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = Math.random() * 16 | 0;
    return (c === "x" ? r : r & 3 | 8).toString(16);
  });
}
class CeridChat extends HTMLElement {
  static observedAttributes = [
    "host",
    "token",
    "placeholder",
    "theme",
    "max-claims"
  ];
  // Shadow DOM root
  shadow;
  // DOM refs (populated in connectedCallback)
  messagesEl;
  inputEl;
  sendBtn;
  // State
  messages = [];
  abortController = null;
  mediaQuery = null;
  mqListener = null;
  constructor() {
    super();
    this.shadow = this.attachShadow({ mode: "open" });
  }
  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------
  connectedCallback() {
    this.render();
    this.applyTheme();
  }
  disconnectedCallback() {
    this.abortController?.abort();
    if (this.mqListener && this.mediaQuery) {
      this.mediaQuery.removeEventListener("change", this.mqListener);
    }
  }
  attributeChangedCallback(name, oldVal, newVal) {
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
  get host() {
    return this.getAttribute("host") ?? "";
  }
  get token() {
    return this.getAttribute("token") ?? void 0;
  }
  get placeholder() {
    return this.getAttribute("placeholder") ?? "Ask Cerid anything";
  }
  get theme() {
    const val = this.getAttribute("theme");
    if (val === "dark" || val === "light" || val === "auto") return val;
    return "auto";
  }
  get maxClaims() {
    const val = parseInt(this.getAttribute("max-claims") ?? "50", 10);
    return isNaN(val) || val < 1 ? 50 : Math.min(val, 200);
  }
  // ---------------------------------------------------------------------------
  // Initial render (called once in connectedCallback)
  // ---------------------------------------------------------------------------
  render() {
    const styleEl = document.createElement("style");
    styleEl.textContent = rawStyles;
    this.shadow.appendChild(styleEl);
    const panel = document.createElement("div");
    panel.className = "cerid-panel";
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "Cerid AI Chat");
    panel.appendChild(this.buildHeader());
    this.messagesEl = document.createElement("div");
    this.messagesEl.className = "cerid-messages";
    this.messagesEl.setAttribute("role", "log");
    this.messagesEl.setAttribute("aria-live", "polite");
    this.messagesEl.setAttribute("aria-label", "Chat messages");
    panel.appendChild(this.messagesEl);
    if (!this.host) {
      this.showErrorBanner(
        'Missing required attribute: host. Add host="https://your-cerid-server" to the element.'
      );
    } else {
      this.renderEmptyState();
    }
    panel.appendChild(this.buildInputArea());
    panel.appendChild(this.buildFooter());
    this.shadow.appendChild(panel);
  }
  buildHeader() {
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
  buildInputArea() {
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
    this.inputEl.addEventListener("keydown", (e) => {
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
    this.inputEl.addEventListener("input", () => {
      this.sendBtn.disabled = !this.inputEl.value.trim() || !this.host;
    });
    if (!this.host) {
      this.inputEl.disabled = true;
      this.sendBtn.disabled = true;
    }
    return form;
  }
  buildFooter() {
    const footer = document.createElement("div");
    footer.className = "cerid-footer";
    footer.innerHTML = `Powered by <a href="https://cerid.ai" target="_blank" rel="noopener noreferrer">Cerid AI</a>`;
    return footer;
  }
  // ---------------------------------------------------------------------------
  // Theme
  // ---------------------------------------------------------------------------
  applyTheme() {
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
      this.mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      const update = (e) => {
        if (e.matches) {
          this.classList.add("cerid-dark");
        } else {
          this.classList.remove("cerid-dark");
        }
      };
      update(this.mediaQuery);
      this.mqListener = update;
      this.mediaQuery.addEventListener("change", this.mqListener);
    }
  }
  // ---------------------------------------------------------------------------
  // Message rendering
  // ---------------------------------------------------------------------------
  renderEmptyState() {
    const empty = document.createElement("div");
    empty.className = "cerid-empty";
    empty.setAttribute("role", "status");
    empty.textContent = "Ask Cerid anything";
    this.messagesEl.appendChild(empty);
  }
  showErrorBanner(text) {
    const banner = document.createElement("div");
    banner.className = "cerid-error-banner";
    banner.setAttribute("role", "alert");
    banner.textContent = text;
    this.messagesEl.appendChild(banner);
  }
  appendMessage(msg) {
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
  updateMessageBubble(id, content, streaming) {
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
  renderClaims(msgId, claims) {
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
  scrollToBottom() {
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }
  // ---------------------------------------------------------------------------
  // Submit / send
  // ---------------------------------------------------------------------------
  async handleSubmit() {
    const query = this.inputEl.value.trim();
    if (!query || !this.host) return;
    this.abortController?.abort();
    this.abortController = new AbortController();
    this.inputEl.value = "";
    this.sendBtn.disabled = true;
    this.inputEl.disabled = true;
    const userMsg = {
      id: generateId(),
      role: "user",
      content: query,
      timestamp: Date.now()
    };
    this.messages.push(userMsg);
    this.appendMessage(userMsg);
    const assistantMsg = {
      id: generateId(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      streaming: true
    };
    this.messages.push(assistantMsg);
    const assistantEl = this.appendMessage(assistantMsg);
    const bubble = assistantEl.querySelector(".cerid-message__bubble");
    if (bubble) {
      bubble.textContent = "";
    }
    try {
      const conversationMessages = this.messages.slice(0, -1).map((m) => ({ role: m.role, content: m.content }));
      const result = await fetchQuery(
        this.host,
        {
          query,
          conversation_messages: conversationMessages.slice(-20)
          // Last 20 for context
        },
        {
          signal: this.abortController.signal,
          token: this.token
        }
      );
      const responseText = result.answer ?? result.context ?? "";
      assistantMsg.content = responseText;
      assistantMsg.streaming = false;
      assistantMsg.claims = result.claims;
      this.updateMessageBubble(assistantMsg.id, responseText, false);
      if (result.claims?.length) {
        this.renderClaims(assistantMsg.id, result.claims);
      }
      if (false) ;
    } catch (err) {
      assistantMsg.streaming = false;
      if (err instanceof DOMException && err.name === "AbortError") {
        this.updateMessageBubble(assistantMsg.id, "", false);
        return;
      }
      const text = err instanceof ApiError ? `Error ${err.status}: ${err.message}` : "Something went wrong. Please try again.";
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
  clearConversation() {
    this.messages = [];
    this.messagesEl.innerHTML = "";
    this.renderEmptyState();
    this.inputEl.value = "";
    this.sendBtn.disabled = true;
    this.inputEl.focus();
  }
}
const ceridChat = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  CeridChat
}, Symbol.toStringTag, { value: "Module" }));
if (typeof window !== "undefined" && typeof customElements !== "undefined") {
  if (!customElements.get("cerid-chat")) {
    Promise.resolve().then(() => ceridChat).then(({ CeridChat: CeridChat2 }) => {
      customElements.define("cerid-chat", CeridChat2);
    });
  }
}
export {
  ApiError,
  CeridChat,
  deriveBand,
  fetchQuery,
  sourceCount
};
