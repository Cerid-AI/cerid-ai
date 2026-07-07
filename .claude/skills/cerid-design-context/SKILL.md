---
name: cerid-design-context
description: "Project-specific UI/UX constraints for **Cerid AI repos only** (cerid-ai-internal, cerid-ai, cerid-trading-agent, cerid-boardroom, cerid-* client repos, and any future cerid-derivative repo). Use ONLY when the current working directory is inside such a repo. Composes with global design-intelligence skills (e.g. ui-ux-pro-max) so generic recommendations land on-brand. Covers the stack pin (React 19 + shadcn/ui new-york + Tailwind v4 + lucide), brand voice + anti-patterns (no AI purple/pink gradients, no marketing funnel), the canonical tier-colour system (private mode + verification bands), the 4-state UX matrix required per pane, the active design-drift gate + allowlist, the canonical primitive ladder under src/web/src/components/ui/, the cross-pane navigation contract, and the accessibility floor."
license: Apache-2.0
metadata:
  author: cerid-ai
  version: "0.2.0"
  scope: cerid-derivative-only
  applies-to:
    - "~/Develop/cerid-ai-internal/**"
    - "~/Develop/cerid-ai/**"
    - "~/Develop/cerid-trading-agent/**"
    - "~/Develop/cerid-boardroom/**"
    - "~/Develop/cerid-*/**"
---

# Cerid Design Context

> **Scope tag (applies-to):** Cerid AI repositories ONLY.
>
> This skill is user-level (`~/.claude/skills/cerid-design-context/`) so the
> same constraints apply across every Cerid-derivative repo without per-repo
> duplication. It must NOT activate for unrelated projects. Before invoking,
> verify the current working directory is inside a cerid-* repository:
>
> ```bash
> # Quick scope check — bail if neither matches.
> pwd | grep -qE '/Develop/cerid-(ai|ai-internal|trading-agent|boardroom|[a-z0-9-]+)(/|$)' \
>   || { echo "cerid-design-context: out of scope"; exit 0; }
> ```
>
> If you're on a non-cerid project (e.g. dotfiles, an unrelated client, a
> standalone library), **do not apply these rules** — they encode Cerid's
> stack pin and brand voice, which won't match other contexts.
>
> A mirrored copy lives at `<repo>/.claude/skills/cerid-design-context/`
> for public-side documentation and for sessions started in a fresh clone
> where the user-level skill isn't yet installed. Keep both copies content-
> identical when editing.

## Stack pin (do not propose alternatives)

| Layer | Choice | Notes |
|---|---|---|
| Framework | React 19 + Vite (web) / Electron (desktop) | No Next.js, no Remix. |
| Styling | Tailwind v4 (`@theme inline`) + shadcn/ui "new-york" | Tokens live in `src/web/src/index.css`. |
| Primitives | shadcn under `src/web/src/components/ui/` | Add new primitives there before reaching for hand-rolled markup. |
| Icons | `lucide-react` only | Drift gate flags non-lucide icon imports. Emojis are not icons. |
| Charts | Recharts (existing) | Inline `style={{}}` props on Recharts elements are drift-allowed via `scripts/design_drift_allowlist.txt`. |
| State | TanStack Query + lightweight contexts | No Redux, no Zustand, no jotai. |
| Tests | Vitest + Testing Library + jest-axe | Snapshots are accepted for badges / icons; behaviour tests preferred. |
| Type discipline | TS strict; `tsc --noEmit` must be green | No `any` outside `// drift-allowed`-style narrow exceptions. |

## Brand voice + anti-patterns

Cerid is **privacy-first, self-hosted, professional**. The product is a thinking
partner, not a hype machine. Match this in copy, in colour choices, and in
visual treatments.

**Reject without negotiation:**
- AI-flavoured purple/pink gradients (`from-purple-500 via-pink-500 to-orange-400`).
- "Sparkle" / "magic" / "unleash" copy. Sparkle icons only appear when literally indicating a sparkle (auto-summary card icon is acceptable; CTAs are not).
- Marketing-funnel patterns (testimonial carousels, conversion CTAs, social-proof badges, "trusted by N companies").
- Skeuomorphic textures, glassmorphism on dense data surfaces, neumorphic shadows on text inputs.
- Dark-mode-only colour schemes. Light mode must always pass WCAG AA contrast.

