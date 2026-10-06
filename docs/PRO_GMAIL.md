# Pro Connector — Gmail

## What this connector does

The Gmail Pro connector lets chat answers cite messages from your Gmail
mailbox alongside local KB artifacts. When a question would benefit from
recent email context (e.g. "what did the vendor quote last week?"), the
connector searches Gmail on demand, pulls the most-relevant threads, and
hands them to the retrieval pipeline as first-class evidence with proper
provenance (sender, subject, timestamp, message-id).

Nothing is bulk-indexed for v1 — search-on-demand only. The mailbox stays
where it is; only the matching results enter the answer context window.

## Architecture

The connector runs as a **sibling MCP server** in its own Docker container,
not inside the Cerid backend:

- Image: `taylorwilsdon/google_workspace_mcp`, pinned by git commit SHA in
  `stacks/connectors/docker-compose.yml`.
- Container name: `cerid-google-workspace-mcp`.
- Internal address: `http://cerid-google-workspace-mcp:8810/mcp`. The container
  and host ports must match — the OAuth consent screen redirects the browser to
  the port the container believes it is on.
- Transport: streamable-HTTP.
- AuthN to the MCP server: **none**. The upstream image has no client-auth
  mechanism (there is no `WORKSPACE_MCP_AUTH_TOKEN` in it), so the bearer Cerid
  sends is ignored. The container's protection is that it binds to loopback
  only. This doc claimed a static-bearer control until 2026-08-09; it never
  existed. Neither does the ms365 sibling: it forwards the client's bearer
  straight to Microsoft Graph rather than checking it, which is why Cerid's
  static hex token arrives there as an invalid JWT.
- AuthN to Google: OAuth 2.0, owned entirely by the sibling container.
  The Cerid backend never sees Google refresh tokens.

The Cerid backend registers the sibling server as a remote MCP endpoint
at startup if the bearer token, the OAuth client credentials, **and**
`USER_GOOGLE_EMAIL` are present in the environment. Any one missing disables
the connector with a clear log line rather than failing the boot.

`USER_GOOGLE_EMAIL` is not optional and is not only for the consent flow.
Every tool this sibling exposes declares `user_google_email` as a REQUIRED
argument — `search_gmail_messages`, `get_events`, `get_gmail_message_content`,
all of them — and `--single-user` does not change that; it only fixes which
account the OAuth flow consents. Without the value the sibling rejects each
call with `1 validation error … Missing required argument`, which arrives as a
tool RESULT rather than an exception, so the connector reports zero results and
looks exactly like an empty mailbox. It is part of `is_configured()` for that
reason: an unset account now reads as "not configured" instead of "no mail".

## Operator setup

### 1. Create the Google OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or reuse an existing personal one).
3. Enable the **Gmail API** under *APIs & Services → Library*.
4. Under *APIs & Services → Credentials*, create an **OAuth 2.0 Client ID**
   of type **Desktop app**.
5. Copy the resulting **client_id** and **client_secret**.

### 2. Populate `.env`

Add to the repo-root `.env`:

```
GOOGLE_OAUTH_CLIENT_ID=<paste-from-google-cloud>
GOOGLE_OAUTH_CLIENT_SECRET=<paste-from-google-cloud>
CERID_CONNECTORS_BEARER=<openssl rand -hex 32>
USER_GOOGLE_EMAIL=<the account you are connecting>
```

`USER_GOOGLE_EMAIL` is required, not cosmetic: `get_events` and every other
tool on the sibling declares `user_google_email` as a required argument, and a
call without it fails validation as a tool RESULT — the connector then reports
zero events, which is indistinguishable from an empty calendar.

`CERID_CONNECTORS_BEARER` is any 32-byte hex string. Generate one with
`openssl rand -hex 32`. The same token must be present when the Cerid
backend boots and when the sibling MCP container boots — they share it
out of band via the `.env` file.

### 3. Bring up the connector stack

```bash
docker compose \
  -f docker-compose.yml \
  -f stacks/connectors/docker-compose.yml \
  --profile pro up -d
```

The `pro` profile gates the Pro-tier sibling containers so they don't
start in default/free deployments.

### 4. First-use OAuth handshake

There is **no browsable start page** — `/oauth/start` is a 404. This doc and
Cerid's own `/connectors/gmail/auth/start` both claimed one until 2026-08-09,
the first time the container was run. The sibling's only HTTP route is the
`/oauth2callback` the consent screen redirects back to; the flow is started by
an MCP tool call.

Set `USER_GOOGLE_EMAIL` in `.env` to the account you are connecting, then:

```bash
K=$(grep -m1 '^CERID_API_KEY=' .env | cut -d= -f2-)
curl -s -X POST -H "X-API-Key: $K" localhost:8888/connectors/gmail/auth/start
```

