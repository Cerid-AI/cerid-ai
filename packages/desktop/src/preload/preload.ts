import { contextBridge, ipcRenderer } from 'electron'

/** Type-safe IPC bridge exposed to the renderer as `window.cerid` */
const ceridBridge = {
  docker: {
    /** Get Docker installation status and container states */
    status: (): Promise<{
      installed: boolean
      running: boolean
      containers: Array<{
        name: string
        id: string
        state: string
        status: string
        health: 'healthy' | 'unhealthy' | 'starting' | 'none'
      }>
    }> => ipcRenderer.invoke('docker:status'),

    /** Start all cerid containers */
    start: (): Promise<{ success: boolean; error?: string }> =>
      ipcRenderer.invoke('docker:start'),

    /** Stop all cerid containers */
    stop: (): Promise<{ success: boolean; error?: string }> =>
      ipcRenderer.invoke('docker:stop'),

    /** Restart a specific container by name */
    restart: (name: string): Promise<{ success: boolean; error?: string }> =>
      ipcRenderer.invoke('docker:restart', name),

    /** Start streaming logs from a container */
    logs: (name: string): Promise<{ success: boolean; error?: string }> =>
      ipcRenderer.invoke('docker:logs', name),

    /** Subscribe to log data events */
    onLogData: (
      callback: (data: { name: string; data: string }) => void,
    ): (() => void) => {
      const handler = (
        _event: Electron.IpcRendererEvent,
        payload: { name: string; data: string },
      ): void => {
        callback(payload)
      }
      ipcRenderer.on('docker:logs:data', handler)
      return () => {
        ipcRenderer.removeListener('docker:logs:data', handler)
      }
    },

    /** Subscribe to log error events */
    onLogError: (
      callback: (data: { name: string; error: string }) => void,
    ): (() => void) => {
      const handler = (
        _event: Electron.IpcRendererEvent,
        payload: { name: string; error: string },
      ): void => {
        callback(payload)
      }
      ipcRenderer.on('docker:logs:error', handler)
      return () => {
        ipcRenderer.removeListener('docker:logs:error', handler)
      }
    },
  },

  app: {
    /** Get the application version */
    version: (): Promise<string> => ipcRenderer.invoke('app:version'),

    /** Trigger an update check */
    checkUpdate: (): Promise<{ success: boolean }> =>
      ipcRenderer.invoke('app:checkUpdate'),

    /** Get the current platform */
    platform: (): Promise<NodeJS.Platform> =>
      ipcRenderer.invoke('app:platform'),

    /** Listen for update check requests from tray */
    onCheckUpdate: (callback: () => void): (() => void) => {
      const handler = (): void => {
        callback()
      }
      ipcRenderer.on('app:check-update', handler)
      return () => {
        ipcRenderer.removeListener('app:check-update', handler)
      }
    },
  },
} as const

contextBridge.exposeInMainWorld('cerid', ceridBridge)

// Type declaration for the renderer to consume
export type CeridBridge = typeof ceridBridge