**Embrace:**
- Calm monochrome with one accent (the `--brand` token — currently a teal).
- Status-driven colour bursts: `bg-green-500/10` for "ok", `bg-amber-500/10` for "transient warn", `bg-red-500/10` for "critical".
- Density gradients: monitoring / audit panes are information-dense (small text, tight gaps); chat + wiki are reading surfaces (larger text, generous gaps).
- Typography hierarchy via type-scale tokens, never one-off `text-[Npx]` (see drift gate).

## Tier-colour system (canonical mapping)

Two parallel colour scales live in the product. **Do not invent a third.**

### Private Mode (`chat-toolbar.tsx`)

| Level | Colour | Semantics |
|---|---|---|
| 0 (Off) | `text-muted-foreground` | Standard behaviour. |
| 1 (Skip saves & sync) | `text-green-500 / bg-green-500/10` | Local-only. |
| 2 (Also skip KB) | `text-yellow-500 / bg-yellow-500/10` | Model isolated. |
| 3 (Also no logging) | `text-orange-500 / bg-orange-500/10` | Bypass Redis. |
| 4 (Full ephemeral) | `text-red-500 / bg-red-500/10` + `animate-pulse` | Wipe-on-close. **Requires `<AlertDialog>` confirmation.** |

### Verification bands (`<VerifiedResponse>`)

| Band | Colour | Semantics |
|---|---|---|
| `verified` | green (border, bg, text) | Datapoint supported by ≥1 source. |
| `partial` | amber | Mixed support / hedge. |
| `unverified` | red | No support / contradiction. |

When designing a third surface that needs a "trust gradient", map to one of these existing scales. Don't introduce a fourth.

## The 4-state UX matrix (required per pane)

Every pane in `src/web/src/components/{chat,kb,sources,subjects,analytics,automations,briefs,memories,agents,audit,monitoring,processor,console,settings,setup,wiki,workflows}` must explicitly handle:

1. **Loading** — `<Skeleton>` blocks matching the shape of the eventual content (not a centered spinner unless the pane is itself small).
2. **Error** — `<Alert variant="destructive">` with retry where applicable. Operator-readable error text, not a stack trace.
3. **Empty** — `<EmptyState icon=... title=... description=...>` from `components/ui/empty-state.tsx`. Never an empty `<div>` or a bare muted string.
4. **Success** — the data-on path. Compose primitives; don't hand-roll cards.

D.2 has an axe-clean test contract — see `src/web/src/__tests__/*.test.tsx` for the pattern (each pane test file asserts all four states render and are axe-clean).

## Design-drift gate (BLOCKING)

Blocking CI gate at `lint / no-design-drift` runs
`scripts/lint-no-design-drift.py --root src/web/src --allow-file scripts/design_drift_allowlist.txt`.

Reports `OK — 0 violations`. The gate is BLOCKING — any new drift fails CI
(it gates `docker` via the `lint` job). Legitimate exceptions carry an inline
`// drift-allowed: <reason>` annotation (content-stable); the `path:lineno`
allowlist file is retained only for a small set of stable entries.

**Hard rules** (gate flags as `arbitrary-tailwind` or `inline-style`):
- No `text-[Npx]` — use `text-label-xxs / -xs / -sm` (≤11 px) or the standard `text-xs / -sm / -base / -lg / -xl` scale.
- No hand-rolled progress bars — use `<ProgressBar pct fillClassName? size?>` from `components/ui/progress-bar.tsx`.
- No raw `<textarea className="ring-[3px]…">` — use `<Textarea>` from `components/ui/textarea.tsx`.
- No raw hex colour literals in JSX — define a token in `index.css` `@theme inline`.
- No non-lucide icons.

**Soft exceptions** (suppress with an inline `// drift-allowed: <reason>` annotation — content-stable; the `path:lineno` allowlist is legacy/kept-minimal):
- Runtime-derived inline styles (popover absolute positioning, animation stagger delays, Recharts API).
- Pinned widths for `<TooltipContent>` / `<SelectTrigger>` / `<ScrollArea>` (sibling alignment).
- Brand chrome (sidebar logo type sizes).
- Workflow editor canvas geometry (SVG-pinned dimensions).

When in doubt, add a `// drift-allowed: <reason>` annotation on the violation line — but only if the reason genuinely can't be expressed as a static token or primitive.

## Canonical primitive ladder

Before reaching for raw markup, climb this ladder:

