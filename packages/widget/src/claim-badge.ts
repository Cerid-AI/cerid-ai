// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vanilla DOM claim badge renderer.
 *
 * Mirrors the V.1 ClaimBadge visual idiom (verified/partial/unverified)
 * without React. Uses inline SVG icons and CSS custom properties.
 *
 * Three bands (matching src/web/src/components/verification/claim-badge.tsx):
 *   verified   → green / CheckCircle
 *   partial    → amber / Minus
 *   unverified → red   / CircleDot
 */

import {
  ICON_CHECK_CIRCLE,
  ICON_MINUS,
  ICON_CIRCLE_DOT,
  ICON_EXTERNAL_LINK,
} from "./icons.js";
import {
  deriveBand,
  sourceCount,
  type ClaimVerification,
  type VerificationBand,
} from "./types.js";

interface BandConfig {
  label: (n: number) => string;
  ariaLabel: (n: number) => string;
  icon: string;
  cssClass: string;
}

const BAND_CONFIG: Record<VerificationBand, BandConfig> = {
  verified: {
    label: (n) => (n > 0 ? `Verified · ${n} source${n > 1 ? "s" : ""}` : "Verified"),
    ariaLabel: (n) =>
      n > 0
        ? `Claim verified with ${n} source${n > 1 ? "s" : ""}`
        : "Claim verified",
    icon: ICON_CHECK_CIRCLE,
    cssClass: "cerid-badge--verified",
  },
  partial: {
    label: () => "Partial",
    ariaLabel: () => "Claim partially verified or uncertain",
    icon: ICON_MINUS,
    cssClass: "cerid-badge--partial",
  },
  unverified: {
    label: () => "Unverified",
    ariaLabel: () => "Claim unverified — no supporting source found",
    icon: ICON_CIRCLE_DOT,
    cssClass: "cerid-badge--unverified",
  },
};

/**
 * Create a DOM node for a single claim badge.
 * The returned element is a <span> wrapping the icon + label text.
 * On click, a small detail popover is toggled below the badge.
 */
export function createClaimBadge(claim: ClaimVerification): HTMLElement {
  const band = deriveBand(claim);
  const n = sourceCount(claim);
  const cfg = BAND_CONFIG[band];

  // Outer wrapper (acts as the popover anchor)
  const wrapper = document.createElement("span");
  wrapper.className = "cerid-badge-wrapper";

  // The badge pill
  const badge = document.createElement("button");
  badge.type = "button";
  badge.className = `cerid-badge ${cfg.cssClass}`;
  badge.setAttribute("aria-label", cfg.ariaLabel(n));
  badge.setAttribute("data-band", band);
  badge.setAttribute("aria-haspopup", "true");
  badge.setAttribute("aria-expanded", "false");

  // Icon
  const iconSpan = document.createElement("span");
  iconSpan.className = "cerid-badge__icon";
  iconSpan.innerHTML = cfg.icon;
  badge.appendChild(iconSpan);

  // Label
  const labelSpan = document.createElement("span");
  labelSpan.className = "cerid-badge__label";
  labelSpan.textContent = cfg.label(n);
  badge.appendChild(labelSpan);

  wrapper.appendChild(badge);

  // Popover (lazy-created on first open)
  let popover: HTMLElement | null = null;
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

  // Close on Escape
  badge.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Escape" && open) {
      open = false;
      badge.setAttribute("aria-expanded", "false");
      popover?.setAttribute("hidden", "");
    }
  });

  return wrapper;
}

function createPopover(
  claim: ClaimVerification,
  band: VerificationBand,
  n: number,
): HTMLElement {
  const popover = document.createElement("div");
  popover.className = `cerid-badge-popover cerid-badge-popover--${band}`;
  popover.setAttribute("role", "tooltip");

  // Claim text
  const claimEl = document.createElement("p");
  claimEl.className = "cerid-badge-popover__claim";
  claimEl.textContent = claim.claim;
  popover.appendChild(claimEl);

  // Reason / confidence
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

  // Sources
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
