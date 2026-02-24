import { Group, Panel, Separator } from "react-resizable-panels"

interface SplitPaneProps {
  left: React.ReactNode
  right: React.ReactNode
  showRight: boolean
  defaultSize?: number
}

export function SplitPane({ left, right, showRight, defaultSize = 60 }: SplitPaneProps) {
  if (!showRight) {
    return <div className="flex h-full flex-1 flex-col">{left}</div>
  }

  return (
    <Group orientation="horizontal" className="h-full">
      <Panel defaultSize={defaultSize} minSize={35}>
        {left}
      </Panel>
      <Separator className="w-1 bg-border transition-colors hover:bg-primary/20 active:bg-primary/30" />
      <Panel defaultSize={100 - defaultSize} minSize={25}>
        {right}
      </Panel>
    </Group>
  )
}