1. **shadcn primitive** — `Button`, `Badge`, `Card`, `Dialog`, `AlertDialog`, `Tooltip`, `Popover`, `Sheet`, `Tabs`, `ScrollArea`, `Skeleton`, `Separator`, `Input`, `Textarea`, `Select`, `Checkbox`, `Switch`, `Slider`, `Label`, `HoverCard`, `ContextMenu`. (See `components/ui/`.)
2. **Cerid primitive** — `EmptyState`, `LastUpdated`, `InfoTip`, `DomainBadge`, `DataState`, `PaneErrorBoundary`, `ProgressBar`, `Textarea`. (Also under `components/ui/`.)
3. **Feature-area composite** — `VerifiedResponse`, `TrustScoreChip`, `ProcessorPane`, `InvariantsCard`, `GraphExplorer`. (Under domain dirs.)
4. **Hand-rolled JSX** — last resort. If you do this, ask whether a primitive should be extracted instead.

If you find yourself writing the same `flex items-center gap-2 ... rounded-md border ...` twice across two files, stop and propose a primitive.

## Cross-pane navigation (R.2 wiring)

Use `useNavigation()` from `src/web/src/contexts/navigation-context.tsx` for any "click here to go there" behaviour:

```tsx
const { goTo, composeChat } = useNavigation()
// Navigate to another pane:
<Button onClick={() => goTo("wiki")}>Open Wiki</Button>
// Seed the chat composer with a query:
<Button onClick={() => composeChat({ text: `Summarise ${entity.name}` })}>
  Ask about this entity
</Button>
```

Do **not** add `window.location.assign(...)` or React Router calls — there is no router. The app is a single SPA with pane-based navigation.

## Accessibility floor (non-negotiable)

- `prefers-reduced-motion: reduce` must collapse all `transition-*` and `animate-*` to a duration of 0. Tailwind v4 handles this if you don't add custom `@keyframes` outside the safe set.
- Light mode text contrast ≥ 4.5:1 against background. Dark mode mirrors.
- Focus states: `focus-visible:ring-ring/50 focus-visible:ring-[3px]` (the shadcn convention; allowlisted because shadcn).
- Every interactive element keyboard-reachable. Click handlers on `<div>` must come paired with `onKeyDown={Enter/Space}` and `role="button"` + `tabIndex={0}`.
- All `<img>` need `alt`. Decorative SVGs need `aria-hidden="true"`.
- D.3 axe-core CI gate runs across all pane tests; new components must pass `expect(container).toBeAccessible()` (jest-axe).

## Pre-delivery checklist (mirrors the global skill's checklist, project-tuned)

Before claiming a UI change is "done":

- [ ] `npx tsc --noEmit` green.
- [ ] `npx eslint . --quiet` ≤ 0 *new* errors (pre-existing 0 — keep it that way).
- [ ] `npx vitest run` green (including any new state-matrix tests for the surface you touched).
- [ ] `python scripts/lint-no-design-drift.py --root src/web/src --allow-file scripts/design_drift_allowlist.txt` reports 0 violations.
- [ ] Loading / error / empty / success states all render (verified in test or by toggling state in a story-like fashion).
- [ ] Keyboard tab order produces a sensible sequence; focus rings visible.
- [ ] Hover transitions in the 150–300 ms band; no transitions > 500 ms outside hero-level animation.
- [ ] No emojis as icons. No AI purple/pink gradient. Light-mode contrast OK.
- [ ] If you added a new dependency, `npm audit` reports 0 moderate-or-higher.

## Anti-scope (do not bring into the codebase)

- Landing-page / marketing-funnel components (CTA blocks, testimonial carousels, pricing tables outside `pro-section.tsx`).
- Slides / pitch-deck generators.
- AI-art / asset generation (logos, banners, social photos).
- Brand-guideline scaffolding (we have a one-page voice doc inline; we don't need a brand kit generator).
- Skill-marketplace / agent-marketplace surfaces.

These are out-of-product. If you find a generic design skill recommending one of them, decline politely and surface the equivalent in-product surface instead (e.g. recommend a `<KnowledgeLibraryDialog>` upgrade rather than a brand banner generator).

## Related docs (read on demand)

- `docs/PRODUCT_STORY.md` — the canonical 5-primitive narrative (Verification / TrustScore / Narrative Loop / Wiki / Background Processor).
- `docs/CONVENTIONS.md` — broader engineering conventions (drift gate, naming, layering).
- `tasks/2026-05-10-D1-design-drift-punch-list.md` — the D.1 cleanup history with line-by-line rationale for the active allowlist.
- `scripts/design_drift_allowlist.txt` — the active allowlist with per-section rationale.
- `docs/COMPLETED_PHASES.md` — what each v0.x release shipped, so you don't suggest re-doing landed work.
