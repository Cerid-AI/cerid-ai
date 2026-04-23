# Cerid AI — Brand Identity Guide

> **Status:** Locked & Production-Ready
> **Last updated:** 2026-03-30

## Brand Names

| Tier | Product Name | Tagline | Shield Style |
|------|-------------|---------|--------------|
| **Core** (community) | Cerid Core | Smart. Extensible. Private. | Teal outline, teal C |
| **Pro** (commercial) | Cerid Pro | Smart. Secure. Fully Controlled. | Silver/chrome trim, teal C |
| **Enterprise** | Cerid Vault | Secure by Design. Mission Assured. | Gold trim, teal C, gold accent |

## Color Palette

### Primary Palette (institutional-authoritative, premium technical)

| Role | Hex | OKLCH | Usage |
|------|-----|-------|-------|
| **Navy Deep** | `#0A1F3D` | `oklch(0.16 0.03 240)` | Primary dark background, shield base |
| **Metallic Gold** | `#D4AF37` | `oklch(0.78 0.12 85)` | Shield trim, accents, excellence, Pro badges |
| **Lighter Gold** | `#E8C56A` | `oklch(0.85 0.10 85)` | Highlights/rivets (use sparingly) |
| **Teal/Cyan** | `#00E5D8` | `oklch(0.82 0.16 178)` | Core glow, "C", energy, intelligence |
| **Deeper Teal** | `#00C4B4` | — | Secondary glows |
| **Bright Teal** | `#7FF9E8` | — | Energy bursts, gradient end |

### Supporting Neutrals

| Role | Hex | Usage |
|------|-----|-------|
| **Dark Graphite** | `#1F2A44` | Text "VAULT", secondary text |
| **Light Graphite** | `#A8B5C8` | Body text in dark mode |

## Usage Rules

- **Shield Base:** `#0A1F3D`
- **Shield Trim / Rivets:** `#D4AF37`
- **Glowing "C" + Particles:** `#00E5D8` (main) + `#7FF9E8` (highlights)
- **Wordmark "CERID":** Gradient from `#00E5D8` to `#7FF9E8`
- **Wordmark "VAULT":** `#A8B5C8` with thin `#D4AF37` underline
- **Backgrounds:** `#0A1F3D` (dark mode default)
- **Diagrams / Icons:** `#00E5D8` for active flows, `#D4AF37` for boundaries

## CSS Implementation

### GUI (src/web/src/index.css)

```css
/* Dark mode brand variables */
--brand: oklch(0.82 0.16 178);        /* #00E5D8 vibrant teal */
--background: oklch(0.16 0.03 240);   /* #0A1F3D navy deep */
--muted-foreground: oklch(0.72 0.02 230); /* #A8B5C8 light graphite */

/* Gold accent utilities */
.text-gold { color: oklch(0.78 0.12 85); }
.border-gold { border-color: oklch(0.78 0.12 85 / 50%); }
.bg-gold\/10 { background: oklch(0.78 0.12 85 / 10%); }
```

### Marketing Site (packages/marketing/src/app/globals.css)

```css
/* Wordmark gradient (#00E5D8 → #7FF9E8) */
.text-brand-gradient { background: linear-gradient(135deg, #00E5D8, #7FF9E8); }

/* Shine sweep animation */
.text-brand-shine { animation: shine-sweep 4s ease-in-out infinite; }

/* Teal glow pulse */
.glow-teal { animation: teal-pulse 4s ease-in-out infinite; }

/* Circuit-board background pattern */
.bg-circuit { background-image: radial-gradient(circle, rgba(0,229,216,0.07) 1.5px, transparent); }

/* Gold section dividers */
.divider-gold { border-image: linear-gradient(90deg, transparent, rgba(212,175,55,0.4), transparent) 1; }
```

## SVG Shield Component

Three-variant SVG shield available at `src/web/src/components/layout/sidebar.tsx` and `packages/marketing/src/components/brand-shield.tsx`:

- **Core:** Teal outline, teal gradient "C"
- **Pro:** Silver/chrome outline, teal "C"
- **Vault:** Gold trim, teal "C", gold accent dot

Static SVG files:
- `src/web/public/cerid-core.svg`
- `src/web/public/cerid-pro.svg`
- `src/web/public/cerid-vault.svg`
- `src/web/public/cerid-logo.svg` (default, matches Core)

## Brand Assets

Source images in `~/Dropbox/cerid-sync/Graphics and branding/`:

| File | Content | Used In |
|------|---------|---------|
| `8vYaF.jpg` | Gold-trim shield with teal C (hero) | Marketing hero |
| `eK2nQ.jpg` | Hero tagline banner | Marketing features |
| `HT7YS.jpg` | 6-feature icon grid | Cropped into badges |
| `K56Cs.jpg` | 4-feature icon set | Cropped into badges |
| `kr4QB.jpg` | Cerid Vault app icon | Marketing pricing |
| `57mYS.jpg` | Cerid Core app icon | Marketing pricing |
| `5c1SK.jpg` | CERID PRO wordmark | Marketing pricing |
| `sHzTK.jpg` | 3D shield with rivets | Marketing security |
| `umLv0.jpg` | Secure Intelligence banner | Marketing security |
| `pOXvI.jpg` | Privacy-First graphic | Marketing privacy |
| `jGSLg.jpg` | Architecture diagram | Marketing features |
| `uxHt0.jpg` | 12-icon grid | Marketing features |

Individual badges cropped from grids:
- `badge-rag.jpg`, `badge-verification.jpg`, `badge-byom.jpg`
- `badge-agents.jpg`, `badge-secure.jpg`, `badge-architecture.jpg`
- `badge-ephemeral.jpg`, `badge-zerotrust.jpg`, `badge-orchestrator.jpg`

## Tier-Reactive Elements

The GUI sidebar, favicon, and browser tab title update dynamically based on the active tier:

| Element | Core | Pro | Vault |
|---------|------|-----|-------|
| Sidebar wordmark | CERID **CORE** | CERID **PRO** | CERID **VAULT** |
| Wordmark tier color | Muted gray | Teal (brand) | Gold |
| Favicon | Teal shield SVG | Silver shield SVG | Gold shield SVG |
| Browser tab | "Cerid Core" | "Cerid Pro" | "Cerid Vault" |
| Pro badges | Gold (locked) | Gold (active) | Gold (all unlocked) |

Tier cycling is available via the Shield icon in the sidebar bottom-left (cycles Core → Pro → Vault via `POST /settings/tier`).
