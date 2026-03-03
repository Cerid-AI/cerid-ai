// Copyright (c) 2026 Justin Michaels. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { Group, Panel, Separator } from "react-resizable-panels"

interface SplitPaneProps {
  left: React.ReactNode
  right: React.ReactNode
  showRight: boolean
  defaultSize?: number
}

export function SplitPane({ left, right, showRight, defaultSize = 70 }: SplitPaneProps) {
  if (!showRight) {
    return <div className="flex h-full min-h-0 flex-1 flex-col">{left}</div>
  }

  return (
    <Group orientation="horizontal" className="h-full" resizeTargetMinimumSize={22}>
      <Panel defaultSize={defaultSize} minSize={35} className="min-h-0">
        {left}
      </Panel>
      <Separator className="w-1 bg-border transition-colors hover:bg-primary/20 active:bg-primary/30" />
      <Panel defaultSize={100 - defaultSize} minSize={20}>
        {right}
      </Panel>
    </Group>
  )
}