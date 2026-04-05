# Feedback Loop Design

**Status:** Design document (Phase 4)
**Date:** 2026-04-05

## Current Behavior

When `enable_feedback_loop` is enabled:
1. After each AI response (>= 100 chars, not aborted), the response is ingested into the KB
2. The ingestion uses `ingestFeedback(userQuery, response, model, conversationId)`
3. This is fire-and-forget from the frontend (use-chat.ts)
4. Private mode level >= 1 disables feedback loop

## What Gets Saved

- **Content:** Full assistant response text
- **Metadata:** User query (as context), model used, conversation ID, timestamp
- **Domain:** Auto-categorized via `ai_categorize()`
- **Storage:** ChromaDB (embeddings) + Neo4j (graph relationships)

## Problems

1. **No user visibility:** Users don't know what's being saved
2. **No granularity:** All-or-nothing — either all responses are saved or none
3. **No quality filter:** Low-quality or incorrect responses get saved equally
4. **No cleanup:** Once saved, users must manually find and delete bad entries
5. **Noise accumulation:** Casual conversations and testing pollute the KB

## Proposed Design

### Opt-in Per Conversation

- Default: feedback loop OFF for new conversations
- Toggle in chat toolbar enables it for current conversation
- Visual indicator (small badge) when feedback is active
- Per-conversation state persisted in conversation metadata

### Save Indicator

- When a response is saved to KB, show a subtle "Saved to KB" chip on the message
- Chip is clickable — opens the artifact in KB pane
- Failed saves show "Save failed" with retry option

### Quality Gate

- Only save responses that pass verification (if hallucination check enabled)
- Responses with >50% unverified claims are not auto-saved
- User can override and force-save via message action menu

### Conversation-Scoped KB Tag

- All feedback artifacts from the same conversation share a `conversation:{id}` tag
- KB view can filter by conversation to see what was learned
- Makes bulk cleanup easy: "Delete all artifacts from conversation X"

### Settings

- `FEEDBACK_AUTO_SAVE`: "off" (default), "verified_only", "all"
- `FEEDBACK_MIN_LENGTH`: Minimum response length to trigger save (default: 200 chars)
- `FEEDBACK_EXCLUDE_DOMAINS`: Domains where feedback is never saved (e.g., "testing")

## Implementation Notes

- Backend: `ingestFeedback()` already exists and works
- Frontend: Need per-conversation toggle state + save indicator
- Quality gate: Wire into existing `HallucinationReport` data
- Tagging: Add `conversation_id` to artifact metadata during ingest
- This is a UX + policy change, not an architecture change

## Not in Scope

- User ratings on responses (thumbs up/down) — separate feature
- Response editing before save — too complex for v1
- Selective paragraph saving — future enhancement
