// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react";
import { CeridWidget } from "./CeridWidget";
import type { CeridWidgetConfig } from "./types";
import { DEFAULT_CONFIG } from "./types";

// ---------------------------------------------------------------------------
// SVG icons
// ---------------------------------------------------------------------------

const ChatIcon = () => (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z" />
    <path d="M7 9h10v2H7zm0-3h10v2H7z" />
  </svg>
);

const CloseIcon = () => (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
  </svg>
);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface CeridBubbleProps {
  config: CeridWidgetConfig;
}

/**
 * Floating bubble button that toggles the chat panel open/closed.
 *
 * Renders both the bubble and (when open) the CeridWidget panel,
 * positioned according to `config.position`.
 */
export function CeridBubble({ config }: CeridBubbleProps) {
  const [open, setOpen] = useState(false);
  const position = config.position ?? DEFAULT_CONFIG.position;

  const toggle = useCallback(() => setOpen((prev) => !prev), []);
  const close = useCallback(() => setOpen(false), []);

  // Resolve theme class
  const [themeClass, setThemeClass] = useState("");
  const theme = config.theme ?? DEFAULT_CONFIG.theme;

  useEffect(() => {
    if (theme === "dark") {
      setThemeClass("cerid-dark");
    } else if (theme === "light") {
      setThemeClass("");
    } else {
      // "auto": follow system preference
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const update = (e: MediaQueryListEvent | MediaQueryList) => {
        setThemeClass(e.matches ? "cerid-dark" : "");
      };
      update(mq);
      mq.addEventListener("change", update);
      return () => mq.removeEventListener("change", update);
    }
  }, [theme]);

  return (
    <div className={`cerid-widget-container ${position} ${themeClass}`}>
      {open && <CeridWidget config={config} onClose={close} />}
      <button
        className="cerid-bubble"
        onClick={toggle}
        type="button"
        aria-label={open ? "Close chat" : "Open chat"}
      >
        {open ? <CloseIcon /> : <ChatIcon />}
      </button>
    </div>
  );
}
