import { autoUpdater } from 'electron-updater'
import { app, dialog, BrowserWindow } from 'electron'

/** Interval handle for periodic update checks */
let checkInterval: ReturnType<typeof setInterval> | null = null

/** 24 hours in milliseconds */
const UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000

export function initAutoUpdater(mainWindow: BrowserWindow | null): void {
  // Configure update feed — GitHub Releases for sunrunnerfire/cerid-ai
  autoUpdater.setFeedURL({
    provider: 'github',
    owner: 'sunrunnerfire',
    repo: 'cerid-ai',
  })

  // Don't auto-download; let user decide
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('checking-for-update', () => {
    console.log('[desktop:updater] Checking for updates...')
  })

  autoUpdater.on('update-available', (info) => {
    console.log('[desktop:updater] Update available:', info.version)

    dialog
      .showMessageBox({
        type: 'info',
        title: 'Update Available',
        message: `Cerid AI v${info.version} is available.`,
        detail: 'Would you like to download and install it?',
        buttons: ['Download', 'Later'],
        defaultId: 0,
        cancelId: 1,
      })
      .then((result) => {
        if (result.response === 0) {
          autoUpdater.downloadUpdate().catch((err) => {
            console.error('[desktop:updater] Download failed:', err)
          })
        }
      })
      .catch((err) => {
        console.error('[desktop:updater] Dialog error:', err)
      })
  })

  autoUpdater.on('update-not-available', () => {
    console.log('[desktop:updater] App is up to date.')
  })

  autoUpdater.on('download-progress', (progress) => {
    const percent = Math.round(progress.percent)
    console.log(`[desktop:updater] Download progress: ${percent}%`)
    mainWindow?.setProgressBar(percent / 100)
  })

  autoUpdater.on('update-downloaded', () => {
    console.log('[desktop:updater] Update downloaded.')
    mainWindow?.setProgressBar(-1) // Remove progress bar

    dialog
      .showMessageBox({
        type: 'info',
        title: 'Update Ready',
        message: 'Update has been downloaded.',
        detail: 'Restart Cerid AI now to apply the update?',
        buttons: ['Restart Now', 'Later'],
        defaultId: 0,
        cancelId: 1,
      })
      .then((result) => {
        if (result.response === 0) {
          autoUpdater.quitAndInstall()
        }
      })
      .catch((err) => {
        console.error('[desktop:updater] Dialog error:', err)
      })
  })

  autoUpdater.on('error', (error) => {
    console.error('[desktop:updater] Error:', error.message)
  })

  // Initial check after a short delay (let the app settle)
  setTimeout(() => {
    checkForUpdates()
  }, 10_000)

  // Periodic check every 24 hours
  checkInterval = setInterval(() => {
    checkForUpdates()
  }, UPDATE_CHECK_INTERVAL_MS)
}

/** Trigger an update check. Safe to call multiple times. */
export function checkForUpdates(): void {
  if (!app.isPackaged) {
    console.log('[desktop:updater] Skipping update check in dev mode.')
    return
  }
  autoUpdater.checkForUpdates().catch((err) => {
    console.error('[desktop:updater] Check failed:', err)
  })
}

export function destroyUpdater(): void {
  if (checkInterval) {
    clearInterval(checkInterval)
    checkInterval = null
  }
}
