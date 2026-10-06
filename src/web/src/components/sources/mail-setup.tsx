// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { EntitlementsUnavailableNote } from "@/components/shared/entitlements-error-notice"
import { useEntitlements } from "@/hooks/use-entitlements"
import { startConnectorAuth } from "@/lib/api/connectors"
import {
  addInboxAccount,
  applyInbox,
  discoverInbox,
  fetchInboxSetup,
  removeInboxAccount,
  skipInbox,
  undoInbox,
  updateInboxAccount,
  type InboxAccount,
  type InboxDecision,
  type InboxSetup,
} from "@/lib/api/inbox"

const CATEGORIES = ["urgent", "actionable", "personal", "newsletter", "promo", "spam"] as const

type MailProvider = "gmail" | "outlook" | "apple_mail"

function artifactRefs(decision: InboxDecision): Array<{ id: string; domain: string }> {
  return (decision.rag_artifact_ids || []).flatMap((item) => {
    if (typeof item === "string" && item) return [{ id: item, domain: "" }]
    if (item && typeof item === "object" && item.id) {
      return [{ id: String(item.id), domain: String(item.domain || "") }]
    }
    return []
  })
}

function ArtifactLinks({ decision }: { decision: InboxDecision }) {
  const refs = artifactRefs(decision)
  if (decision.utility === "none" || refs.length === 0) return null
  return (
    <span className="flex flex-wrap gap-2">
      {refs.map((ref) => (
        <a
          key={`${ref.domain}-${ref.id}`}
          className="text-label-xs underline"
          href={`/?artifact=${encodeURIComponent(ref.id)}`}
        >
          {ref.domain === "finance" ? "Finance card" : ref.domain === "inbox" ? "Correspondence" : "Knowledge"}
        </a>
      ))}
    </span>
  )
}

function Toggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-sm">
      <span>{label}</span>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} aria-label={label} />
    </div>
  )
}

export function MailSetup({ provider }: { provider: MailProvider }) {
  const flag = provider === "apple_mail" ? "apple_mail_reader" : "inbox_triage"
  const { forFlag, isLoading: entitlementsLoading, isError: entitlementsError } = useEntitlements()
  const gate = forFlag(flag, "pro")
  const locked = !entitlementsLoading && !entitlementsError && gate.state === "locked"
  const queryClient = useQueryClient()
  const setup = useQuery({
    queryKey: ["inbox-setup", provider],
    queryFn: () => fetchInboxSetup(provider),
    enabled: !entitlementsLoading && !entitlementsError && !locked,
  })
  const [address, setAddress] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState("")
  const [consentNote, setConsentNote] = useState("")
  const [found, setFound] = useState<string[]>([])

  if (entitlementsLoading || locked) return null
  if (entitlementsError) {
    return (
      <section className="space-y-4" data-testid="mail-setup">
        <h3 className="text-sm font-medium text-foreground">Inbox</h3>
        <EntitlementsUnavailableNote />
      </section>
    )
  }

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["inbox-setup", provider] })
  }

  async function run(key: string, work: () => Promise<void>) {
    setBusy(key)
    setError("")
    try {
      await work()
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed")
    } finally {
      setBusy("")
    }
  }

  const data = setup.data
  const actionsOn = Boolean(data?.actions_enabled)

  return (
    <section className="space-y-4" data-testid="mail-setup">
      <h3 className="text-sm font-medium text-foreground">Inbox</h3>
      {setup.isLoading && <p className="text-sm text-muted-foreground">Loading mail setup</p>}
      {setup.isError && (
        <p className="text-sm text-destructive" role="alert">
          {setup.error instanceof Error ? setup.error.message : "Mail setup failed"}
        </p>
      )}
      {error && (
        <p className="text-sm text-destructive" role="alert">{error}</p>
      )}
      {data && (
        <SetupBody
          provider={provider}
          inbox={data}
          actionsOn={actionsOn}
          address={address}
          displayName={displayName}
          busy={busy}
          consentNote={consentNote}
          found={found}
          onAddress={setAddress}
          onDisplayName={setDisplayName}
          onConsentNote={setConsentNote}
          onFound={setFound}
          onRun={run}
        />
      )}
    </section>
  )
}

