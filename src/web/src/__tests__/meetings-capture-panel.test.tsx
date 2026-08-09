// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe } from "jest-axe"
import { MeetingsCapturePanel } from "@/components/sources/meetings-capture-panel"
import type { MeetingJob } from "@/lib/api/meetings"

const mockUpload = vi.fn()
const mockList = vi.fn()

vi.mock("@/lib/api/meetings", () => ({
  uploadMeeting: (...a: unknown[]) => mockUpload(...a),
  listMeetingJobs: (...a: unknown[]) => mockList(...a),
  getMeetingJob: vi.fn(),
}))

const job = (overrides: Partial<MeetingJob> = {}): MeetingJob => ({
  job_id: "j1",
  stage: "completed",
  progress: 1.0,
  started_at: 1000,
  completed_at: 1060,
  error: null,
  artifact_id: "meeting:abc123",
  duration_seconds: 3600,
  speakers_detected: 3,
  calendar_event_id: null,
  ...overrides,
})

// Top-level (NOT inside a describe): the axe-clean suite below must also start
// from the empty-jobs default. A describe-scoped beforeEach never fires for
// tests skipped by a `-t` filter, so the CI a11y job (`vitest -t "axe-clean"`)
// would otherwise mount the panel with an implementation-less list mock.
beforeEach(() => {
  mockUpload.mockReset()
  mockList.mockReset()
  mockList.mockResolvedValue([])
})

describe("MeetingsCapturePanel", () => {
  it("renders drop zone with accepted formats", async () => {
    render(<MeetingsCapturePanel />)
    expect(await screen.findByTestId("meeting-drop-zone")).toBeInTheDocument()
    expect(screen.getByText(/m4a, .mp3, .wav/i)).toBeInTheDocument()
  })

  it("rejects unsupported file types", async () => {
    render(<MeetingsCapturePanel />)
    await screen.findByTestId("meeting-drop-zone")
    const input = screen.getByTestId("meeting-file-input") as HTMLInputElement
    const file = new File(["x"], "note.txt", { type: "text/plain" })
    Object.defineProperty(input, "files", { value: [file], writable: false })
    fireEvent.change(input)
    expect(await screen.findByRole("alert")).toHaveTextContent(/Unsupported file type/i)
    expect(mockUpload).not.toHaveBeenCalled()
  })

  it("uploads on file pick and refreshes", async () => {
    mockUpload.mockResolvedValue({ job_id: "j1" })
    render(<MeetingsCapturePanel />)
    await screen.findByTestId("meeting-drop-zone")
    const input = screen.getByTestId("meeting-file-input") as HTMLInputElement
    const file = new File(["x".repeat(1000)], "demo.m4a", { type: "audio/m4a" })
    Object.defineProperty(input, "files", { value: [file], writable: false })
    fireEvent.change(input)
    await waitFor(() => {
      expect(mockUpload).toHaveBeenCalledWith(file)
    })
  })

  it("shows in-progress job with stage label + progress", async () => {
    mockList.mockResolvedValue([
      job({ stage: "transcribing", progress: 0.55, completed_at: null, artifact_id: null }),
    ])
    render(<MeetingsCapturePanel />)
    expect(await screen.findByText(/Transcribing/i)).toBeInTheDocument()
    expect(screen.getByText("55%")).toBeInTheDocument()
  })

  it("shows completed job with duration + speakers", async () => {
    mockList.mockResolvedValue([job({ duration_seconds: 90, speakers_detected: 2 })])
    render(<MeetingsCapturePanel />)
    expect(await screen.findByText(/Meeting ingested/i)).toBeInTheDocument()
    expect(screen.getByText(/1:30/)).toBeInTheDocument()
    expect(screen.getByText(/2 speakers/)).toBeInTheDocument()
  })

  it("shows failed job with error message", async () => {
    mockList.mockResolvedValue([
      job({ stage: "failed", error: "Whisper crashed", artifact_id: null }),
    ])
    render(<MeetingsCapturePanel />)
    expect(await screen.findByText(/Failed/i)).toBeInTheDocument()
    expect(screen.getByText(/Whisper crashed/)).toBeInTheDocument()
  })

  it("shows calendar-matched badge when calendar_event_id set", async () => {
    mockList.mockResolvedValue([job({ calendar_event_id: "evt:abc" })])
    render(<MeetingsCapturePanel />)
    expect(await screen.findByText(/calendar matched/i)).toBeInTheDocument()
  })

  it("Open button on completed job sets URL params", async () => {
    mockList.mockResolvedValue([job({ artifact_id: "meeting:abc" })])
    const user = userEvent.setup()
    render(<MeetingsCapturePanel />)
    await user.click(await screen.findByTestId("meeting-view-j1"))
    expect(window.location.search).toContain("entity=meeting%3Aabc")
    expect(window.location.search).toContain("mode=wiki")
  })
})

// ---------------------------------------------------------------------------
// axe-clean — one assertion per visually-distinct state (no fetch/loading/
// error/empty pane cycle; drop zone + job-list states ARE the distinct
// states this surface exposes).
// ---------------------------------------------------------------------------

describe("MeetingsCapturePanel — axe-clean", () => {
  it("is axe-clean in the default drop-zone state", async () => {
    const { container } = render(<MeetingsCapturePanel />)
    await screen.findByTestId("meeting-drop-zone")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with the unsupported-file-type error alert shown", async () => {
    const { container } = render(<MeetingsCapturePanel />)
    await screen.findByTestId("meeting-drop-zone")
    const input = screen.getByTestId("meeting-file-input") as HTMLInputElement
    const file = new File(["x"], "note.txt", { type: "text/plain" })
    Object.defineProperty(input, "files", { value: [file], writable: false })
    fireEvent.change(input)
    await screen.findByRole("alert")
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with an in-progress job", async () => {
    mockList.mockResolvedValue([
      job({ stage: "transcribing", progress: 0.55, completed_at: null, artifact_id: null }),
    ])
    const { container } = render(<MeetingsCapturePanel />)
    await screen.findByText(/Transcribing/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with a completed job", async () => {
    mockList.mockResolvedValue([job({ duration_seconds: 90, speakers_detected: 2, calendar_event_id: "evt:abc" })])
    const { container } = render(<MeetingsCapturePanel />)
    await screen.findByText(/Meeting ingested/i)
    expect(await axe(container)).toHaveNoViolations()
  })

  it("is axe-clean with a failed job", async () => {
    mockList.mockResolvedValue([
      job({ stage: "failed", error: "Whisper crashed", artifact_id: null }),
    ])
    const { container } = render(<MeetingsCapturePanel />)
    await screen.findByText(/Failed/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
