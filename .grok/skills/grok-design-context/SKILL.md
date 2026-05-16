---
name: grok-design-context
description: Project-specific UI/UX and design system constraints for Cerid AI repos only. Grok-optimized version. Use when working inside cerid-ai, cerid-ai-internal, cerid-trading-agent, cerid-boardroom, cerid-* client repos, or any future cerid-derivative. Composes with global design skills. Covers the stack pin (React 19 + Tailwind + shadcn/ui + specific component patterns), voice of the product, and brand constraints.
---

# Cerid Design Context (Grok Edition)

You are operating inside a Cerid-family product.

## Core Constraints (always active in these repos)

- **Stack pin**: React 19 + TypeScript + Tailwind + shadcn/ui (new components must follow existing patterns exactly).
- **No new design systems** without explicit approval.
- **Voice**: Professional but warm, enterprise-grade but not cold, clear and direct.
- **Preservation of existing patterns** is extremely high priority in the cerid-ai frontend.

When the user asks for UI work, frontend components, marketing site changes, or dashboard UX, you **must** load this context.

Use the `grok-design-context` skill (this one) + any global frontend skills the user has enabled.

For very large frontend tasks, consider forking a specialized `frontend-design` subagent (if available in your plugin set) with this context injected.

## Specific Cerid Rules

- All new pages/components should feel like they belong in the existing Cerid AI application.
- Color tokens, spacing, typography must come from the existing design tokens.
- Accessibility (a11y) and dark mode are non-negotiable.
- Performance budgets matter for the main web app.

If the task is marketing-site (cerid-ai-marketing) vs core product (cerid-ai), the constraints differ slightly — ask for clarification if unsure.

This skill should be installed into `~/Develop/.grok/skills/` for all Cerid developers (via dotfiles).
