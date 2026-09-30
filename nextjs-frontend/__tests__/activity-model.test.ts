import {
  applyCount,
  applyJobEvent,
  batchPhase,
  dayTime,
  jobPhase,
  parseJobLog,
  plainError,
  reconcileOrder,
  span,
  stepStates,
  triggerOf,
  waitingReason,
  workerState,
  type JobRecord,
} from "@/components/activity/job-model";
import { cancelSummary } from "@/components/activity/cancel-dialog";

const job = (over: Partial<JobRecord>): JobRecord => ({ id: 1, status: "queued", steps: ["transcribe", "diarize", "analyze", "summarize", "report"], step_index: 0, ...over }) as JobRecord;

describe("step states", () => {
  it("fills done steps, the running one and the rest waiting", () => {
    expect(stepStates(job({ status: "running", step_index: 2 }))).toEqual(["done", "done", "running", "waiting", "waiting"]);
  });
  it("marks the failed step and what didn't run", () => {
    expect(stepStates(job({ status: "failed", step_index: 3 }))).toEqual(["done", "done", "done", "failed", "not-run"]);
  });
  it("uses the log to show skipped steps", () => {
    const log = parseJobLog(["10:00:00 no LLM configured; skipped", "10:00:00 summarize done in 0.0s"], [{ type: "summarize" }]);
    expect(stepStates(job({ status: "succeeded", steps: [{ type: "summarize" }], step_index: 1 }), log.byStep)).toEqual(["skipped"]);
  });
});

describe("the one-line phase", () => {
  it("says what is happening in words", () => {
    expect(jobPhase(job({ status: "running", step_index: 1 }))).toBe("Diarize · step 2 of 5");
    expect(jobPhase(job({ status: "queued" }), { waitingFor: "transcribe" })).toBe("Waiting for a worker that can transcribe");
    expect(jobPhase(job({ status: "failed", step_index: 3, error: "LLMError: provider timed out" }))).toBe("Summarize failed: provider timed out");
    expect(jobPhase(job({ status: "succeeded", step_index: 5, started_at: "2026-09-30T10:00:00Z", finished_at: "2026-09-30T10:01:48Z" }))).toBe("Succeeded in 1 m 48 s");
    expect(jobPhase(job({ status: "succeeded", started_at: "2026-09-30T10:00:00Z", finished_at: "2026-09-30T10:00:00Z" }))).toBe("Succeeded in under a second");
    expect(jobPhase(job({ status: "cancelled", step_index: 1 }))).toBe("Cancelled at step 2 of 5");
  });
  it("uses a step's own name when it has one", () => {
    expect(jobPhase(job({ status: "running", steps: [{ type: "llm", name: "Meeting notes" }] }))).toBe("Meeting notes · step 1 of 1");
  });
});

describe("formatting", () => {
  it("formats durations like the design", () => {
    expect(span(48_000)).toBe("48 s");
    expect(span(108_000)).toBe("1 m 48 s");
    expect(span(607_000)).toBe("10 m 07 s");
    expect(span(200)).toBe("0.2 s");
    expect(span(3 * 3600_000 + 5 * 60_000)).toBe("3 h 05 m");
  });
  it("drops the exception class from errors", () => {
    expect(plainError("RuntimeError: rclone isn't installed")).toBe("rclone isn't installed");
    expect(plainError("plain words")).toBe("plain words");
  });
  it("writes day and time with short months", () => {
    expect(dayTime("2026-09-14T16:02:05")).toBe("14 Sep, 16:02");
    expect(dayTime("2026-09-14T16:02:05", true)).toBe("14 Sep, 16:02:05");
  });
});

describe("triggers", () => {
  it("names watched folders, people and the system", () => {
    expect(triggerOf("watch:3")).toEqual({ kind: "watch", label: "watched folder 3", watch: 3 });
    expect(triggerOf("lena@lens.local", { "lena@lens.local": "Lena Vogel" })).toEqual({ kind: "person", label: "Lena Vogel" });
    expect(triggerOf("me@lens.local", {}, "me@lens.local").label).toBe("You");
    expect(triggerOf(null).kind).toBe("system");
  });
});

