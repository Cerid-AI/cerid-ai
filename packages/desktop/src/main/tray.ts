import { app, Menu, nativeImage, Tray } from 'electron'
import path from 'node:path'
import { getContainerStatus, startContainers, stopContainers } from './docker'
import type { BrowserWindow } from 'electron'

let tray: Tray | null = null
let pollInterval: ReturnType<typeof setInterval> | null = null

type TrayColor = 'green' | 'yellow' | 'red'

function getTrayIconPath(color: TrayColor): string {
  // In production, icons are in resources/. In dev, use a fallback.
  const resourcesPath = app.isPackaged
    ? path.join(process.resourcesPath, 'resources')
    : path.join(__dirname, '..', '..', 'resources')

  return path.join(resourcesPath, `tray-${color}.png`)
}

function createTrayIcon(color: TrayColor): Electron.NativeImage {
  const iconPath = getTrayIconPath(color)
  try {
    const icon = nativeImage.createFromPath(iconPath)
    if (!icon.isEmpty()) {
      // Resize for tray (16x16 on macOS)
      return icon.resize({ width: 16, height: 16 })
    }
  } catch {
    // Fallback: create a minimal colored icon programmatically
  }

  // Fallback: create a simple colored dot as a 16x16 PNG via data URL
  return createFallbackIcon(color)
}

function createFallbackIcon(color: TrayColor): Electron.NativeImage {
  const colorMap: Record<TrayColor, string> = {
    green: '#22c55e',
    yellow: '#eab308',
    red: '#ef4444',
  }
  const hex = colorMap[color]

  // Create a simple 16x16 circle icon using a data URL SVG
  const svg = `<svg width="16" height="16" xmlns="http://www.w3.org/2000/svg">
    <circle cx="8" cy="8" r="6" fill="${hex}" />
  </svg>`

  const encoded = Buffer.from(svg).toString('base64')
  return nativeImage.createFromDataURL(`data:image/svg+xml;base64,${encoded}`)
}

async function determineColor(): Promise<TrayColor> {
  try {
    const status = await getContainerStatus()
    if (!status.installed || !status.running) return 'red'
    if (status.containers.length === 0) return 'red'

    const allHealthy = status.containers.every(
      (c) => c.state === 'running' && (c.health === 'healthy' || c.health === 'none'),
    )
    if (allHealthy) return 'green'

    const someRunning = status.containers.some((c) => c.state === 'running')
    if (someRunning) return 'yellow'

    return 'red'
  } catch {
    return 'red'
  }
}

function buildContextMenu(
  mainWindow: BrowserWindow | null,
  color: TrayColor,
): Menu {
  const statusLabels: Record<TrayColor, string> = {
    green: 'All Services Healthy',
    yellow: 'Some Services Degraded',
    red: 'Services Offline',
  }

  return Menu.buildFromTemplate([
    {
      label: statusLabels[color],
      enabled: false,
    },
    { type: 'separator' },
    {
      label: 'Open Cerid AI',
      click: () => {
        if (mainWindow) {
          mainWindow.show()
          mainWindow.focus()
        }
      },
    },
    { type: 'separator' },
    {
      label: 'Start Services',
      click: async () => {
        const result = await startContainers()
        if (!result.success) {
          console.error('[desktop:tray] Start failed:', result.error)
        }
      },
    },
    {
      label: 'Stop Services',
      click: async () => {
        const result = await stopContainers()
        if (!result.success) {
          console.error('[desktop:tray] Stop failed:', result.error)
        }
      },
    },
    { type: 'separator' },
    {
      label: 'Check for Updates',
      click: () => {
        mainWindow?.webContents.send('app:check-update')
      },
    },
    {
      label: 'Quit',
      click: () => {
        app.quit()
      },
    },
  ])
}

export async function createTray(mainWindow: BrowserWindow | null): Promise<void> {
  const color = await determineColor()
  const icon = createTrayIcon(color)

  tray = new Tray(icon)
  tray.setToolTip('Cerid AI')
  tray.setContextMenu(buildContextMenu(mainWindow, color))

  // Double-click on tray opens main window (Windows behavior)
  tray.on('double-click', () => {
    if (mainWindow) {
      mainWindow.show()
      mainWindow.focus()
    }
  })

  // Poll container health every 15 seconds and update tray
  pollInterval = setInterval(async () => {
    const newColor = await determineColor()
    const newIcon = createTrayIcon(newColor)
    tray?.setImage(newIcon)
    tray?.setContextMenu(buildContextMenu(mainWindow, newColor))
  }, 15_000)
}

export function destroyTray(): void {
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
  if (tray) {
    tray.destroy()
    tray = null
  }
}
