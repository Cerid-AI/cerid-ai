import Dockerode from 'dockerode'
import { execFile, spawn } from 'node:child_process'
import { promisify } from 'node:util'
import os from 'node:os'
import path from 'node:path'
import { statfs } from 'node:fs/promises'

const execFileAsync = promisify(execFile)

const docker = new Dockerode()

/** Cerid container name prefix used in docker-compose.yml */
const CERID_PREFIX = 'cerid-ai'

/** Path to the repo root (two levels up from dist/main/) */
function getRepoRoot(): string {
  // In dev: packages/desktop/ — in prod: packaged app resources
  // The compose file lives at the repo root
  if (process.env.CERID_REPO_ROOT) {
    return process.env.CERID_REPO_ROOT
  }
  // Default: assume we're in packages/desktop/dist/main/
  return path.resolve(__dirname, '..', '..', '..', '..')
}

export interface ContainerInfo {
  name: string
  id: string
  state: string
  status: string
  health: 'healthy' | 'unhealthy' | 'starting' | 'none'
}

export interface DockerStatus {
  installed: boolean
  running: boolean
  containers: ContainerInfo[]
}

/** Check if the docker CLI binary is accessible */
export async function isDockerInstalled(): Promise<boolean> {
  try {
    await execFileAsync('docker', ['--version'])
    return true
  } catch {
    return false
  }
}

/** Check if the Docker daemon is responsive */
export async function isDockerRunning(): Promise<boolean> {
  try {
    await docker.ping()
    return true
  } catch {
    return false
  }
}

/** Parse health status from container inspection data */
function parseHealth(
  container: Dockerode.ContainerInfo,
): ContainerInfo['health'] {
  const healthStatus = container.Status?.toLowerCase() ?? ''
  if (healthStatus.includes('healthy') && !healthStatus.includes('unhealthy')) {
    return 'healthy'
  }
  if (healthStatus.includes('unhealthy')) return 'unhealthy'
  if (healthStatus.includes('starting') || healthStatus.includes('health: starting')) {
    return 'starting'
  }
  return 'none'
}

