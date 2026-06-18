# Apple Reminders connector

## What this connector does

The Apple Reminders connector reads your local Reminders through Apple's
EventKit and makes them queryable from your Cerid knowledge base, so you can
ask about your to-dos and reminder lists from chat. Everything happens on your
Mac — reminder data never leaves the machine.

## One-time setup

The connector reads reminders via the bundled `ceridreminders` Swift helper,
which uses EventKit and therefore needs **Reminders** access for the Cerid
desktop app.

1. Open **System Settings → Privacy & Security → Reminders**.
2. Enable the toggle next to **Cerid**. (If Cerid isn't listed yet, open
   **Connectors → Apple Reminders** and click **Enable** once — macOS will
   prompt for access, after which Cerid appears in this list.)
3. **Quit and relaunch Cerid.** The kernel's TCC cache only re-reads
   permissions on process start, so a running Cerid will keep showing
   "Needs access" until you restart it.

Once relaunched, open **Connectors → Apple Reminders** and click **Enable**.

## What gets ingested

For each reminder list the helper can read:

- The reminder list name and how many reminders it holds.
- Reminder titles, notes, due dates, and completion state, as the helper's
  per-reminder fetch is enabled.

Reminders are surfaced with `source: apple_reminders`.

> **Status:** the connector currently surfaces your reminder **lists**. Full
> per-reminder content (titles, due dates, notes) lands with the helper's
> date-ranged fetch — no reconfiguration is needed when it does; the same
> Reminders grant covers it.

## What's NOT ingested

- Reminder lists you have not granted access to.
- Attachments on reminders.
- Shared lists owned by other iCloud accounts you have not added on this Mac.

## Privacy posture

All access is local and read-only through EventKit. The connector makes no
network calls. When you later ask the agent a question, only the retrieved
snippets used to answer that specific query are sent to whichever LLM you've
configured.

## Troubleshooting

**"Needs access" banner won't go away.**
Grant Reminders access to Cerid, then **quit and relaunch the app**. Toggling
the permission while Cerid is running does not take effect until restart.

**No reminders after enabling.**
Confirm the `ceridreminders` helper shipped with your build (it is bundled in
the signed desktop app) and that at least one list exists in Reminders.app.
Re-run the scan after granting access.
