// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useClaimFeedback — per-claim thumbs rating hook (Phase R.1).
 *
 * Returns a `submit` function that POSTs to POST /sdk/v1/feedback via a
 * tanstack-react-query mutation.  Optimistic local state: the hook tracks
 * which sentiment the user chose so the UI can highlight the active thumb
 * immediately, then rolls back on error.
 *
 * Design constraints:
 * - Feedback is per-claim, never bundled.
 * - No vote-count display.  The mutation result (rating_id) is returned
 *   but callers should not surface it to users.
 * - Error handling is fire-and-forget from the user's perspective; the
 *   hook rolls back the optimistic state and logs via logSwallowedError.
 */

import { useState, useCallback, useRef } from "react"
import { useMutation } from "@tanstack/react-query"
import { submitClaimFeedbackV2, type ClaimSentiment } from "@/lib/api/feedback"
import { logSwallowedError } from "@/lib/log-swallowed"

export interface UseClaimFeedbackReturn {
  /**
   * Submit a sentiment rating for a single claim.
   * Optimistically updates the local `activeSentiment` state; rolls back
   * on network or server error.
   */
  submit: (claimId: string, sentiment: ClaimSentiment) => Promise<void>
  /**
   * The currently-active sentiment for the most recently rated claim, or
   * ``null`` if no rating has been submitted yet (or after a rollback).
   */
  activeSentiment: ClaimSentiment | null
  /** True while the mutation request is in flight. */
  isPending: boolean
}

interface FeedbackOptions {
  /** Optional session identifier for anonymous idempotency. */
  sessionId?: string
  /** Optional authenticated user identifier. */
  userId?: string
}

/**
 * Hook for submitting per-claim user feedback.
 *
 * @param options  Optional ``sessionId`` / ``userId`` for idempotency.
 *
 * @example
 * ```tsx
 * const { submit, activeSentiment, isPending } = useClaimFeedback({ sessionId: "sess-abc" })
 *
 * <button onClick={() => submit(claim.id, 1)} disabled={isPending}>
 *   <ThumbsUp className={activeSentiment === 1 ? "text-green-500" : ""} />
 * </button>
 * ```
 */
export function useClaimFeedback(options: FeedbackOptions = {}): UseClaimFeedbackReturn {
  const { sessionId, userId } = options
  const [activeSentiment, setActiveSentiment] = useState<ClaimSentiment | null>(null)

  // Use a ref to track the pre-mutation sentiment so onError always sees the
  // current value, avoiding the stale-closure problem with useMutation.
  const prevSentimentRef = useRef<ClaimSentiment | null>(null)

  const mutation = useMutation({
    mutationFn: ({
      claimId,
      sentiment,
    }: {
      claimId: string
      sentiment: ClaimSentiment
    }) =>
      submitClaimFeedbackV2({
        claim_id: claimId,
        sentiment,
        session_id: sessionId,
        user_id: userId,
      }),
    onMutate: ({ sentiment }) => {
      // Snapshot the current value before optimistically updating
      prevSentimentRef.current = activeSentiment
      setActiveSentiment(sentiment)
    },
    onError: (err) => {
      // Roll back to the snapshot captured in onMutate
      setActiveSentiment(prevSentimentRef.current)
      logSwallowedError(err instanceof Error ? err : new Error(String(err)), "use-claim-feedback")
    },
  })

  const submit = useCallback(
    async (claimId: string, sentiment: ClaimSentiment): Promise<void> => {
      await mutation.mutateAsync({ claimId, sentiment })
    },
    // mutation.mutateAsync is stable across renders
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mutation.mutateAsync, sessionId, userId],
  )

  return {
    submit,
    activeSentiment,
    isPending: mutation.isPending,
  }
}