describe("parseJobLog", () => {
  const steps = ["transcribe", "diarize", { type: "llm", key: "notes" }];
  it("splits the log per step with durations and notes", () => {
    const r = parseJobLog(
      [
        "13:18:39 imported transcript: nothing to transcribe",
        "13:18:39 transcribe done in 0.0s",
        "13:18:39 speakers come from the transcript; kept them",
        "13:18:40 diarize done in 1.5s",
        "13:18:41 llm failed: LLMError: no language model is configured",
      ],
      steps,
    );
    expect(r.lines).toHaveLength(5);
    expect(r.byStep[0]).toMatchObject({ seconds: 0, outcome: "done", note: "imported transcript: nothing to transcribe" });
    expect(r.byStep[1]).toMatchObject({ seconds: 1.5, outcome: "done" });
    expect(r.byStep[2]).toMatchObject({ outcome: "failed", note: "LLMError: no language model is configured" });
    expect(r.byStep[2].lines).toHaveLength(1);
    expect(r.lines[4].tone).toBe("fail");
    expect(r.lines[1].tone).toBe("ok");
  });
  it("lets a retry override an earlier failure", () => {
    const r = parseJobLog(["10:00:00 llm failed: timeout", "10:05:00 saved output notes", "10:05:01 llm done in 12.0s"], [{ type: "llm" }]);
    expect(r.byStep[0]).toMatchObject({ outcome: "done", seconds: 12, note: "saved output notes" });
  });
  it("marks steps whose condition wasn't met", () => {
    const r = parseJobLog(["10:00:00 summarize skipped: its condition isn't met"], ["summarize"]);
    expect(r.byStep[0].outcome).toBe("skipped");
  });
});

describe("workers", () => {
  const now = Date.parse("2026-09-30T12:00:00Z");
  it("is idle, busy or silent by heartbeat", () => {
    expect(workerState({ heartbeat_at: "2026-09-30T11:59:40Z", current: null }, now)).toBe("idle");
    expect(workerState({ heartbeat_at: "2026-09-30T11:59:40Z", current: 4 }, now)).toBe("busy");
    expect(workerState({ heartbeat_at: "2026-09-30T11:48:00Z", current: null }, now)).toBe("silent");
    // A long job doesn't refresh the worker's own heartbeat: still busy while its job runs.
    expect(workerState({ heartbeat_at: "2026-09-30T11:30:00Z", current: 4 }, now, true)).toBe("busy");
  });
  it("explains why a job waits", () => {
    const workers = [
      { name: "mac", steps: ["transcribe"] },
      { name: "gpu", steps: ["transcribe", "diarize"] },
      { name: "server", steps: ["analyze"] },
    ];
    const r = waitingReason("transcribe", workers, { mac: "busy", gpu: "silent", server: "idle" });
    expect(r.stuck).toBe(false);
    expect(r.text).toBe("mac is busy, gpu is silent, server doesn’t run transcribe.");
    expect(waitingReason("transcribe", workers, { mac: "silent", gpu: "silent", server: "idle" }).stuck).toBe(true);
  });
});

describe("live list", () => {
  it("keeps the order while scrolled down and holds new rows back", () => {
    expect(reconcileOrder([3, 2, 1], [{ id: 5 }, { id: 4 }, { id: 3 }, { id: 2 }, { id: 1 }], false)).toEqual({ order: [3, 2, 1], pending: [5, 4] });
    expect(reconcileOrder([3, 2, 1], [{ id: 4 }, { id: 3 }, { id: 1 }], true)).toEqual({ order: [4, 3, 1], pending: [] });
    expect(reconcileOrder([], [{ id: 2 }, { id: 1 }], false)).toEqual({ order: [2, 1], pending: [] });
  });
  it("applies a change in place, adds new matching rows and drops ones that no longer match", () => {
    const rows = [
      { id: 2, status: "running", log: ["x"] },
      { id: 1, status: "queued" },
    ];
    expect(applyJobEvent(rows, { id: 2, status: "succeeded" })[0]).toEqual({ id: 2, status: "succeeded", log: ["x"] });
    expect(applyJobEvent(rows, { id: 3, status: "queued" }).map((r) => r.id)).toEqual([3, 2, 1]);
    expect(applyJobEvent(rows, { id: 2, status: "succeeded" }, ["running"]).map((r) => r.id)).toEqual([1]);
  });
  it("moves counts between statuses", () => {
    expect(applyCount({ running: 2, succeeded: 1 }, "running", "succeeded")).toEqual({ running: 1, succeeded: 2 });
    expect(applyCount({}, undefined, "queued")).toEqual({ queued: 1 });
  });
});

describe("batches and cancelling", () => {
  it("summarises a batch as one row", () => {
    const b = { id: 1, label: "Reprocess", status: "running", progress: { counts: { succeeded: 20, failed: 1, running: 2 }, done: 21, total: 30, remaining: 9 } };
    expect(batchPhase(b)).toMatchObject({ icon: "running", text: "21 of 39 · 1 failed", action: "pause" });
    expect(batchPhase({ ...b, status: "sample done" })).toMatchObject({ icon: "gate", action: "continue" });
    expect(batchPhase({ ...b, status: "finished" })).toMatchObject({ icon: "failed", action: "retry" });
  });
  it("says which outputs a cancel keeps", () => {
    expect(cancelSummary(job({ status: "running", step_index: 2 }))).toMatchObject({ kept: ["Transcribe", "Diarize", "Analyze"], dropped: ["Summarize", "Report"] });
    expect(cancelSummary(job({ status: "queued", step_index: 1 }))).toMatchObject({ kept: ["Transcribe"], dropped: ["Diarize", "Analyze", "Summarize", "Report"] });
  });
});
