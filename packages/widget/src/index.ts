// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Cerid AI Chat Widget — Entry Point
 *
 * This module supports three usage modes:
 *
 * 1. **Script tag** — Auto-initializes from `data-cerid-url` and `data-client-id`
 *    attributes on the `<script>` tag, renders into a Shadow DOM container.
 *
 * 2. **iframe** — The backend serves `/widget.html` which loads this script in
 *    full-page mode.
 *
 * 3. **React component** — Import `CeridWidget`, `CeridBubble`, or `CeridChatAPI`
 *    from `@cerid-ai/widget` for programmatic use.
 */

import type { CeridWidgetConfig } from "./types";

// ---------------------------------------------------------------------------
// Re-exports for npm / React component usage
// ---------------------------------------------------------------------------

export { CeridWidget } from "./CeridWidget";
export type { CeridWidgetProps } from "./CeridWidget";
export { CeridBubble } from "./CeridBubble";
export type { CeridBubbleProps } from "./CeridBubble";
export { CeridChatAPI } from "./api";
export type { CeridWidgetConfig, ChatMessage, SSEChunk } from "./types";
export { DEFAULT_CONFIG } from "./types";

// ---------------------------------------------------------------------------
// Script-tag auto-initialization
// ---------------------------------------------------------------------------

/** Injected stylesheet content (bundled at build time by vite-plugin-css-injected-by-js). */
import styles from "./styles.css?inline";

/**
 * Detect the current `<script>` tag's data attributes and auto-mount the
 * widget into a Shadow DOM container.
 */
function autoInit(): void {
  // Find our script tag
  const scripts = document.querySelectorAll("script[data-cerid-url]");
  const scriptTag = scripts[scripts.length - 1] as HTMLScriptElement | undefined;
  if (!scriptTag) return;

  const apiUrl = scriptTag.getAttribute("data-cerid-url");
  const clientId = scriptTag.getAttribute("data-client-id");
  if (!apiUrl || !clientId) {
    console.warn(
      "[cerid-widget] Missing required attributes: data-cerid-url, data-client-id",
    );
    return;
  }

  const config: CeridWidgetConfig = {
    apiUrl,
    clientId,
    apiKey: scriptTag.getAttribute("data-api-key") ?? undefined,
    position:
      (scriptTag.getAttribute("data-position") as CeridWidgetConfig["position"]) ??
      undefined,
    theme:
      (scriptTag.getAttribute("data-theme") as CeridWidgetConfig["theme"]) ??
      undefined,
    title: scriptTag.getAttribute("data-title") ?? undefined,
    placeholder: scriptTag.getAttribute("data-placeholder") ?? undefined,
    initialMessage: scriptTag.getAttribute("data-initial-message") ?? undefined,
  };

  // Also check for a global config object
  const globalConfig =
    (window as Record<string, unknown>).ceridChatConfig as
      | Partial<CeridWidgetConfig>
      | undefined;
  if (globalConfig) {
    Object.assign(config, globalConfig);
  }

  mountWidget(config);
}

/**
 * Mount the widget into the DOM using Shadow DOM for style isolation.
 */
function mountWidget(config: CeridWidgetConfig): void {
  // Create host element
  const host = document.createElement("div");
  host.id = "cerid-widget-host";
  host.style.cssText =
    "position:fixed;z-index:2147483647;top:0;left:0;width:0;height:0;overflow:visible;pointer-events:none;";
  document.body.appendChild(host);

  // Attach shadow DOM
  const shadow = host.attachShadow({ mode: "open" });

  // Inject styles
  const styleEl = document.createElement("style");
  styleEl.textContent = styles;
  shadow.appendChild(styleEl);

  // Create a mount point inside shadow
  const mountPoint = document.createElement("div");
  mountPoint.style.pointerEvents = "auto";
  shadow.appendChild(mountPoint);

  // Apply theme class to shadow host
  const theme = config.theme ?? "auto";
  if (theme === "dark") {
    shadow.host.classList.add("cerid-dark");
  } else if (theme === "auto") {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const update = (e: MediaQueryListEvent | MediaQueryList) => {
      if (e.matches) {
        shadow.host.classList.add("cerid-dark");
      } else {
        shadow.host.classList.remove("cerid-dark");
      }
    };
    update(mq);
    mq.addEventListener("change", update);
  }

  // Dynamically import React to avoid hard dependency for script-tag users.
  // When used as a script tag on a page without React, we inline a minimal
  // vanilla renderer. For the npm package, React is a peer dependency.
  renderWithReact(mountPoint, config);
}

/**
 * Render using React (expected to be available when the npm package is used,
 * or bundled with the widget JS for script-tag mode).
 */
function renderWithReact(
  container: HTMLElement,
  config: CeridWidgetConfig,
): void {
  // These are bundled into the widget JS at build time
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require("react");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const ReactDOM = require("react-dom/client");
  const { CeridBubble } = require("./CeridBubble");

  const root = ReactDOM.createRoot(container);
  root.render(React.createElement(CeridBubble, { config }));
}

// ---------------------------------------------------------------------------
// Check if we're in a full-page (iframe) context
// ---------------------------------------------------------------------------

function isFullPage(): boolean {
  return (
    typeof (window as Record<string, unknown>).__CERID_WIDGET_FULLPAGE__ ===
    "boolean"
  );
}

/**
 * For iframe mode: renders the widget as a full-page chat interface.
 * The backend sets `window.__CERID_WIDGET_FULLPAGE__ = true` and
 * `window.ceridChatConfig = { ... }`.
 */
function mountFullPage(): void {
  const config = (window as Record<string, unknown>).ceridChatConfig as
    | CeridWidgetConfig
    | undefined;
  if (!config?.apiUrl || !config?.clientId) {
    console.error(
      "[cerid-widget] Full-page mode requires window.ceridChatConfig with apiUrl and clientId.",
    );
    return;
  }

  const container = document.getElementById("cerid-widget-root");
  if (!container) {
    console.error('[cerid-widget] Missing element with id="cerid-widget-root".');
    return;
  }

  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const React = require("react");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const ReactDOM = require("react-dom/client");
  const { CeridWidget } = require("./CeridWidget");

  const root = ReactDOM.createRoot(container);
  root.render(React.createElement(CeridWidget, { config, fullPage: true }));
}

// ---------------------------------------------------------------------------
// Auto-init on DOM ready
// ---------------------------------------------------------------------------

if (typeof window !== "undefined") {
  const init = () => {
    if (isFullPage()) {
      mountFullPage();
    } else {
      autoInit();
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}
