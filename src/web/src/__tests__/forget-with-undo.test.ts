// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from "sonner"
import { forgetWithUndo } from "@/lib/forget-with-undo"
import { ForgetConflictError } from "@/lib/api"
import type { Conversation } from "@/lib/types"

const convo = (id: string): Conversation => ({ id, title: id, messages: [], model: "m", createdAt: 1, updatedAt: 1 })

function deps(outcome: unknown) {
  return {
    conversations: [convo("c1"), convo("c2")],
    forget: vi.fn().mockResolvedValue(outcome),
    restore: vi.fn().mockResolvedValue(undefined),
  }
}

describe("forgetWithUndo", () => {
  beforeEach(() => { vi.clearAllMocks() })

  it("offers Undo after a move to Trash, restoring the removed conversations", async () => {
    const d = deps({ status: "done", result: { forget_id: "fg_1", state: "trashed", receipt: null } })
    await forgetWithUndo(d, ["c1"], "trash", [{ kind: "artifact", id: "a1" }])
    expect(d.forget).toHaveBeenCalledWith(["c1"], { mode: "trash", derived: [{ kind: "artifact", id: "a1" }] })
    const [message, options] = vi.mocked(toast.success).mock.calls[0] as [string, { action: { label: string; onClick: () => void } }]
    expect(message).toBe("Moved to Trash")
    expect(options.action.label).toBe("Undo")
    options.action.onClick()
    expect(d.restore).toHaveBeenCalledWith("fg_1", [convo("c1")])
  })

  it("explains a refused Undo", async () => {
    const d = deps({ status: "done", result: { forget_id: "fg_1", state: "trashed", receipt: null } })
    d.restore.mockRejectedValueOnce(new ForgetConflictError("erasing"))
    await forgetWithUndo(d, ["c1"], "trash", [])
    const options = vi.mocked(toast.success).mock.calls[0][1] as unknown as { action: { onClick: () => void } }
    options.action.onClick()
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith("This has started erasing and can't be restored."))
  })

  it("a permanent forget has no Undo", async () => {
    const d = deps({ status: "done", result: { forget_id: "fg_1", state: "purged", receipt: null } })
    await forgetWithUndo(d, ["c1", "c2"], "permanent", [])
    expect(toast.success).toHaveBeenCalledWith("Forgotten permanently")
  })

  it("a permanent forget still erasing says so", async () => {
    const d = deps({ status: "done", result: { forget_id: "fg_1", state: "trashed_pending", receipt: null } })
    await forgetWithUndo(d, ["c1"], "permanent", [])
    expect(toast.success).toHaveBeenCalledWith("Forgotten. Some stores are still erasing and will finish shortly.")
  })

  it("reports an unreachable server and a private-mode deferral", async () => {
    await forgetWithUndo(deps({ status: "failed" }), ["c1"], "trash", [])
    expect(toast.error).toHaveBeenCalledWith(
      "Couldn't reach the server. The chat will move to the Trash when it reconnects; related memories were not removed.",
    )
    await forgetWithUndo(deps({ status: "deferred" }), ["c1"], "trash", [])
    expect(toast.success).toHaveBeenCalledWith("Deleted on this device", {
      description: "It moves to the Trash when Private Mode is off.",
    })
    await forgetWithUndo(deps({ status: "deferred" }), ["c1"], "permanent", [{ kind: "artifact", id: "a1" }])
    expect(toast.success).toHaveBeenLastCalledWith("Deleted on this device", {
      description: "It moves to the Trash when Private Mode is off. Erase it from Settings → Data then; related memories are kept until you do.",
    })
  })

  it("shows why the server refused", async () => {
    await forgetWithUndo(deps({ status: "refused", message: "Forgetting needs an admin in multi-user mode" }), ["c1"], "trash", [])
    expect(toast.error).toHaveBeenCalledWith("Couldn't delete: Forgetting needs an admin in multi-user mode")
  })
})
