import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import path from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
import {
  getContainerStatus,
  startContainers,
  stopContainers,
  restartContainer,
  streamLogs,
  getDockerDesktopDownloadUrl,
  startDockerDesktop,
  pullImagesWithProgress,
  getSystemRequirements,
  isDockerInstalled,
  isDockerRunning,
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

  ipcMain.handle('docker:downloadUrl', () => {
    return getDockerDesktopDownloadUrl()
  })

  ipcMain.handle('docker:startDesktop', () => {
    return startDockerDesktop()
  })

  ipcMain.handle('docker:pullImages', (event) => {
    pullImagesWithProgress((service, status, percent) => {
      event.sender.send('docker:pull:progress', { service, status, percent })
    }).catch((error) => {
      console.error('[desktop:main] Pull images failed:', error)
    })
    return { success: true }
  })

  ipcMain.handle('system:requirements', () => {
    return getSystemRequirements()
  })

  ipcMain.handle('app:openExternal', (_event, url: string) => {
    if (typeof url !== 'string' || url.length === 0) {
      return { success: false, error: 'URL is required' }
    }
    shell.openExternal(url).catch(console.error)
    return { success: true }
  })

  ipcMain.handle('app:exportData', async () => {
    const result = await dialog.showOpenDialog({
      title: 'Choose export destination',
      buttonLabel: 'Export Here',
      properties: ['openDirectory', 'createDirectory'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return { success: false, error: 'cancelled' }
    }

    const dest = result.filePaths[0]
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    const exportDir = path.join(dest, `cerid-export-${timestamp}`)

    try {
      const repoRoot = process.env.CERID_REPO_ROOT ?? path.resolve(__dirname, '..', '..', '..', '..')

      // Run the backup script if it exists, otherwise copy data dirs directly
      try {
        await execFileAsync('bash', [path.join(repoRoot, 'scripts', 'backup-kb.sh')], {
          cwd: repoRoot,
          timeout: 120_000,
          env: { ...process.env, BACKUP_DIR: exportDir },
        })
      } catch {
        // Fallback: copy data directories manually
        const { mkdirSync, cpSync } = await import('node:fs')
        mkdirSync(exportDir, { recursive: true })

        const dataDirs = [
          { src: path.join(repoRoot, 'stacks', 'infrastructure', 'data', 'neo4j'), name: 'neo4j' },
          { src: path.join(repoRoot, 'stacks', 'infrastructure', 'data', 'chroma'), name: 'chroma' },
        ]

        for (const { src, name } of dataDirs) {
          try {
            cpSync(src, path.join(exportDir, name), { recursive: true })
          } catch (e) {
            console.warn(`[desktop:export] Could not copy ${name}:`, e)
          }
        }
      }

      return { success: true, path: exportDir }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      return { success: false, error: message }
    }
  })
}

// ── App Lifecycle ───────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  registerIpcHandlers()

  mainWindow = createWindow()

  await createTray(mainWindow)
  initAutoUpdater(mainWindow)

  // Best-effort Docker Desktop startup — non-blocking
  // The React GUI setup wizard handles the rest if this fails
  try {
    const installed = await isDockerInstalled()
    if (installed) {
      const running = await isDockerRunning()
      if (!running) {
        console.log('[desktop:main] Docker installed but not running, attempting to start Docker Desktop...')
        const result = await startDockerDesktop()
        if (result.success) {
          console.log('[desktop:main] Docker Desktop started successfully')
        } else {
          console.warn('[desktop:main] Could not auto-start Docker Desktop:', result.error)
        }
      }
    } else {
      console.log('[desktop:main] Docker not installed — GUI setup wizard will guide the user')
    }
  } catch (error) {
    console.warn('[desktop:main] Docker pre-check failed (non-fatal):', error)
  }

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

let isQuitting = false

app.on('before-quit', async (event) => {
  if (isQuitting) return

  // Show quit confirmation with export option on first quit attempt
  event.preventDefault()

  const { response } = await dialog.showMessageBox({
    type: 'question',
    title: 'Quit Cerid AI',
    message: 'Would you like to export your knowledge base before quitting?',
    detail: 'Your data is stored in Docker volumes and will persist for next launch. Export creates a portable backup you can restore later.',
    buttons: ['Quit', 'Export & Quit', 'Cancel'],
    defaultId: 0,
    cancelId: 2,
  })

  if (response === 2) return // Cancel

  if (response === 1) {
    // Export & Quit — trigger the export IPC from main process
    const exportResult = await dialog.showOpenDialog({
      title: 'Choose export destination',
      buttonLabel: 'Export Here',
      properties: ['openDirectory', 'createDirectory'],
    })

    if (!exportResult.canceled && exportResult.filePaths.length > 0) {
      const dest = exportResult.filePaths[0]
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      const exportDir = path.join(dest, `cerid-export-${timestamp}`)
      const repoRoot = process.env.CERID_REPO_ROOT ?? path.resolve(__dirname, '..', '..', '..', '..')

      try {
        const { mkdirSync, cpSync } = await import('node:fs')
        mkdirSync(exportDir, { recursive: true })

        for (const name of ['neo4j', 'chroma']) {
          try {
            cpSync(
              path.join(repoRoot, 'stacks', 'infrastructure', 'data', name),
              path.join(exportDir, name),
              { recursive: true },
            )
          } catch { /* skip missing dirs */ }
        }

        dialog.showMessageBoxSync({
          type: 'info',
          title: 'Export Complete',
          message: `Knowledge base exported to:\n${exportDir}`,
        })
      } catch (error) {
        dialog.showMessageBoxSync({
          type: 'warning',
          title: 'Export Failed',
          message: `Could not export data: ${error instanceof Error ? error.message : String(error)}`,
        })
      }
    }
  }

  // Proceed with quit
  isQuitting = true

  // Clean up all active log streams
  for (const [, handle] of activeLogStreams) {
    handle.abort()
  }
  activeLogStreams.clear()

  destroyTray()
  destroyUpdater()

  app.quit()
})
