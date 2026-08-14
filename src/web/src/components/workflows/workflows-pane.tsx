// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { Workflow } from "@/lib/types"
import WorkflowList from "./workflow-list"
import WorkflowEditor from "./workflow-editor"

/**
 * WorkflowsPane — owns list <-> editor navigation for the Visual Workflow
 * Builder (Phase 50). `editing` is the pane's only piece of state:
 *   - null    -> list
 *   - "new"   -> editor seeded with a blank workflow
 *   - Workflow -> editor seeded with that workflow (edit-in-place)
 *
 * Duplicate seeds the editor with a copy of the source workflow's nodes/
 * edges/name but a blank id, so Save always calls createWorkflow (never
 * updateWorkflow) and the result is a genuinely new workflow.
 */
export default function WorkflowsPane() {
  const [editing, setEditing] = useState<Workflow | null | "new">(null)
  const queryClient = useQueryClient()

  const returnToList = () => {
    setEditing(null)
    void queryClient.invalidateQueries({ queryKey: ["workflows"] })
  }

  // UX-21: Save must not eject the user from the builder mid-edit. Keep the
  // editor open on the saved workflow (which now has an id, so Run unlocks
  // for a first-time save) and refresh the list cache in the background.
  const stayAfterSave = (saved: Workflow) => {
    setEditing(saved)
    void queryClient.invalidateQueries({ queryKey: ["workflows"] })
  }

  if (editing === null) {
    return (
      <WorkflowList
        onEdit={(wf) => setEditing(wf)}
        onCreate={() => setEditing("new")}
        onDuplicate={(wf) => setEditing({ ...wf, id: "", name: `${wf.name} (copy)` })}
      />
    )
  }

  return (
    <WorkflowEditor
      workflow={editing === "new" ? null : editing}
      onSave={stayAfterSave}
      onBack={returnToList}
    />
  )
}
