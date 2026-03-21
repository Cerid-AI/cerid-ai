import { app, BrowserWindow, ipcMain, shell } from 'electron'
import path from 'node:path'
import {
  getContainerStatus,
  startContainers,
  stopContainers,
  restartContainer,
  streamLogs,
} from './docker'
import { createTray, destroyTray } from './tray'
import { initAutoUpdater, checkForUpdates, destroyUpdater } from './updater'

let mainWindow: BrowserWindow | null = null

/** Active log streams keyed by container name */
const activeLogStreams = new Map<string, { abort: () => void }>()

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
    show: false,
    backgroundColor: '#0a0a0a',
  })

  // Graceful show after content loads
  win.once('ready-to-show', () => {
    win.show()
  })

  // Open external links in the system browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url).catch(console.error)
    return { action: 'deny' }
  })

  // Load the React GUI
  if (process.env.NODE_ENV === 'development' || !app.isPackaged) {
    const devUrl = process.env.VITE_DEV_SERVER_URL ?? 'http://localhost:5173'
    win.loadURL(devUrl).catch((err) => {
      console.error('[desktop:main] Failed to load dev URL:', err)
    })
  } else {
    const indexPath = path.join(__dirname, '../web/index.html')
    win.loadFile(indexPath).catch((err) => {
      console.error('[desktop:main] Failed to load production file:', err)
    })
  }

  win.on('closed', () => {
    mainWindow = null
  })

  return win
}

// ── IPC Handlers ────────────────────────────────────────────────────────────

function registerIpcHandlers(): void {
  ipcMain.handle('docker:status', async () => {
    return getContainerStatus()
  })

  ipcMain.handle('docker:start', async () => {
    return startContainers()
  })

  ipcMain.handle('docker:stop', async () => {
    return stopContainers()
  })

  ipcMain.handle('docker:restart', async (_event, name: string) => {
    if (typeof name !== 'string' || name.length === 0) {
      return { success: false, error: 'Container name is required' }
    }
    return restartContainer(name)
  })

  ipcMain.handle('docker:logs', async (event, name: string) => {
    if (typeof name !== 'string' || name.length === 0) {
      return { success: false, error: 'Container name is required' }
    }

    // Abort any existing stream for this container
    const existing = activeLogStreams.get(name)
    if (existing) {
      existing.abort()
      activeLogStreams.delete(name)
    }

    const handle = await streamLogs(
      name,
      (chunk) => {
        // Send log chunks to the renderer
        event.sender.send('docker:logs:data', { name, data: chunk })
      },
      (error) => {
        event.sender.send('docker:logs:error', { name, error })
        activeLogStreams.delete(name)
      },
    )

    activeLogStreams.set(name, handle)
    return { success: true }
  })

  ipcMain.handle('app:version', () => {
    return app.getVersion()
  })

  ipcMain.handle('app:checkUpdate', () => {
    checkForUpdates()
    return { success: true }
  })

  ipcMain.handle('app:platform', () => {
    return process.platform
  })
}

// ── App Lifecycle ───────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  registerIpcHandlers()

  mainWindow = createWindow()

  await createTray(mainWindow)
  initAutoUpdater(mainWindow)

  // macOS: re-create window when dock icon clicked
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow()
    } else {
      mainWindow?.show()
      mainWindow?.focus()
    }
  })
})

app.on('window-all-closed', () => {
  // On macOS, keep the app running in the tray
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  // Clean up all active log streams
  for (const [, handle] of activeLogStreams) {
    handle.abort()
  }
  activeLogStreams.clear()

  destroyTray()
  destroyUpdater()
})
