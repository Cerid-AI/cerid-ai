// Packaged-app first-run smoke — driven over the Chrome DevTools Protocol
// against a real installed Cerid AI.app, not a dev server.
//
// Invoked by tests/beta/desktop-smoke.sh, which owns the app's process
// lifecycle (launch, quit, relaunch). This script only talks to whatever
// is listening on 127.0.0.1:9222 and prints one `PASS|FAIL|SKIP <name>`
// line per check.
//
// Runs in two stages because check 6 ("relaunch") requires the app to
// actually quit and restart, which drops the CDP connection this process
// holds. `--stage=initial` runs checks 1-5 and hands off to the shell
// wrapper for the quit/relaunch; `--stage=post-relaunch` (a second `node`
// invocation, after the wrapper relaunches the app) runs check 6 and the
// cleanup step, using tests/beta/reports/desktop-smoke-state.json to learn
// what the first stage found (and which conversation to delete).

import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import path from "node:path"
import fs from "node:fs"
import os from "node:os"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const E2E_DIR = path.resolve(__dirname, "..", "e2e")
const STATE_FILE = path.resolve(__dirname, "..", "reports", "desktop-smoke-state.json")

const CDP_URL = "http://127.0.0.1:9222"
const MCP_BASE = process.env.CERID_MCP_BASE || "http://localhost:8888"
const API_KEY = process.env.CERID_API_KEY || ""
const APP_SUPPORT_DIR = path.join(os.homedir(), "Library", "Application Support", "cerid-desktop")
const TEST_QUESTION = "In one sentence, what is a token bucket rate limiter?"

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const results = []
function record(status, name, detail) {
  results.push({ status, name, detail })
  console.log(`${status} ${name}${detail ? ` — ${detail}` : ""}`)
}

function requirePlaywright() {
  // createRequire's base only needs to exist so Node can walk up to
  // tests/beta/e2e/node_modules (a symlink to the real install) — see
  // task facts for why NODE_PATH alone isn't reliable for an ESM import.
  const require = createRequire(path.join(E2E_DIR, "package.json"))
  return require("playwright")
}

