import Dockerode from 'dockerode'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'

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
