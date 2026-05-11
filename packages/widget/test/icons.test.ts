// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import {
  ICON_CHECK_CIRCLE,
  ICON_MINUS,
  ICON_CIRCLE_DOT,
  ICON_SEND,
  ICON_EXTERNAL_LINK,
} from "../src/icons.js";

const ALL_ICONS = {
  ICON_CHECK_CIRCLE,
  ICON_MINUS,
  ICON_CIRCLE_DOT,
  ICON_SEND,
  ICON_EXTERNAL_LINK,
};

describe("icons", () => {
  for (const [name, svg] of Object.entries(ALL_ICONS)) {
    it(`${name} is a non-empty string`, () => {
      expect(typeof svg).toBe("string");
      expect(svg.length).toBeGreaterThan(10);
    });

    it(`${name} starts with <svg`, () => {
      expect(svg.trimStart()).toMatch(/^<svg/);
    });

    it(`${name} ends with </svg>`, () => {
      expect(svg.trimEnd()).toMatch(/<\/svg>$/);
    });

    it(`${name} has xmlns attribute`, () => {
      expect(svg).toContain("xmlns=");
    });

    it(`${name} has aria-hidden="true"`, () => {
      expect(svg).toContain('aria-hidden="true"');
    });

    it(`${name} has viewBox attribute`, () => {
      expect(svg).toContain("viewBox=");
    });
  }

  it("exports exactly 5 icons", () => {
    expect(Object.keys(ALL_ICONS)).toHaveLength(5);
  });
});
