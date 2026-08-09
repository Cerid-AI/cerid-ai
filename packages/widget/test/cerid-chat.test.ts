// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for <cerid-chat> web component.
 *
 * Environment: Vitest with jsdom.
 * Axe: jest-axe for accessibility assertions against shadow DOM content.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { axe, toHaveNoViolations } from "jest-axe";
import { CeridChat } from "../src/cerid-chat.js";
import type { SDKQueryResponse } from "../src/types.js";

// Extend vitest's expect with jest-axe matchers
expect.extend(toHaveNoViolations);

// ---------------------------------------------------------------------------
// Module-level mock for CSS ?inline import
// ---------------------------------------------------------------------------

vi.mock("../src/styles.css?inline", () => ({
  default: ":host { color: red; }",
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TAG = "cerid-chat";

function makeOkResponse(body: SDKQueryResponse): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const QUERY_RESPONSE_WITH_CLAIMS: SDKQueryResponse = {
  context: "RAG context text",
  answer: "This is the assistant answer.",
  sources: [],
  confidence: 0.85,
  domains_searched: ["finance"],
  total_results: 2,
  token_budget_used: 200,
  graph_results: 0,
  results: [],
  claims: [
    {
      claim: "The sky is blue.",
      status: "verified",
      confidence: 0.95,
      source_artifact_id: "doc-001",
      source_filename: "sky-facts.pdf",
    },
    {
      claim: "Water is wet.",
      status: "uncertain",
      confidence: 0.6,
      reason: "No direct source found.",
    },
    {
      claim: "The moon is made of cheese.",
      status: "unverified",
      confidence: 0.1,
    },
  ],
};

const QUERY_RESPONSE_MINIMAL: SDKQueryResponse = {
  context: "Some context",
  answer: "Answer text",
  sources: [],
  confidence: 0.7,
  domains_searched: [],
  total_results: 0,
  token_budget_used: 0,
  graph_results: 0,
  results: [],
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

function registerElement(): void {
  if (!customElements.get(TAG)) {
    customElements.define(TAG, CeridChat);
  }
}

function createElement(attrs: Record<string, string> = {}): CeridChat {
  const el = document.createElement(TAG) as CeridChat;
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v);
  }
  document.body.appendChild(el);
  return el;
}

function getShadowEl(el: CeridChat, selector: string): Element | null {
  return el.shadowRoot?.querySelector(selector) ?? null;
}

function getShadowEls(el: CeridChat, selector: string): Element[] {
  return Array.from(el.shadowRoot?.querySelectorAll(selector) ?? []);
}

async function waitFor(
  fn: () => boolean,
  timeout = 2000,
  interval = 30,
): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (fn()) return;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("waitFor timed out");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("<cerid-chat>", () => {
  beforeEach(() => {
    registerElement();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    // Clean up DOM
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------
  // Registration
  // -------------------------------------------------------------------------

  describe("registration", () => {
    it("registers as cerid-chat custom element", () => {
      expect(customElements.get(TAG)).toBeDefined();
    });

    it("creates a shadow root in open mode", () => {
      const el = createElement({ host: "http://localhost:8000" });
      expect(el.shadowRoot).not.toBeNull();
      expect(el.shadowRoot?.mode).toBe("open");
    });
  });

  // -------------------------------------------------------------------------
  // Empty state (with valid host)
  // -------------------------------------------------------------------------

  describe("empty state", () => {
    it("shows 'Ask Cerid anything' empty state when host is provided", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const empty = getShadowEl(el, ".cerid-empty");
      expect(empty).not.toBeNull();
      expect(empty?.textContent).toContain("Ask Cerid anything");
    });

    it("uses custom placeholder when placeholder attribute is set", () => {
      const el = createElement({
        host: "http://localhost:8000",
        placeholder: "Ask me anything about docs",
      });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      expect(input?.placeholder).toBe("Ask me anything about docs");
    });
  });

  // -------------------------------------------------------------------------
  // Error state (missing host)
  // -------------------------------------------------------------------------

  describe("missing host attribute", () => {
    it("renders an error banner when host is missing", () => {
      const el = createElement({});
      const banner = getShadowEl(el, ".cerid-error-banner");
      expect(banner).not.toBeNull();
      expect(banner?.textContent).toContain("host");
    });

    it("disables the input when host is missing", () => {
      const el = createElement({});
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      expect(input?.disabled).toBe(true);
    });

    it("disables the send button when host is missing", () => {
      const el = createElement({});
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;
      expect(btn?.disabled).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // Input + submit
  // -------------------------------------------------------------------------

  describe("input and submit", () => {
    it("send button is disabled when input is empty", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
    });

    it("send button enables when input has text", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "hello";
      input.dispatchEvent(new Event("input"));

      expect(btn.disabled).toBe(false);
    });

    it("POSTs to /sdk/v1/query with correct body on submit", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(QUERY_RESPONSE_MINIMAL));

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "What is RAG?";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(() => vi.mocked(fetch).mock.calls.length > 0);

      const [url, init] = vi.mocked(fetch).mock.calls[0]!;
      expect(url).toBe("http://localhost:8000/sdk/v1/query");
      expect(init?.method).toBe("POST");

      const body = JSON.parse(init?.body as string) as { query: string };
      expect(body.query).toBe("What is RAG?");
    });

    it("clears input after submit", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(QUERY_RESPONSE_MINIMAL));

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Test query";
      input.dispatchEvent(new Event("input"));
      btn.click();

      // Input should be cleared immediately
      expect(input.value).toBe("");
    });

    it("Enter key submits the form", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(QUERY_RESPONSE_MINIMAL));

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;

      input.value = "Enter key test";
      input.dispatchEvent(new Event("input"));
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

      await waitFor(() => vi.mocked(fetch).mock.calls.length > 0);

      expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("Shift+Enter does NOT submit", () => {
      vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(QUERY_RESPONSE_MINIMAL));

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;

      input.value = "Shift enter";
      input.dispatchEvent(new Event("input"));
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }),
      );

      expect(fetch).not.toHaveBeenCalled();
    });
  });

  // -------------------------------------------------------------------------
  // Claim badge rendering
  // -------------------------------------------------------------------------

  describe("claim badge rendering", () => {
    it("renders a verified badge for a verified claim with a source", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(
        makeOkResponse(QUERY_RESPONSE_WITH_CLAIMS),
      );

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Tell me about the sky";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(
        () => getShadowEls(el, "[data-band]").length >= 3,
        3000,
      );

      const badges = getShadowEls(el, "[data-band]");
      const bands = badges.map((b) => b.getAttribute("data-band"));
      expect(bands).toContain("verified");
      expect(bands).toContain("partial");
      expect(bands).toContain("unverified");
    });

    it("renders all three band variants", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(
        makeOkResponse(QUERY_RESPONSE_WITH_CLAIMS),
      );

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Claims test";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(() => getShadowEls(el, ".cerid-badge--verified").length > 0, 3000);

      expect(getShadowEls(el, ".cerid-badge--verified")).toHaveLength(1);
      expect(getShadowEls(el, ".cerid-badge--partial")).toHaveLength(1);
      expect(getShadowEls(el, ".cerid-badge--unverified")).toHaveLength(1);
    });

    it("respects max-claims attribute", async () => {
      // Create a response with 5 claims
      const manyClaims: SDKQueryResponse = {
        ...QUERY_RESPONSE_MINIMAL,
        claims: Array.from({ length: 5 }, (_, i) => ({
          claim: `Claim ${i}`,
          status: "unverified" as const,
          confidence: 0.1,
        })),
      };

      vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(manyClaims));

      const el = createElement({
        host: "http://localhost:8000",
        "max-claims": "2",
      });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Many claims";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(() => getShadowEls(el, "[data-band]").length > 0, 3000);

      // max-claims=2 → only 2 badges
      const badges = getShadowEls(el, "[data-band]");
      expect(badges.length).toBeLessThanOrEqual(2);
    });
  });

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  describe("loading state", () => {
    it("disables input while a request is in flight", async () => {
      // Delay the fetch response so we can check intermediate state
      let resolveFetch: (r: Response) => void;
      const fetchPromise = new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      });
      vi.mocked(fetch).mockReturnValueOnce(fetchPromise);

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Loading test";
      input.dispatchEvent(new Event("input"));
      btn.click();

      // Input should be disabled while loading
      await waitFor(() => input.disabled, 500);
      expect(input.disabled).toBe(true);

      // Resolve the fetch
      resolveFetch!(makeOkResponse(QUERY_RESPONSE_MINIMAL));
      await waitFor(() => !input.disabled, 2000);
      expect(input.disabled).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------------

  describe("error state", () => {
    it("shows error text in the assistant bubble on API failure", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(null, { status: 500, statusText: "Internal Server Error" }),
      );

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Error test";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(() => {
        const bubbles = getShadowEls(el, ".cerid-message--assistant .cerid-message__bubble");
        return bubbles.some((b) => b.textContent?.includes("Error") || b.textContent?.includes("wrong"));
      }, 3000);

      const bubbles = getShadowEls(el, ".cerid-message--assistant .cerid-message__bubble");
      const errorBubble = bubbles.find(
        (b) => b.textContent?.includes("Error") || b.textContent?.includes("wrong"),
      );
      expect(errorBubble).toBeDefined();
    });

    it("re-enables input after error", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(null, { status: 500, statusText: "Server Error" }),
      );

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Recover test";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(() => !input.disabled, 3000);
      expect(input.disabled).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Clear conversation
  // -------------------------------------------------------------------------

  describe("clear conversation", () => {
    it("clears messages and restores empty state", async () => {
      vi.mocked(fetch).mockResolvedValueOnce(makeOkResponse(QUERY_RESPONSE_MINIMAL));

      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input") as HTMLTextAreaElement;
      const btn = getShadowEl(el, ".cerid-send-btn") as HTMLButtonElement;

      input.value = "Hello";
      input.dispatchEvent(new Event("input"));
      btn.click();

      await waitFor(() => getShadowEls(el, ".cerid-message").length > 0, 3000);

      // Click clear
      const clearBtn = getShadowEl(el, ".cerid-header__clear") as HTMLButtonElement;
      clearBtn.click();

      expect(getShadowEls(el, ".cerid-message")).toHaveLength(0);
      expect(getShadowEl(el, ".cerid-empty")).not.toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Shadow DOM mode
  // -------------------------------------------------------------------------

  describe("shadow DOM", () => {
    it("is open mode (accessible via shadowRoot)", () => {
      const el = createElement({ host: "http://localhost:8000" });
      expect(el.shadowRoot?.mode).toBe("open");
    });

    it("isolates styles inside shadow root", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const styleEl = el.shadowRoot?.querySelector("style");
      expect(styleEl).not.toBeNull();
      // The injected style should contain our CSS (mocked to :host { color: red; })
      expect(styleEl?.textContent).toContain(":host");
    });
  });

  // -------------------------------------------------------------------------
  // Accessibility (axe)
  // -------------------------------------------------------------------------

  describe("accessibility", () => {
    it("shadow DOM content is axe-clean (empty state)", async () => {
      const el = createElement({ host: "http://localhost:8000" });

      // axe only runs against the light DOM; we surface the shadow content
      // by extracting innerHTML from the shadow root for the axe run.
      // This is the accepted approach for jsdom + jest-axe.
      const shadowHtml = el.shadowRoot?.innerHTML ?? "";
      const container = document.createElement("div");
      container.innerHTML = shadowHtml;
      document.body.appendChild(container);

      const results = await axe(container);
      expect(results).toHaveNoViolations();

      container.remove();
    });

    it("messages area has correct ARIA roles", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const messages = getShadowEl(el, ".cerid-messages");
      expect(messages?.getAttribute("role")).toBe("log");
      expect(messages?.getAttribute("aria-live")).toBe("polite");
    });

    it("send button has an aria-label", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const btn = getShadowEl(el, ".cerid-send-btn");
      expect(btn?.getAttribute("aria-label")).toBeTruthy();
    });

    it("input has an aria-label", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const input = getShadowEl(el, ".cerid-input");
      expect(input?.getAttribute("aria-label")).toBeTruthy();
    });

    it("panel has role=region and aria-label", () => {
      const el = createElement({ host: "http://localhost:8000" });
      const panel = getShadowEl(el, ".cerid-panel");
      expect(panel?.getAttribute("role")).toBe("region");
      expect(panel?.getAttribute("aria-label")).toBeTruthy();
    });
  });
});
