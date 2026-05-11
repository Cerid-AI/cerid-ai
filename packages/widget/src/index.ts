// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * @cerid/widget — entry point.
 *
 * Auto-registers the <cerid-chat> custom element on import.
 * For CDN (IIFE) builds this fires on script load.
 * For ESM/CJS library builds, consumers get the class for manual registration.
 */

export { CeridChat } from "./cerid-chat.js";
export { fetchQuery, ApiError } from "./api.js";
export { deriveBand, sourceCount } from "./types.js";
export type {
  SDKQueryRequest,
  SDKQueryResponse,
  SDKSourceChunk,
  ClaimVerification,
  ClaimStatus,
  ClaimType,
  VerificationBand,
  ChatMessage,
  WidgetTheme,
} from "./types.js";

// Auto-register the custom element if running in a browser context
if (typeof window !== "undefined" && typeof customElements !== "undefined") {
  if (!customElements.get("cerid-chat")) {
    import("./cerid-chat.js").then(({ CeridChat }) => {
      customElements.define("cerid-chat", CeridChat);
    });
  }
}
