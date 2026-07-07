// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

vi.mock("@/lib/api", () => ({
  applySetupConfig: vi.fn(),
  validateProviderKey: vi.fn(),
  fetchSetupStatus: vi.fn().mockResolvedValue({
    configured: false,
    setup_required: true,
    missing_keys: ["OPENROUTER_API_KEY"],
    optional_keys: ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"],
  }),
  fetchSetupHealth: vi.fn().mockResolvedValue({ services: {} }),
  fetchProviderCredits: vi.fn().mockResolvedValue({ configured: false, balance: null }),
  fetchSystemCheck: vi.fn().mockResolvedValue({
    ram_gb: 16,
    docker_running: true,
    env_exists: true,
    env_keys_present: [],
    ollama_detected: false,
    ollama_url: null,
    ollama_models: [],
    lightweight_recommended: false,
    archive_path_exists: false,
    default_archive_path: "~/cerid-archive",
  }),
  fetchModelDoctor: vi.fn().mockResolvedValue({
    hardware_profile: "unknown",
    ok: true,
    findings: [],
    known_good_local: {},
    candidate_upgrades: {},
    catalog_size: 0,
  }),
  uploadFile: vi.fn(),
  queryKB: vi.fn(),
  pullOllamaModel: vi.fn(),
}))

vi.mock("@/hooks/use-drag-drop", () => ({
  useDragDrop: () => ({ isDragOver: false, dragHandlers: {} }),
}))

vi.mock("@/lib/api/knowledge-packs", () => ({
  fetchKnowledgePackRegistry: vi.fn().mockResolvedValue({
    schema_version: 1,
    packs_by_domain: {
      coding: [
        {
          id: "python-stdlib-docs",
          name: "Python Standard Library Documentation",
          version: "1.0.0",
          description: "Authoritative Python stdlib reference.",
          domain: "coding",
          sub_category: "python",
          tags: ["python"],
          license: "PSF-2.0",
          size_bytes: 167128,
          artifact_count: 208,
          download_url: "https://example.com/pystd.tar.gz",
          sha256: "ghi789",
          provenance: { status: "built" },
        },
      ],
    },
  }),
  installKnowledgePack: vi.fn().mockResolvedValue({
    pack_id: "python-stdlib-docs",
    version: "1.0.0",
    installed_at: "2026-05-10T12:00:00Z",
    domain: "coding",
    artifact_count: 208,
  }),
}))

vi.mock("@/lib/api/kb", () => ({
  queryKB: vi.fn().mockResolvedValue({
    results: [{ content: "Python pathlib answer." }],
    total_results: 1,
    confidence: 0.9,
  }),
}))

vi.mock("@/lib/log-swallowed", () => ({
  logSwallowedError: vi.fn(),
}))

import { SetupWizard } from "@/components/setup/setup-wizard"
import { FirstDocumentStep } from "@/components/setup/first-document-step"
import { fetchSystemCheck } from "@/lib/api"

const noop = () => {}

function renderWizard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SetupWizard open={true} onComplete={noop} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("SetupWizard", () => {
  it("renders step 0 welcome content", () => {
    renderWizard()
    expect(screen.getByText(/Welcome to Cerid/i)).toBeInTheDocument()
  })

  it("renders Get Started button on step 0", () => {
    renderWizard()
    expect(screen.getByText("Get Started")).toBeInTheDocument()
  })

  it("mentions RAG and verification in the product description", () => {
    renderWizard()
    expect(screen.getByText(/RAG-powered retrieval/)).toBeInTheDocument()
  })

  it("renders dialog title for accessibility", () => {
    renderWizard()
    expect(screen.getByText("Cerid AI Setup")).toBeInTheDocument()
  })

  // ---- Task 1.3a: local-only mode gate on the API Keys step ----

  it("enables Next on the API Keys step when a local backend is detected, without an OpenRouter key", async () => {
    vi.mocked(fetchSystemCheck).mockResolvedValueOnce({
      ram_gb: 16,
      docker_running: true,
      env_exists: true,
      env_keys_present: [],
      ollama_detected: true,
      ollama_url: "http://localhost:11434",
      ollama_models: ["llama3.2:3b"],
      lightweight_recommended: false,
      archive_path_exists: false,
      default_archive_path: "~/cerid-archive",
      os: "darwin",
      cpu: "Apple M1",
      cpu_cores: 8,
      gpu: "Apple M1 GPU",
      gpu_acceleration: "metal",
    })
    renderWizard()
    // Wait for the system check to resolve — SystemCheckCard runs on step 0
    // and auto-enables state.ollama.enabled once ollama_detected is true.
    await waitFor(() => expect(fetchSystemCheck).toHaveBeenCalled())
    await screen.findByText(/Detected \(1 model\)/i)

    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByText(/A local inference backend was detected/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^next$/i })).toBeEnabled()
  })

  it("keeps Next disabled on the API Keys step when no local backend is detected and no key is validated", async () => {
    vi.mocked(fetchSystemCheck).mockResolvedValueOnce({
      ram_gb: 16,
      docker_running: true,
      env_exists: true,
      env_keys_present: [],
      ollama_detected: false,
      ollama_url: null,
      ollama_models: [],
      lightweight_recommended: false,
      archive_path_exists: false,
      default_archive_path: "~/cerid-archive",
      os: "darwin",
      cpu: "Apple M1",
      cpu_cores: 8,
      gpu: "Apple M1 GPU",
      gpu_acceleration: "metal",
    })
    renderWizard()
    await waitFor(() => expect(fetchSystemCheck).toHaveBeenCalled())
    await screen.findByText(/Not found/i)

    fireEvent.click(screen.getByRole("button", { name: /get started/i }))
    expect(screen.getByRole("button", { name: /^next$/i })).toBeDisabled()
  })

  it("step 6 renders both tabs via FirstDocumentStep", () => {
    // Test the tab structure through a direct FirstDocumentStep render —
    // wizard navigation side-effects (resume logic, localStorage gate) are
    // covered separately in sample-pack-tab.test.tsx and first-document-step.test.tsx.
    const onChange = vi.fn()
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <FirstDocumentStep
          state={{ ingested: false, queried: false, skipped: false, documentCount: 0 }}
          onChange={onChange}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole("tab", { name: "Upload your own" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Try a sample pack" })).toBeInTheDocument()
  })

  it("clicking 'Try a sample pack' tab shows the sample pack description text", async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <FirstDocumentStep
          state={{ ingested: false, queried: false, skipped: false, documentCount: 0 }}
          onChange={onChange}
        />
      </QueryClientProvider>,
    )

    await user.click(screen.getByRole("tab", { name: "Try a sample pack" }))

    // The SamplePackTab mounts and shows its description while the catalog loads
    expect(
      screen.getByText(/Install a curated knowledge pack/i),
    ).toBeInTheDocument()
  })
})