/** List cerid containers and their states */
export async function getContainerStatus(): Promise<DockerStatus> {
  const installed = await isDockerInstalled()
  if (!installed) {
    return { installed: false, running: false, containers: [] }
  }

  const running = await isDockerRunning()
  if (!running) {
    return { installed: true, running: false, containers: [] }
  }

  try {
    const containers = await docker.listContainers({ all: true })
    const ceridContainers: ContainerInfo[] = containers
      .filter((c) => {
        const names = c.Names?.map((n) => n.replace(/^\//, '')) ?? []
        return names.some(
          (name) =>
            name.startsWith(CERID_PREFIX) || name.includes('cerid'),
        )
      })
      .map((c) => ({
        name: (c.Names?.[0] ?? '').replace(/^\//, ''),
        id: c.Id.slice(0, 12),
        state: c.State ?? 'unknown',
        status: c.Status ?? 'unknown',
        health: parseHealth(c),
      }))

    return { installed: true, running: true, containers: ceridContainers }
  } catch (error) {
    console.error('[desktop:docker] Failed to list containers:', error)
    return { installed: true, running: true, containers: [] }
  }
}

/** Start all cerid containers via docker compose */
export async function startContainers(): Promise<{ success: boolean; error?: string }> {
  try {
    const repoRoot = getRepoRoot()
    await execFileAsync('docker', ['compose', 'up', '-d'], {
      cwd: repoRoot,
      timeout: 120_000,
    })
    return { success: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error('[desktop:docker] Failed to start containers:', message)
    return { success: false, error: message }
  }
}

/** Stop all cerid containers via docker compose */
export async function stopContainers(): Promise<{ success: boolean; error?: string }> {
  try {
    const repoRoot = getRepoRoot()
    await execFileAsync('docker', ['compose', 'down'], {
      cwd: repoRoot,
      timeout: 60_000,
    })
    return { success: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error('[desktop:docker] Failed to stop containers:', message)
    return { success: false, error: message }
  }
}

/** Restart a specific container by name */
export async function restartContainer(
  name: string,
): Promise<{ success: boolean; error?: string }> {
  try {
    const containers = await docker.listContainers({ all: true })
    const target = containers.find((c) =>
      c.Names?.some((n) => n.replace(/^\//, '') === name),
    )
    if (!target) {
      return { success: false, error: `Container "${name}" not found` }
    }
    const container = docker.getContainer(target.Id)
    await container.restart({ t: 10 })
    return { success: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error(`[desktop:docker] Failed to restart ${name}:`, message)
    return { success: false, error: message }
  }
}

// ── Enhanced Docker Detection + Guided Install ──────────────────────────────

/** Returns a platform-specific Docker Desktop download URL */
export function getDockerDesktopDownloadUrl(): string {
  if (process.platform === 'darwin') {
    return os.arch() === 'arm64'
      ? 'https://desktop.docker.com/mac/main/arm64/Docker.dmg'
      : 'https://desktop.docker.com/mac/main/amd64/Docker.dmg'
  }
  if (process.platform === 'win32') {
    return 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
  }
  // Linux fallback — Docker Engine install docs
  return 'https://docs.docker.com/engine/install/'
}

/** Attempt to launch Docker Desktop and wait for the daemon to respond */
export async function startDockerDesktop(): Promise<{ success: boolean; error?: string }> {
  try {
    // Check if already running
    const alreadyRunning = await isDockerRunning()
    if (alreadyRunning) {
      return { success: true }
    }

    // Launch Docker Desktop
    if (process.platform === 'darwin') {
      await execFileAsync('open', ['-a', 'Docker'])
    } else if (process.platform === 'win32') {
      const exePath = 'C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe'
      spawn(exePath, [], { detached: true, stdio: 'ignore' }).unref()
    } else {
      return { success: false, error: 'Unsupported platform for Docker Desktop launch' }
    }

    // Poll docker.ping() every 2s for up to 30s
    const maxAttempts = 15
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      try {
        await docker.ping()
        return { success: true }
      } catch {
        // Daemon not ready yet, keep polling
      }
    }

    return { success: false, error: 'Docker Desktop started but daemon did not respond within 30 seconds' }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error('[desktop:docker] Failed to start Docker Desktop:', message)
    return { success: false, error: message }
  }
}

/** Images to pull for a complete cerid stack */
const CERID_IMAGES = [
  'neo4j:5',
  'chromadb/chroma:0.5.23',
  'redis:7-alpine',
  'maximhq/bifrost:latest',
  'python:3.11-slim',
  'nginx:1.29-alpine',
] as const

export type PullProgressCallback = (service: string, status: string, percent: number) => void

/** Pull all required Docker images sequentially with progress reporting */
export async function pullImagesWithProgress(
  onProgress: PullProgressCallback,
): Promise<{ success: boolean; error?: string }> {
  for (const image of CERID_IMAGES) {
    const serviceName = image.split(':')[0].split('/').pop() ?? image
    onProgress(serviceName, 'pulling', 0)

    try {
      const stream = await docker.pull(image)

      await new Promise<void>((resolve, reject) => {
        // Track layer progress for percent estimation
        const layerProgress = new Map<string, { current: number; total: number }>()

        docker.modem.followProgress(
          stream,
          (err: Error | null) => {
            if (err) {
              reject(err)
            } else {
              onProgress(serviceName, 'complete', 100)
              resolve()
            }
          },
          (event: { id?: string; status?: string; progressDetail?: { current?: number; total?: number } }) => {
            // Parse progress from Docker pull events
            if (event.progressDetail?.total && event.id) {
              layerProgress.set(event.id, {
                current: event.progressDetail.current ?? 0,
                total: event.progressDetail.total,
              })

              // Calculate aggregate percent across all layers
              let totalBytes = 0
              let downloadedBytes = 0
              for (const [, layer] of layerProgress) {
                totalBytes += layer.total
                downloadedBytes += layer.current
              }

              const percent = totalBytes > 0 ? Math.round((downloadedBytes / totalBytes) * 100) : 0
              onProgress(serviceName, event.status ?? 'downloading', Math.min(percent, 99))
            } else if (event.status) {
              onProgress(serviceName, event.status, -1)
            }
          },
        )
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      console.error(`[desktop:docker] Failed to pull ${image}:`, message)
      onProgress(serviceName, 'error', -1)
      return { success: false, error: `Failed to pull ${image}: ${message}` }
    }
  }

  return { success: true }
}

export interface SystemRequirements {
  ram_gb: number
  disk_free_gb: number
  ram_sufficient: boolean
  disk_sufficient: boolean
}

/** Check if the machine meets minimum requirements to run cerid */
export async function getSystemRequirements(): Promise<SystemRequirements> {
  const ramBytes = os.totalmem()
  const ram_gb = Math.round((ramBytes / (1024 ** 3)) * 10) / 10

  let disk_free_gb = 0
  try {
    // Check free space on the root filesystem (where Docker stores data)
    const stats = await statfs(process.platform === 'win32' ? 'C:\\' : '/')
    disk_free_gb = Math.round((Number(stats.bfree) * Number(stats.bsize) / (1024 ** 3)) * 10) / 10
  } catch {
    console.error('[desktop:docker] Failed to check disk space')
  }

  return {
    ram_gb,
    disk_free_gb,
    ram_sufficient: ram_gb >= 8,
    disk_sufficient: disk_free_gb >= 10,
  }
}

/** Stream logs from a specific container. Returns an abort function. */
export async function streamLogs(
  name: string,
  onData: (chunk: string) => void,
  onError: (error: string) => void,
): Promise<{ abort: () => void }> {
  try {
    const containers = await docker.listContainers({ all: true })
    const target = containers.find((c) =>
      c.Names?.some((n) => n.replace(/^\//, '') === name),
    )
    if (!target) {
      onError(`Container "${name}" not found`)
      return { abort: () => {} }
    }

    const container = docker.getContainer(target.Id)
    const stream = await container.logs({
      follow: true,
      stdout: true,
      stderr: true,
      tail: 200,
      timestamps: true,
    })

    // Dockerode returns a readable stream for follow mode
    const readable = stream as unknown as NodeJS.ReadableStream
    let aborted = false

    readable.on('data', (chunk: Buffer) => {
      if (!aborted) {
        // Docker stream protocol: first 8 bytes are header, rest is payload
        // For simplicity, convert entire buffer and trim control chars
        const text = chunk.toString('utf8').replace(/[\x00-\x08]/g, '')
        onData(text)
      }
    })

    readable.on('error', (err: Error) => {
      if (!aborted) {
        onError(err.message)
      }
    })

    return {
      abort: () => {
        aborted = true
        if ('destroy' in readable && typeof readable.destroy === 'function') {
          readable.destroy()
        }
      },
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    onError(message)
    return { abort: () => {} }
  }
}
