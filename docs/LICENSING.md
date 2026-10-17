# Licensing and activation

How this public offering is licensed, and how a running install decides its tier.
[cerid.ai](https://cerid.ai) does not sell a subscription and does not issue a
license key. A note about the work, including an offer to acquire the
intellectual property, goes to [hello@cerid.ai](mailto:hello@cerid.ai).
Security reports stay at [security@cerid.ai](mailto:security@cerid.ai).

For the flag-by-flag feature split see [`TIER_MATRIX.md`](TIER_MATRIX.md); for
the source licenses that govern each directory see
[`../CONTRIBUTING.md`](../CONTRIBUTING.md#license).

## Tiers

The tiers are gates in the code, not prices.

| Tier | What it covers |
|------|----------------|
| **Core** | The self-hosted knowledge companion: all four knowledge surfaces, 12 agents, 62 MCP tools, local + web sources, the local LLM pipeline, and both SDKs. No account, no telemetry, no seat limit, no expiry. |
| **Pro** | The wider surfaces in the same repository: cloud and Apple connectors, Meeting Capture, custom Smart RAG, advanced analytics, daily digest, inbox triage, metamorphic verification. |
| **Enterprise** | Multi-user surfaces that exist in the code: tenant isolation, SSO/SAML, audit logging. Not a separate offer. |

## What the site does not do

There is no checkout and no new key minted on cerid.ai.
[cerid.ai/pricing](https://cerid.ai/pricing) is a contact page, not a plan picker.
[cerid.ai/contact](https://cerid.ai/contact) is the same note.

## What the app still does

The running app can still start a one-time local 14-day Pro trial from
**Settings → Plan & Billing**, and it can still accept an Ed25519 key that was
issued earlier. The server grants the trial to itself. One trial per
installation. No data leaves the machine for either step, and a self-hosted
server never contacts a payment provider.

When a trial ends the server drops back to the baseline tier. Nothing is
deleted: sources you connected during the trial stop syncing, and the knowledge
they already produced stays in your knowledge base.

Paste a key that was already issued under **Settings → Plan & Billing** and
choose **Activate**. Validation is offline: the key carries its own tier and
expiry, so activation works on an air-gapped host and no license server is
ever contacted. It needs no reinstall and no restart.

## How keys are verified

Keys are **Ed25519-signed and offline-verifiable**. A private key that is not
in this repository signed the keys that exist. cerid.ai does not mint a new
one. The running server verifies a key with the **public** key compiled into
the app. A public key can verify signatures but cannot produce them, so
shipping it in source-available code gives away nothing — keys cannot be
minted from this repository.

A key is `CERID-PRO-` followed by base32 groups of four encoding 72 bytes: an
8-byte signed payload (`expiry_day`, `tier_byte`, scheme `version`, and a 4-byte
email fingerprint kept for audit only) plus the 64-byte signature over it.
Because the expiry and tier live *inside* the signed payload, neither can be
edited without invalidating the signature.

Verification is entirely local — no license server, no network call, works
air-gapped. A key is rejected if the signature does not match, if it was signed
by anyone else, if any byte of it has been altered, if the scheme version is
retired, or if it has expired.

> One quirk worth knowing: the final character of a key encodes a single
> significant bit, so a typo there can still decode to the same key. Every byte
> of the payload and signature is covered; only that one trailing character is
> slack.

Setting `CERID_LICENSE_PUBLIC_KEY` to an empty string disables verification and
accepts any correctly-shaped key. That exists for local preview only, and the
server logs a warning on every boot while it is in effect.

## How the runtime tier is decided

Three sources compose, and the **most capable one wins** — a source can raise
the tier, never lower it:

| Source | Set by | Notes |
|--------|--------|-------|
| `CERID_TIER` env var | Operator | The **baseline floor**. Deactivating a license or ending a trial falls back to this, not to `community`. |
| Active trial | Settings → Plan & Billing | Grants Pro until its expiry. |
| Activated license key | Settings → Plan & Billing | Grants the key's tier until the key's embedded expiry. |

So an operator who pins `CERID_TIER=enterprise` on an air-gapped box keeps
Enterprise even while a Pro trial is running, and an install whose key lapses
returns to whatever the operator configured rather than being knocked to Core.

Entitlement is re-derived at every startup, so an activated license survives a
container restart. If it did not, `FEATURE_TIER` would fall back to the env
value on each boot and gated features would silently disappear.

### `CERID_TIER` for operators

The variable exists for deployments where interactive activation is impractical
— air-gapped installs, CI. Setting it does not grant a license to the plugin
trees, whose own terms are in [`../plugins/LICENSE`](../plugins/LICENSE) (BUSL-1.1).

Using it to raise the tier without a key puts the server in the
**unlicensed-Pro** state. Nothing is blocked, degraded, or time-limited — but
the server stops pretending a key is present:

- a non-dismissible notice on the Pro settings panes, and an **Unlicensed**
  marker in the status bar. The status bar renders on every screen, but it
  is hidden below the `md` breakpoint — on a narrow window the mobile tab
  bar occupies that position — so on a phone the settings-pane notice is
  the only marker (this doc said "on every screen" until 2026-08-10);
- a `license_notice` WARNING in the server log on every boot;
- `"Generated by an unlicensed copy of Cerid Pro"` stamped into Pro-generated
  artifacts such as the daily digest.

All of it clears the moment you activate a key or start the free trial. An
install that holds a key and also pins `CERID_TIER` is reported as licensed — the
key wins — and a server mid-trial is reported as trialing, not unlicensed.

Cerid is source-available, so a determined reader can always change what runs
on their own machine. There is no phone-home, no seat enforcement, and no
attempt at DRM. The gates stay so the surfaces remain distinguishable. They
are not a checkout.

## Endpoints

A running install exposes these locally (no network egress):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/license/status` | Current tier, its source, and trial state |
| `GET` | `/license/capabilities` | Per-feature entitlement map + `license_state` (drives the lock badges and the unlicensed notice) |
| `POST` | `/license/activate` | Validate and persist a key |
| `POST` | `/license/deactivate` | Drop back to the baseline tier |
| `POST` | `/license/trial` | Start the one-time 14-day trial |

## Troubleshooting

**"Invalid or expired license key."** The signature did not verify. Usually a
truncated paste — copy the whole key including the `CERID-PRO-` prefix and every
dash group. Otherwise the key has expired, or it was not issued by Cerid.

**"Invalid license key format."** The text is not shaped like a key at all
(wrong prefix, or it does not decode to 72 bytes).

**Pro features still locked after activating.** Check
`GET /license/status` reports `tier: "pro"`. If it does and the UI disagrees,
the browser is holding a cached capabilities response for up to 60 seconds.

**A feature is Pro-marked but still off on Pro.** That is a server flag, not a
plan limit — the feature's env var is disabled. Settings names the variable
inline for exactly this case.

**Moving to a new machine.** Activate the same key there. Cerid records which
host last activated a key for audit only; it is not enforced.
