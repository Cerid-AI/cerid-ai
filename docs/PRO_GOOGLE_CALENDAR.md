# Pro Connector — Google Calendar

## What this connector does

The Google Calendar Pro connector pulls events from your Google Calendar
into the local KB so chat answers can cite meetings alongside emails and
local artifacts. It also drives **meeting-capture stitching** — when an
audio file is ingested whose recording window overlaps a calendar event,
the event's title, attendees, and description are merged into the meeting
artifact's metadata so retrieval has the human-readable context.

Events are fetched for the requested window on demand (e.g. "what was on
my calendar last Tuesday?") rather than bulk-synced.

## Architecture

The connector runs as a **sibling MCP server** in its own Docker container,
not inside the Cerid backend. It shares the same container as the Gmail
connector — one Google Workspace MCP server covers both APIs:

- Image: `taylorwilsdon/google_workspace_mcp`, pinned by git commit SHA in
  `stacks/connectors/docker-compose.yml`.
- Container name: `cerid-google-workspace-mcp`.
- Internal address: `http://cerid-google-workspace-mcp:8000/mcp`.
- Transport: streamable-HTTP.
- AuthN to the MCP server: static bearer token (`CERID_CONNECTORS_BEARER`).
- AuthN to Google: OAuth 2.0, owned entirely by the sibling container.
  The Cerid backend never sees Google refresh tokens.

If you have already set up the Gmail connector, Calendar reuses the same
OAuth grant — you just add the Calendar scope and re-consent once.

## Operator setup

### 1. Create the Google OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or reuse an existing personal one).
3. Enable the **Google Calendar API** under *APIs & Services → Library*.
   If you are also using the Gmail connector, enable the **Gmail API** in
   the same project.
4. Under *APIs & Services → Credentials*, create an **OAuth 2.0 Client ID**
   of type **Desktop app** (or reuse the one created for Gmail).
5. Copy the resulting **client_id** and **client_secret**.

### 2. Populate `.env`

Add to the repo-root `.env`:

```
GOOGLE_OAUTH_CLIENT_ID=<paste-from-google-cloud>
GOOGLE_OAUTH_CLIENT_SECRET=<paste-from-google-cloud>
CERID_CONNECTORS_BEARER=<openssl rand -hex 32>
```

`CERID_CONNECTORS_BEARER` is any 32-byte hex string. Generate one with
`openssl rand -hex 32`. The same token must be present when the Cerid
backend boots and when the sibling MCP container boots.

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

There is no browsable start page (`/oauth/start` is a 404 — the claim was
wrong until 2026-08-09). Start the flow through Cerid, which calls the
sibling's `start_google_auth` tool and returns the consent URL:

```bash
K=$(grep -m1 '^CERID_API_KEY=' .env | cut -d= -f2-)
curl -s -X POST -H "X-API-Key: $K" localhost:8888/connectors/google_calendar/auth/start
```

Open the returned URL in a browser on the host machine.
Complete the Google sign-in flow; consent to the Calendar scopes (and
Gmail scopes if you're enabling both). The OAuth refresh token is written
to the container's persistent volume and survives restarts.

## Enable in Cerid

Set the feature flag:

```
google_calendar_sync=true
```

Operators on the Pro tier (`CERID_FEATURE_TIER=pro`) get this flag flipped
on automatically.

## What gets ingested

Events are fetched **for the requested window only**. Two consumers drive
the fetch:

1. **Chat questions** that name a date or range — the connector returns
   events in that window as citable artifacts for the answer.
2. **Meeting-capture stitching** — see below.

The local KB does not retain raw event payloads between sessions; only
event ids and stitch-relevant metadata persist for deduplication.

## Calendar stitching integration

When `meeting_capture` ingests an audio file, it records the start and
end timestamps of the recording. The stitching pass queries the Calendar
connector for events that overlap that window. On a match, the meeting
artifact's metadata is augmented with:

- Event title (used as the meeting artifact's display name if no other
  title was provided)
- Attendee list (becomes the meeting's participant set)
- Event description (appended to the artifact's context block)
- Calendar event id (so re-ingesting the same audio re-binds to the same
  event rather than creating a duplicate)

If no overlapping event is found, the meeting artifact is created
unchanged — stitching is additive, never blocking.

## Privacy posture

- **Credentials never leave the Mac.** The OAuth client_id/secret and the
  refresh token live entirely on the host.
- **Bearer token is local-network only.** `CERID_CONNECTORS_BEARER` gates
  the sibling MCP container on the Docker bridge network.
- **Refresh tokens stay in the container volume,** never in the Cerid
  backend's memory or its KB stores.
- **No background polling.** Calendar is contacted only when a user query
  or a meeting-capture stitch pass triggers it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Boot log: `CERID_CONNECTORS_BEARER unset — Pro cloud connectors not registered` | Set the env var in `.env`, then `docker compose up -d` again. |
| Boot log: `GOOGLE_OAUTH_CLIENT_ID missing — google-workspace-mcp disabled` | Add both `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` and recreate the sibling container so it picks up the new env. |
| Meeting artifacts never pick up event titles | Calendar scope not granted during OAuth. Revoke the grant in your Google account settings, then redo the handshake above. |
| `breaker-open` errors in `/health` referencing `google-workspace-mcp` | Sibling container has crashed or is restarting. Check `docker logs cerid-google-workspace-mcp`. |
| Stitching attaches the wrong event | Multiple overlapping events in the recording window. Use the event-id override on the meeting artifact to pin the correct one. |
