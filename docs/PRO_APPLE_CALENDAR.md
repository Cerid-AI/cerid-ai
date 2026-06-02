# Apple Calendar connector

## What this connector does

The Apple Calendar connector reads your local Calendar events through Apple's
EventKit and makes them queryable from your Cerid knowledge base, so you can
ask about past and upcoming meetings from chat. It also feeds **meeting
capture's** calendar stitching, resolving Apple Calendar events alongside
Google and Outlook so a captured meeting can be matched to the right calendar
entry. Everything happens on your Mac — event data never leaves the machine.

## One-time setup

The connector reads events via the bundled `ceridek` Swift helper, which uses
EventKit and therefore needs **Calendars** access for the Cerid desktop app.

1. Open **System Settings → Privacy & Security → Calendars**.
2. Enable the toggle next to **Cerid**. (If Cerid isn't listed yet, open
   **Connectors → Apple Calendar** and click **Enable** once — macOS will
   prompt for access, after which Cerid appears in this list.)
3. **Quit and relaunch Cerid.** The kernel's TCC cache only re-reads
   permissions on process start, so a running Cerid will keep showing
   "Needs access" until you restart it.

Once relaunched, open **Connectors → Apple Calendar** and click **Enable**.

## What gets ingested

For each event the helper can read:

- Title, start/end time, and all-day flag.
- The calendar the event belongs to and its account.
- Location and notes text, when present.
- Attendee names where exposed by EventKit.

Events are surfaced with `source: apple_calendar`.

## What's NOT ingested

- Calendars you have not granted access to.
- Attachments on events.
- Declined events, depending on your Calendar settings.

## Privacy posture

All access is local and read-only through EventKit. The connector makes no
network calls. When you later ask the agent a question, only the retrieved
snippets used to answer that specific query are sent to whichever LLM you've
configured.

## Troubleshooting

**"Needs access" banner won't go away.**
Grant Calendars access to Cerid, then **quit and relaunch the app**. Toggling
the permission while Cerid is running does not take effect until restart.

**No events after enabling.**
Confirm the `ceridek` helper shipped with your build (it is bundled in the
signed desktop app) and that at least one calendar is enabled in Calendar.app.
Re-run the scan after granting access.

**Meeting capture isn't matching Apple Calendar events.**
Calendar stitching only resolves events from connectors that are enabled and
have access granted. Make sure this connector shows **Enabled** before
capturing a meeting.
