# Enterprise — SAML 2.0 single sign-on

Authenticate users against your own identity provider (Okta, Entra ID / Azure
AD, Google Workspace, Keycloak, …) instead of a password.

## Requires multi-user mode

The endpoints are registered only when `CERID_MULTI_USER=true`. This is not an
oversight to work around: SSO issues a session for an identity the IdP has
attested to, and single-user mode has exactly one operator authenticated by API
key, with nobody for an IdP to distinguish. On a single-user install there is
nothing for SSO to do.

Multi-user mode is itself gated behind `CERID_MULTI_USER_EXPERIMENTAL=true` and
requires `CERID_JWT_SECRET`. Read those warnings before enabling it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/saml/metadata` | SP metadata XML — paste into the IdP |
| GET | `/auth/saml/login` | SP-initiated login; redirects to the IdP |
| POST | `/auth/saml/acs` | Assertion Consumer Service — the IdP posts here |

All three are gated on the Enterprise `sso_saml` flag and return 403 below it,
or 503 if the gate itself cannot be evaluated.

## Configuration

```bash
CERID_SAML_SP_ENTITY_ID=https://cerid.example.com/auth/saml/metadata
CERID_SAML_SP_ACS_URL=https://cerid.example.com/auth/saml/acs
CERID_SAML_IDP_ENTITY_ID=https://idp.example.com/metadata
CERID_SAML_IDP_SSO_URL=https://idp.example.com/sso
CERID_SAML_IDP_X509_CERT="-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----"
CERID_SAML_CLOCK_SKEW_SECONDS=60
```

**Every field is required.** The router returns 503 naming what is missing
rather than starting half-configured — a blank IdP certificate makes every
signature fail in a way that reads as "SSO is broken", and a blank SP entity id
would make the audience check compare against `""` and pass for anything.

An env var cannot hold real newlines, so paste the PEM with literal `\n`
between lines; it is unescaped when read.

Clock skew is capped at 300 seconds regardless of what you set. A larger skew
is a larger window in which a stolen assertion still replays.

## What is verified

Every one of these is a refusal, not a warning:

1. the XML parses with entity resolution and network access **off** (XXE/SSRF)
2. a signature is present — an unsigned assertion is rejected outright
3. the signature verifies against the **pinned** `CERID_SAML_IDP_X509_CERT`,
   never against a certificate carried in the message
4. the IdP reported `Success`
5. the `AudienceRestriction` names this SP — a correctly signed assertion
   minted for a *different* service is still refused
6. `Destination`, when present, is this ACS URL
7. the assertion is inside `NotBefore` / `NotOnOrAfter`, plus bounded skew
8. the assertion ID has not been seen before (replay, via Redis `SET NX`)

**Signature wrapping** is the attack the implementation is shaped around: sign
a benign assertion, wrap a forged one around it, and hope the SP verifies one
and reads the other. Everything returned is extracted exclusively from the
subtree the verifier hands back, and the original document is never consulted
again.

Verification uses [signxml](https://github.com/XML-Security/signxml) rather
than hand-rolled XML-DSig. Canonicalisation, transforms and reference
resolution are where home-grown SAML gets broken, and it looks finished either
way.

## Behaviour worth knowing

- **Just-in-time provisioning.** A user who authenticates successfully and has
  no account gets one, in the default tenant. Their password field is set to an
  unusable value on purpose — an SSO user must not gain a second, weaker way in
  through the password login route.
- **Email resolution** prefers the `email` / `mail` / standard OID / WS-Fed
  claim names, and falls back to the NameID when it looks like an address.
  Configure the IdP to release one; without it the assertion is refused.
- **No Redis, no login.** Replay protection lives in Redis, so if it is
  unreachable the ACS returns 503 and authenticates nobody. An endpoint that
  cannot tell a first use of an assertion from a twentieth must not
  authenticate.
- **Rejections do not say why.** The caller gets `SAML authentication failed.`;
  the reason goes to the log and to the audit trail. Telling an unauthenticated
  caller which check it failed helps it pass next time.
- **Every attempt is audited.** Successes and denials both land in the
  [audit log](ENTERPRISE_AUDIT_LOG.md) as `auth.saml`. A run of denials there
  is what an attack looks like.

## Not included

- Single Logout (SLO)
- IdP-initiated login (the ACS accepts it, but no `RelayState` landing is wired)
- Signed `AuthnRequest`s — the published metadata declares
  `AuthnRequestsSigned="false"`, and the security of the flow rests on
  verifying the IdP's signature on the way back
- Encrypted assertions
- Group/role mapping from assertion attributes — attributes are captured, but
  every provisioned user gets the default role

## History

Added 2026-08-11. `sso_saml` had been ✓ Enterprise in `docs/TIER_MATRIX.md`
with no implementation anywhere; it was recorded as `unimplemented` on
2026-08-10 so an Enterprise install would report it degraded rather than
silently fine, and built here.
