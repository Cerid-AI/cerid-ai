// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Runs the real container entrypoint under `sh` with hostile env values and
// asserts the emitted env-config.js keeps them as inert data (FE-10). The
// script's output paths are env-overridable precisely so this test can point
// them into a tmpdir; a stub `nginx` on PATH satisfies the final exec.

import { afterEach, describe, expect, it } from 'vitest'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import vm from 'node:vm'

const execFileAsync = promisify(execFile)
const SCRIPT = path.resolve(__dirname, '..', '..', 'docker-entrypoint.sh')

const tempDirs: string[] = []

function setup() {
  const dir = mkdtempSync(path.join(tmpdir(), 'entrypoint-test-'))
  tempDirs.push(dir)
  const bin = path.join(dir, 'bin')
  mkdirSync(bin)
  const nginxStub = path.join(bin, 'nginx')
  writeFileSync(nginxStub, '#!/bin/sh\nexit 0\n')
  chmodSync(nginxStub, 0o755)
  writeFileSync(path.join(dir, 'index.html'), '<html><head></head><body></body></html>\n')
  const env: NodeJS.ProcessEnv = {
    PATH: `${bin}:${process.env.PATH}`,
    CERID_HTML_PATH: path.join(dir, 'index.html'),
    CERID_ENV_JS_PATH: path.join(dir, 'env-config.js'),
    CERID_VERSION_JS_PATH: path.join(dir, 'version.json'),
    CERID_KEY_INC_PATH: path.join(dir, 'cerid-api-key.inc'),
  }
  return { dir, env }
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true })
  }
})

describe('docker-entrypoint.sh', () => {
  it('emits hostile env values as inert, verbatim data in env-config.js', async () => {
    const { dir, env } = setup()
    const hostile = {
      VITE_MCP_URL: '/api/mcp"; window.pwned = 1; //',
      VITE_BIFROST_URL: '/api/bifrost\\"; window.pwned = 2; //',
      VITE_SENTRY_DSN_WEB: '`touch injected-backtick`',
      VITE_APP_VERSION: '";alert(1);// $(touch injected-subst)',
    }

    await execFileAsync('sh', [SCRIPT], { cwd: dir, env: { ...env, ...hostile } })

    const emitted = readFileSync(path.join(dir, 'env-config.js'), 'utf8')
    const context: { window: { __ENV__?: Record<string, string>; pwned?: number } } = {
      window: {},
    }
    vm.createContext(context)
    // Must parse as JS and assign — an unescaped quote would throw or execute
    new vm.Script(emitted).runInContext(context)

    expect(context.window.pwned).toBeUndefined()
    expect(context.window.__ENV__).toBeDefined()
    expect(context.window.__ENV__?.VITE_MCP_URL).toBe(hostile.VITE_MCP_URL)
    expect(context.window.__ENV__?.VITE_BIFROST_URL).toBe(hostile.VITE_BIFROST_URL)
    expect(context.window.__ENV__?.VITE_SENTRY_DSN_WEB).toBe(hostile.VITE_SENTRY_DSN_WEB)
    expect(context.window.__ENV__?.VITE_APP_VERSION).toBe(hostile.VITE_APP_VERSION)

    // Shell payloads stayed data — nothing executed at container start
    expect(existsSync(path.join(dir, 'injected-backtick'))).toBe(false)
    expect(existsSync(path.join(dir, 'injected-subst'))).toBe(false)
  })

  it('refuses to start when the API key would inject nginx directives', async () => {
    const { dir, env } = setup()
    const hostileKey = 'abc";\nadd_header X-Injected 1;\nproxy_set_header X-API-Key "'

    await expect(
      execFileAsync('sh', [SCRIPT], {
        cwd: dir,
        env: { ...env, VITE_CERID_API_KEY: hostileKey },
      }),
    ).rejects.toMatchObject({ code: 1 })

    // Nothing was written before the refusal
    expect(existsSync(path.join(dir, 'cerid-api-key.inc'))).toBe(false)
  })

  it('quotes a well-formed API key into the nginx include unchanged', async () => {
    const { dir, env } = setup()

    await execFileAsync('sh', [SCRIPT], {
      cwd: dir,
      env: { ...env, VITE_CERID_API_KEY: 'sk-cerid-0123456789' }, // pragma: allowlist secret
    })

    const include = readFileSync(path.join(dir, 'cerid-api-key.inc'), 'utf8')
    expect(include).toBe('proxy_set_header X-API-Key "sk-cerid-0123456789";\n')
  })
})