That calls the sibling's `start_google_auth` tool and returns the consent URL.
Open it on the host, complete sign-in, and consent to the scopes Cerid asks
for. With inbox actions off, those are `openid`, `email`, `gmail.readonly`,
and `calendar.readonly`. With `CERID_INBOX_ACTIONS_ENABLED` on, the same
login also asks for `gmail.labels`, `gmail.modify`, and `gmail.compose`.
It never asks for `gmail.send`. The refresh token is written to the
container's persistent volume and survives restarts. Repeat this when you
revoke access in your Google account settings, when you turn inbox actions
on (the router does not start that re-login), or when the OAuth consent
screen is still in "Testing" — Google expires that refresh token after 7
days. See `docs/RUNBOOK_PRO_CONNECTORS.md` §2.

## Enable in Cerid

Set the feature flag:

```
gmail_connector=true
```

Operators on the Pro tier (`CERID_TIER=pro`) get this flag flipped
on automatically — no manual override needed.

## What gets ingested

Gmail data is **search-on-demand**, not bulk-indexed. When a chat question
benefits from email context, the connector runs the equivalent of a Gmail
search query, returns the top-N matching threads, and the retrieval
pipeline treats each thread as a citable artifact for that turn only.
The local KB does not retain Gmail bodies between sessions; only message
ids and lightweight metadata may be cached for deduplication.

## Privacy posture

- **Credentials never leave the Mac.** The OAuth client_id/secret and the
  refresh token live entirely on the host (in `.env` and the sibling
  container's volume respectively).
- **Bearer token is local-network only.** `CERID_CONNECTORS_BEARER` gates
  the sibling MCP container on the Docker bridge network; it is not
  reachable from outside the host.
- **Refresh tokens stay in the container volume,** never in the Cerid
  backend's memory or its KB stores.
- **Chat search is on demand.** A chat question contacts Google only when
  retrieval asks. Inbox triage is the exception: when both gates in
  `docs/PRO_INBOX_TRIAGE.md` are open, Cerid reads recent unread mail on
  that cadence.

## Inbox actions

Off by default. Set `CERID_INBOX_ACTIONS_ENABLED=true` (or `1`) and
**recreate** the sibling container. A restart keeps the command that was
baked in at create time. `--single-user` stays. Do not set `--tool-tier`.

The read command is:

```bash
uv run main.py --transport streamable-http --single-user --read-only --tools gmail calendar
```

With the flag on, `stacks/connectors/docker-compose.yml` switches the
ceiling to:

```bash
uv run main.py --transport streamable-http --single-user --permissions gmail:drafts calendar:readonly
```

That ceiling is drafts and label edits. It is not `gmail:send`.
Classification uses the connector's existing message fetch, a short
excerpt. It does not request `format=full`. Label names are
`Cerid/Urgent`, `Cerid/Action`, `Cerid/Personal`, `Cerid/Newsletter`,
`Cerid/Promo`, and `Cerid/Spam`; a missing label is created on the first
apply. The content tool does not return labels, so a later pass
learns a correction only when label names are already on the message
metadata.

A second Gmail address added under Sources → Connectors stays pending.
Applies whose account differs from `USER_GOOGLE_EMAIL` are skipped.
Removing an address stops later reads and applies for it and leaves
existing knowledge-base cards in place. Undo of a decision already
recorded for that address still runs.

Re-consent after turning the flag on with the same
`POST /connectors/gmail/auth/start` as §4, then recreate the container.
`/oauth/start` is a 404.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Boot log: `CERID_CONNECTORS_BEARER unset — Pro cloud connectors not registered` | Set the env var in `.env`, then `docker compose up -d` again. |
| Boot log: `GOOGLE_OAUTH_CLIENT_ID missing — google-workspace-mcp disabled` | Add both `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` and recreate the sibling container so it picks up the new env. |
| `breaker-open` errors in `/health` referencing `google-workspace-mcp` | Sibling container has crashed or is restarting. Check `docker logs cerid-google-workspace-mcp`. |
| `/oauth/start` returns 404 | Expected — that route never existed. Use the `auth/start` call in §4. |
| `auth/start` says the sibling is unreachable | Container not running, or the `pro` profile was not passed. Re-run compose with `--profile pro`. |
| 401 from MCP server in backend logs | Bearer token mismatch between the backend's env and the sibling's env. Confirm both containers were recreated after the last `.env` edit. |
| Actions flag is set and apply still reports `disabled`, or labels are refused | The sibling is still on the read-only command, or consent never gained `gmail.compose`. Recreate the container (do not only restart it) and repeat §4. |
