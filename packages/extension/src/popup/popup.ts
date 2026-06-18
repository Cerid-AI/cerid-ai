// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Cerid AI extension popup (Phase 4b B4b.2).
 *
 * Two buttons: Save Page (page-capture → /sdk/v1/ingest) and Open Cerid
 * (deep-link to localhost). Reads the configured base URL from
 * chrome.storage.local; defaults to http://localhost:8888.
 */

const CERID_BASE_KEY = "cerid_base_url"
const DEFAULT_BASE = "http://localhost:8888"

const $ = <T extends Element>(sel: string) =>
  document.querySelector(sel) as T | null

async function getBaseUrl(): Promise<string> {
  const stored = await chrome.storage.local.get(CERID_BASE_KEY)
  return stored[CERID_BASE_KEY] || DEFAULT_BASE
}

function showStatus(message: string, kind: "ok" | "err" | "info" = "info") {
  const el = $<HTMLDivElement>("#status")
  if (!el) return
  el.textContent = message
  el.className = "status " + (kind === "ok" ? "ok" : kind === "err" ? "err" : "")
}

async function captureCurrentTab() {
  const captureBtn = $<HTMLButtonElement>("#capture")
  if (captureBtn) captureBtn.disabled = true

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab?.id || !tab.url) {
      showStatus("No active tab", "err")
      return
    }
    if (!/^https?:/.test(tab.url)) {
      showStatus("Cerid can only capture http/https pages", "err")
      return
    }

    showStatus("Extracting…")
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractReadable,
    })
    if (!result || typeof result !== "object") {
      showStatus("Extraction failed", "err")
      return
    }
    const { title, content, url } = result as { title: string; content: string; url: string }
    if (!content?.trim()) {
      showStatus("No readable content found", "err")
      return
    }

    showStatus("Sending…")
    const baseUrl = await getBaseUrl()
    const resp = await fetch(`${baseUrl}/sdk/v1/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        kind: "external_adapter",
        domain: "general",
        metadata: {
          title,
          url,
          ingest_source: "browser_extension",
        },
      }),
    })
    if (!resp.ok) {
      showStatus(`Save failed: HTTP ${resp.status}`, "err")
      return
    }
    showStatus("Saved to Cerid ✓", "ok")
  } catch (exc) {
    showStatus(exc instanceof Error ? exc.message : "Unknown error", "err")
  } finally {
    if (captureBtn) captureBtn.disabled = false
  }
}

/**
 * Injected into the active tab. Pulls a clean(ish) readable view of
 * the page: title, canonical URL, and main text content. We avoid a
 * full Readability library to keep the extension under the
 * inline-script-size budget; the heuristic here mirrors what mainstream
 * readers do: prefer <main>, then <article>, then <body>; strip scripts,
 * styles, nav, footer, aside.
 */
function extractReadable() {
  function strip(text: string): string {
    return text.replace(/\s+/g, " ").trim()
  }
  function visibleText(root: Element): string {
    const clone = root.cloneNode(true) as Element
    clone
      .querySelectorAll("script, style, noscript, nav, footer, aside, iframe, svg")
      .forEach((n) => n.remove())
    return strip(clone.textContent ?? "")
  }

  const root =
    document.querySelector("main") ??
    document.querySelector("article") ??
    document.body

  const canonical =
    document.querySelector<HTMLLinkElement>("link[rel=canonical]")?.href ??
    location.href
  return {
    title: document.title || canonical,
    content: visibleText(root).slice(0, 100_000),
    url: canonical,
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#capture")?.addEventListener("click", captureCurrentTab)
  $("#open-cerid")?.addEventListener("click", async () => {
    const baseUrl = await getBaseUrl()
    chrome.tabs.create({ url: baseUrl })
  })
})