function SetupBody({
  provider,
  inbox,
  actionsOn,
  address,
  displayName,
  busy,
  consentNote,
  found,
  onAddress,
  onDisplayName,
  onConsentNote,
  onFound,
  onRun,
}: {
  provider: MailProvider
  inbox: InboxSetup
  actionsOn: boolean
  address: string
  displayName: string
  busy: string
  consentNote: string
  found: string[]
  onAddress: (value: string) => void
  onDisplayName: (value: string) => void
  onConsentNote: (value: string) => void
  onFound: (value: string[]) => void
  onRun: (key: string, work: () => Promise<void>) => Promise<void>
}) {
  const accounts = inbox.accounts
  const consentFor = (value: string) =>
    accounts.find((account) => account.address === value)?.consent
  const runsOnDesktop = provider === "apple_mail" && inbox.source_state === "runs_on_desktop"

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {actionsOn
          ? "Mailbox writes are on for this process. Per-category auto-apply stays off until you turn a category on."
          : "Mailbox writes are off. Set CERID_INBOX_ACTIONS_ENABLED and recreate the Gmail and Outlook connector containers, then re-consent. This page displays the flag. It does not change the containers."}
      </p>
      {runsOnDesktop ? (
        <p className="text-sm text-muted-foreground" data-testid="runs-on-desktop">
          Apple Mail runs through the desktop app. Reads, filing, and address discovery happen there; this server
          does not run the Mail bridge, and that is not missing configuration.
        </p>
      ) : provider === "apple_mail" ? (
        <p className="text-sm text-muted-foreground">
          Apple Mail writes need Automation permission on this Mac. Grant it in System Settings. There is no OAuth step.
        </p>
      ) : (
        <div className="space-y-1">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy !== ""}
            onClick={() => {
              void onRun("consent", async () => {
                const flow = await startConnectorAuth(provider)
                onConsentNote(flow.instructions || "Finish consent in the provider window.")
              })
            }}
          >
            Re-consent
          </Button>
          {consentNote && <p className="text-label-xs text-muted-foreground">{consentNote}</p>}
        </div>
      )}

      <div>
        <h4 className="mb-1 text-sm font-medium">Addresses</h4>
        {accounts.length === 0 && (
          <p className="text-sm text-muted-foreground">No addresses yet.</p>
        )}
        <ul className="space-y-3">
          {accounts.map((account) => (
            <AccountCard
              key={`${account.provider}:${account.address}`}
              account={account}
              busy={busy !== ""}
              onRun={onRun}
            />
          ))}
        </ul>
        <form
          className="mt-3 space-y-2"
          onSubmit={(event) => {
            event.preventDefault()
            const next = address.trim()
            if (!next) return
            void onRun("add", async () => {
              await addInboxAccount({ provider, address: next, display_name: displayName.trim() })
              onAddress("")
              onDisplayName("")
            })
          }}
        >
          <div>
            <label htmlFor={`inbox-address-${provider}`} className="text-sm">Address</label>
            <input
              id={`inbox-address-${provider}`}
              type="email"
              value={address}
              onChange={(event) => onAddress(event.target.value)}
              className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
            />
          </div>
          <div>
            <label htmlFor={`inbox-name-${provider}`} className="text-sm">Display name</label>
            <input
              id={`inbox-name-${provider}`}
              value={displayName}
              onChange={(event) => onDisplayName(event.target.value)}
              className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
            />
          </div>
          <Button type="submit" size="sm" disabled={busy !== "" || address.trim() === ""}>
            Add address
          </Button>
        </form>
        {provider === "apple_mail" && !runsOnDesktop && (
          <div className="mt-2 space-y-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy !== ""}
              onClick={() => {
                void onRun("discover", async () => {
                  const discovered = await discoverInbox(true)
                  onFound(discovered.apple_mail)
                  if (discovered.error) throw new Error(discovered.error)
                })
              }}
            >
              Find addresses
            </Button>
            {found.map((item) => (
              <Button
                key={item}
                type="button"
                size="sm"
                variant="outline"
                onClick={() => onAddress(item)}
              >
                Use {item}
              </Button>
            ))}
          </div>
        )}
      </div>

      <div>
        <h4 className="mb-1 text-sm font-medium">Review queue</h4>
        {inbox.proposals.length === 0 && (
          <p className="text-sm text-muted-foreground">No proposed decisions.</p>
        )}
        <ul className="space-y-3">
          {inbox.proposals.map((decision) => (
            <li key={decision.id} className="space-y-1 rounded-md border border-border p-2">
              <p className="text-sm">
                {`${decision.action} · ${decision.category}${decision.band ? ` · ${decision.band}` : ""}${decision.model ? ` · ${decision.model}` : ""}`}
              </p>
              {decision.classification_reason ? (
                <p className="text-sm text-muted-foreground">{decision.classification_reason}</p>
              ) : null}
              {decision.draft_body ? (
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">{decision.draft_body}</p>
              ) : null}
              {consentFor(decision.account_address) === "pending" && (
                <p className="text-label-xs text-amber-600">Pending consent. Applies for this address are skipped.</p>
              )}
              <ArtifactLinks decision={decision} />
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={!actionsOn || busy !== ""}
                  onClick={() => {
                    void onRun(`apply-${decision.id}`, async () => {
                      const result = await applyInbox([decision.id], false)
                      const row = result.results?.[0]
                      if (row && row.ok === false) throw new Error(row.reason || "Apply failed")
                    })
                  }}
                >
                  Approve
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy !== ""}
                  onClick={() => {
                    void onRun(`skip-${decision.id}`, async () => {
                      await skipInbox(decision.id)
                    })
                  }}
                >
                  Skip
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {inbox.recent.length > 0 && (
        <div>
          <h4 className="mb-1 text-sm font-medium">Applied</h4>
          <ul className="space-y-2">
            {inbox.recent.map((decision) => (
              <li key={decision.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  {decision.action} · {decision.category}
                  <ArtifactLinks decision={decision} />
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!actionsOn || busy !== ""}
                  onClick={() => {
                    void onRun(`undo-${decision.id}`, async () => {
                      const result = await undoInbox(decision.id, false)
                      if (result.ok === false) throw new Error(result.reason || "Undo failed")
                    })
                  }}
                >
                  Undo
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="space-y-1 text-sm text-muted-foreground">
        <p>Background model: {inbox.background_model || "provider default"}</p>
        <p>Chat model: {inbox.chat_model || "provider default"}</p>
      </div>
      <div>
        <h4 className="mb-1 text-sm font-medium">Sender pins</h4>
        {inbox.pins.length === 0 ? (
          <p className="text-sm text-muted-foreground">No sender pins yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {inbox.pins.map((pin) => (
              <li key={`${pin.source}:${pin.sender}`}>{pin.sender} · {pin.category} · {pin.action}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function AccountCard({
  account,
  busy,
  onRun,
}: {
  account: InboxAccount
  busy: boolean
  onRun: (key: string, work: () => Promise<void>) => Promise<void>
}) {
  if (account.removed) {
    return (
      <li className="text-sm text-muted-foreground">
        {account.address} is removed. Add it again to include it. Undo of past decisions still works.
      </li>
    )
  }
  const patch = (fields: Parameters<typeof updateInboxAccount>[0]) =>
    onRun(`${account.address}:${Object.keys(fields).join(",")}`, () => updateInboxAccount(fields).then(() => undefined))

  return (
    <li className="space-y-1 rounded-md border border-border p-2">
      <p className="text-sm font-medium">
        {account.display_name || account.address}
        {account.display_name ? <span className="font-normal text-muted-foreground"> · {account.address}</span> : null}
        {account.consent === "pending" && (
          <span className="ml-2 text-label-xs text-amber-600">Pending consent</span>
        )}
      </p>
      {account.last_rejection && (
        <p className="text-label-xs text-destructive">{account.last_rejection}</p>
      )}
      <Toggle
        label={`Include ${account.address}`}
        checked={account.included}
        disabled={busy}
        onChange={(included) => { void patch({ provider: account.provider, address: account.address, included }) }}
      />
      <Toggle
        label={`Folder sorting for ${account.address}`}
        checked={account.folder_sort}
        disabled={busy}
        onChange={(folder_sort) => {
          void patch({ provider: account.provider, address: account.address, folder_sort })
        }}
      />
      {CATEGORIES.map((category) => (
        <Toggle
          key={category}
          label={`Auto-apply ${category} for ${account.address}`}
          checked={account.auto_apply.includes(category)}
          disabled={busy}
          onChange={(on) => {
            const auto_apply = on
              ? [...account.auto_apply, category]
              : account.auto_apply.filter((item) => item !== category)
            void patch({ provider: account.provider, address: account.address, auto_apply })
          }}
        />
      ))}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={() => {
          void onRun(`remove-${account.address}`, async () => {
            await removeInboxAccount(account.provider, account.address)
          })
        }}
      >
        Remove {account.address}
      </Button>
    </li>
  )
}
