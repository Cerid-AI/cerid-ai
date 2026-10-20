// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Public/community edition: the chat's confirm line renders nothing.
//
// The commercial build counts the memory candidates a conversation proposed and
// links them to the operator's Deck for confirmation. That count is served by
// the operator-record routes, which the community tree does not ship, so the
// shared chat panel gets a component with the same props and no output.

export const ChatConfirmLink: (props: { conversationId: string | null; streaming: boolean }) => null = () => null