async function waitForCdpReady(timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${CDP_URL}/json/version`)
      if (res.ok) return true
    } catch {
      // not up yet
    }
    await sleep(500)
  }
  return false
}

async function connectPage(chromium) {
  const browser = await chromium.connectOverCDP(CDP_URL)
  const context = browser.contexts()[0]
  let page = context.pages()[0]
  if (!page) page = await context.waitForEvent("page", { timeout: 15000 })
  await page.bringToFront().catch(() => {})
  return page
}

function apiHeaders(extra = {}) {
  const headers = { ...extra }
  if (API_KEY) headers["X-API-Key"] = API_KEY
  return headers
}

async function fetchConversationIds() {
  const res = await fetch(`${MCP_BASE}/user-state/conversations`, { headers: apiHeaders() })
  if (!res.ok) return null
  const data = await res.json()
  const list = Array.isArray(data) ? data : (data.conversations ?? [])
  return new Set(list.map((c) => c.id))
}

async function deleteConversation(id) {
  const res = await fetch(`${MCP_BASE}/user-state/conversations/${id}`, {
    method: "DELETE",
    headers: apiHeaders(),
  })
  return res.ok
}

/** Polls (rather than a single long waitFor) so a fresh install and an
 *  already-configured Mac are told apart quickly instead of always paying
 *  the full timeout for whichever screen doesn't show. */
async function detectInitialScreen(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if ((await page.getByText("Set up this Mac").first().count()) > 0) return "setup"
    if ((await page.getByRole("button", { name: /New Conversation/i }).count()) > 0) return "main-empty"
    if ((await page.getByLabel("Chat message input").count()) > 0) return "main-active"
    await sleep(400)
  }
  return "unknown"
}

async function runFirstRunAndPersisted(page) {
  try {
    const keyInput = page.getByPlaceholder(/CERID_API_KEY/i)
    await keyInput.waitFor({ state: "visible", timeout: 15000 })
    if (!API_KEY) throw new Error("CERID_API_KEY is not set in the environment")
    await keyInput.fill(API_KEY)

    await page.getByTestId("connection-test").click()

    // Broad match on purpose: the exact wording differs before/after Task
    // A1 (collapsed "Connected to http://localhost:8888" today vs. an
    // open-form "Connected (HTTP 200)" detail once the form stays open for
    // an unsaved key) — both are a successful probe.
    const connected = await page
      .getByText(/Connected/i)
      .first()
      .waitFor({ state: "visible", timeout: 15000 })
      .then(() => true)
      .catch(() => false)
    if (!connected) throw new Error("no connection confirmation appeared within 15s of Test connection")

    const saveBtn = page.getByTestId("connection-save")
    const saveVisible = await saveBtn.isVisible().catch(() => false)
    if (!saveVisible) {
      throw new Error(
        "Save & reconnect not visible after a successful test connection (Task A1 not in this build)",
      )
    }
    await saveBtn.click()

    // bridge.set() reloads the renderer; the reload lands back on wizard
    // step 0 with the now-stored key, which should collapse to the
    // connected view regardless of the A1 fix's presence.
    const reconnected = await page
      .getByTestId("connection-ok")
      .first()
      .waitFor({ state: "visible", timeout: 20000 })
      .then(() => true)
      .catch(() => false)
    if (!reconnected) throw new Error("did not return to a connected state after Save & reconnect reloaded")

    record("PASS", "first-run", "")
  } catch (e) {
    record("FAIL", "first-run", e.message)
  }

  const connFile = path.join(APP_SUPPORT_DIR, "connection.json")
  const secretsDir = path.join(APP_SUPPORT_DIR, "secrets")
  for (let i = 0; i < 10 && !fs.existsSync(connFile); i++) await sleep(500)
  const connExists = fs.existsSync(connFile)
  let secretsCount = 0
  try {
    secretsCount = fs.readdirSync(secretsDir).length
  } catch {
    // secrets dir absent — count stays 0
  }
  if (connExists && secretsCount >= 1) {
    record("PASS", "persisted", `connection.json present, ${secretsCount} secret file(s)`)
  } else {
    record(
      "FAIL",
      "persisted",
      `connection.json ${connExists ? "present" : "missing"}, secrets dir has ${secretsCount} file(s)`,
    )
  }
}

async function runPermissions(page) {
  try {
    const nextBtn = page.getByTestId("desktop-setup-next")
    await nextBtn.waitFor({ state: "visible", timeout: 5000 })
    await nextBtn.click()

    const permStep = page.getByTestId("desktop-setup-permissions")
    await permStep.waitFor({ state: "visible", timeout: 10000 })

    const bodyText = await page.locator("body").innerText()
    const rowCount = await page.locator('[data-testid^="permission-row-"]').count()

    if (bodyText.includes("Error invoking remote method")) {
      record("FAIL", "permissions", 'renderer showed "Error invoking remote method"')
    } else if (rowCount < 4) {
      record("FAIL", "permissions", `only ${rowCount} permission categories listed (need at least 4)`)
    } else {
      record("PASS", "permissions", `${rowCount} categories listed`)
    }
  } catch (e) {
    record("FAIL", "permissions", e.message)
  }
}

/** Best-effort: click through whatever wizard step is currently showing
 *  until neither a "Next — …" button nor "I'll do this later" remain. */
async function advanceToMainScreen(page) {
  for (let i = 0; i < 4; i++) {
    const nextBtn = page.getByTestId("desktop-setup-next")
    if (await nextBtn.isVisible().catch(() => false)) {
      await nextBtn.click()
      await sleep(400)
      continue
    }
    const laterBtn = page.getByRole("button", { name: /I.ll do this later/i })
    if (await laterBtn.isVisible().catch(() => false)) {
      await laterBtn.click()
      await sleep(400)
      continue
    }
    break
  }
}

async function runChat(page) {
  let baselineIds = null
  try {
    baselineIds = await fetchConversationIds()
  } catch {
    // cleanup will just be skipped if this never resolves
  }

  try {
    // .first(): the sidebar also carries an icon-only "New conversation"
    // button (sidebar.tsx) alongside the big empty-state one — both start
    // a conversation the same way, but an unscoped locator here matches
    // both and Playwright throws on the ambiguity.
    const newConvBtn = page.getByRole("button", { name: /New Conversation/i }).first()
    const hasNewConvBtn = await newConvBtn
      .waitFor({ state: "visible", timeout: 8000 })
      .then(() => true)
      .catch(() => false)
    if (hasNewConvBtn) await newConvBtn.click()

    const input = page.getByLabel("Chat message input")
    await input.waitFor({ state: "visible", timeout: 10000 })
    await input.fill(TEST_QUESTION)
    await input.press("Enter")

    const stopBtn = page.getByRole("button", { name: "Stop generation" })
    await stopBtn.waitFor({ state: "visible", timeout: 10000 }).catch(() => {})
    await stopBtn.waitFor({ state: "hidden", timeout: 60000 }).catch(() => {})

    const bodyText = await page.locator("body").innerText()
    if (bodyText.includes("Error:")) {
      record("FAIL", "chat", 'assistant reply contained "Error:"')
    } else {
      record("PASS", "chat", "")
    }
  } catch (e) {
    record("FAIL", "chat", e.message)
  }

  let newConvId = null
  try {
    const afterIds = await fetchConversationIds()
    if (baselineIds && afterIds) {
      const added = [...afterIds].filter((id) => !baselineIds.has(id))
      if (added.length === 1) newConvId = added[0]
    }
  } catch {
    // cleanup will just be skipped
  }
  return newConvId
}

function writeState(state) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true })
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2))
}

function readState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"))
  } catch {
    return null
  }
}

async function runInitialStage() {
  const cdpUp = await waitForCdpReady(30000)
  record(cdpUp ? "PASS" : "FAIL", "launch", cdpUp ? "" : "/json/version did not answer within 30s")
  if (!cdpUp) {
    record("SKIP", "first-run", "app never came up")
    record("SKIP", "persisted", "app never came up")
    record("SKIP", "permissions", "app never came up")
    record("SKIP", "chat", "app never came up")
    writeState({ alreadySetUp: null, newConvId: null, testQuestion: TEST_QUESTION })
    return
  }

  const { chromium } = requirePlaywright()
  const page = await connectPage(chromium)

  const screen = await detectInitialScreen(page, 20000)
  const alreadySetUp = screen === "main-empty" || screen === "main-active"

  if (alreadySetUp) {
    record(
      "SKIP",
      "first-run",
      "app already set up — \"Set up this Mac\" not shown; pass --reset to exercise the first-run flow",
    )
    record("SKIP", "persisted", "skipped — app already set up (see first-run)")
    record("SKIP", "permissions", "skipped — desktop setup wizard not shown because the app is already set up")
  } else if (screen === "setup") {
    await runFirstRunAndPersisted(page)
    await runPermissions(page)
    await advanceToMainScreen(page)
  } else {
    record("FAIL", "first-run", 'neither "Set up this Mac" nor the main chat screen appeared within 20s')
    record("SKIP", "persisted", "skipped — could not determine app state")
    record("SKIP", "permissions", "skipped — could not determine app state")
  }

  const newConvId = await runChat(page)
  writeState({ alreadySetUp, newConvId, testQuestion: TEST_QUESTION })
}

async function runPostRelaunchStage() {
  const state = readState()

  const cdpUp = await waitForCdpReady(30000)
  if (!cdpUp) {
    record("FAIL", "relaunch", "CDP endpoint did not answer within 30s after relaunch")
    record("SKIP", "cleanup", "app did not come back up — nothing to verify")
    return
  }

  const { chromium } = requirePlaywright()
  const page = await connectPage(chromium)

  const screen = await detectInitialScreen(page, 15000)
  if (screen === "setup") {
    record("FAIL", "relaunch", 'the "Set up this Mac" screen was shown again after relaunch')
  } else if (screen === "unknown") {
    record("FAIL", "relaunch", "neither the setup screen nor the main chat screen appeared within 15s")
  } else {
    record("PASS", "relaunch", "")
  }

  if (state?.newConvId) {
    try {
      const ok = await deleteConversation(state.newConvId)
      record(ok ? "PASS" : "FAIL", "cleanup", ok ? "" : `DELETE /user-state/conversations/${state.newConvId} failed`)
    } catch (e) {
      record("FAIL", "cleanup", e.message)
    }
  } else {
    record("SKIP", "cleanup", "no new conversation id was captured during the chat check")
  }
}

async function main() {
  const stage = process.argv.find((a) => a.startsWith("--stage="))?.slice("--stage=".length) || "initial"
  if (stage === "initial") await runInitialStage()
  else if (stage === "post-relaunch") await runPostRelaunchStage()
  else throw new Error(`unknown --stage=${stage}`)

  const anyFail = results.some((r) => r.status === "FAIL")
  process.exit(anyFail ? 1 : 0)
}

main().catch((e) => {
  console.error(`FAIL script-error — ${e.stack || e.message}`)
  process.exit(1)
})
