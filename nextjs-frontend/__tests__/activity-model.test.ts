import {
  appendLog,
  applyCount,
  approx,
  applyJobEvent,
  batchPhase,
  dayTime,
  jobPhase,
  outputText,
  parseJobLog,
  pauseNote,
  pipelineKey,
  pipelineLabel,
  pipelineOptions,
  plainError,
  reconcileOrder,
  runSteps,
  span,
  stepSpecs,
  stepStates,
  timeLeft,
  triggerOf,
  type JobRecord,
  usually,
  waitingReason,
  workerLoad,
  workerState,
} from "@/components/activity/job-model";
import { cancelSummary } from "@/components/activity/cancel-dialog";

const job = (over: Partial<JobRecord>): JobRecord =>
  ({
    id: 1,
    status: "queued",
    steps: ["transcribe", "diarize", "analyze", "summarize", "report"],
    step_index: 0,
    ...over,
  }) as JobRecord;

describe("step states", () => {
  it("fills done steps, the running one and the rest waiting", () => {
    expect(stepStates(job({ status: "running", step_index: 2 }))).toEqual([
      "done",
      "done",
      "running",
      "waiting",
      "waiting",
    ]);
  });
  it("marks the failed step and what didn't run", () => {
    expect(stepStates(job({ status: "failed", step_index: 3 }))).toEqual(["done", "done", "done", "failed", "not-run"]);
  });
  it("uses the log to show skipped steps", () => {
    const log = parseJobLog(
      ["10:00:00 no LLM configured; skipped", "10:00:00 summarize done in 0.0s"],
      [{ type: "summarize" }],
    );
    expect(
      stepStates(
        job({
          status: "succeeded",
          steps: [{ type: "summarize" }],
          step_index: 1,
        }),
        log.byStep,
      ),
    ).toEqual(["skipped"]);
  });
});

describe("the one-line phase", () => {
  it("says what is happening in words", () => {
    expect(jobPhase(job({ status: "running", step_index: 1 }))).toBe("Diarize · step 2 of 5");
    expect(jobPhase(job({ status: "queued" }), { waitingFor: "transcribe" })).toBe(
      "Waiting for a worker that can transcribe",
    );
    expect(
      jobPhase(
        job({
          status: "failed",
          step_index: 3,
          error: "LLMError: provider timed out",
        }),
      ),
    ).toBe("Summarize failed: provider timed out");
    expect(
      jobPhase(
        job({
          status: "succeeded",
          step_index: 5,
          started_at: "2026-09-30T10:00:00Z",
          finished_at: "2026-09-30T10:01:48Z",
        }),
      ),
    ).toBe("Succeeded in 1 m 48 s");
    expect(
      jobPhase(
        job({
          status: "succeeded",
          started_at: "2026-09-30T10:00:00Z",
          finished_at: "2026-09-30T10:00:00Z",
        }),
      ),
    ).toBe("Succeeded in under a second");
    expect(jobPhase(job({ status: "cancelled", step_index: 1 }))).toBe("Cancelled at step 2 of 5");
  });
  it("uses a step's own name when it has one", () => {
    expect(
      jobPhase(
        job({
          status: "running",
          steps: [{ type: "llm", name: "Meeting notes" }],
        }),
      ),
    ).toBe("Meeting notes · step 1 of 1");
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
    expect(triggerOf("watch:3")).toEqual({
      kind: "watch",
      label: "watched folder 3",
      watch: 3,
    });
    expect(triggerOf("lena@lens.local", { "lena@lens.local": "Lena Vogel" })).toEqual({
      kind: "person",
      label: "Lena Vogel",
    });
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
    expect(r.byStep[0]).toMatchObject({
      seconds: 0,
      outcome: "done",
      note: "imported transcript: nothing to transcribe",
    });
    expect(r.byStep[1]).toMatchObject({ seconds: 1.5, outcome: "done" });
    expect(r.byStep[2]).toMatchObject({
      outcome: "failed",
      note: "LLMError: no language model is configured",
    });
    expect(r.byStep[2].lines).toHaveLength(1);
    expect(r.lines[4].tone).toBe("fail");
    expect(r.lines[1].tone).toBe("ok");
  });
  it("lets a retry override an earlier failure", () => {
    const r = parseJobLog(
      ["10:00:00 llm failed: timeout", "10:05:00 saved output notes", "10:05:01 llm done in 12.0s"],
      [{ type: "llm" }],
    );
    expect(r.byStep[0]).toMatchObject({
      outcome: "done",
      seconds: 12,
      note: "saved output notes",
    });
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
    // paused: once it has finished its run; silence still wins
    expect(workerState({ heartbeat_at: "2026-09-30T11:59:40Z", current: null, paused: true }, now)).toBe("paused");
    expect(workerState({ heartbeat_at: "2026-09-30T11:59:40Z", current: 4, paused: true }, now)).toBe("busy");
    expect(workerState({ heartbeat_at: "2026-09-30T11:48:00Z", current: null, paused: true }, now)).toBe("silent");
  });
  it("says what pausing and draining mean right now, and the load", () => {
    expect(pauseNote({ paused: false, current: 4 })).toBeNull();
    expect(pauseNote({ paused: true, current: null, paused_by: "ann@x.io" })).toBe(
      "Paused by ann@x.io: takes no new runs until resumed.",
    );
    expect(pauseNote({ paused: true, current: 4 })).toBe("Paused: finishes this run, then takes no new ones.");
    expect(pauseNote({ paused: true, draining: true, current: 4 })).toBe(
      "Draining: hands its run back to the queue after the step it’s on.",
    );
    expect(workerLoad({ load: 0.42, cpus: 8, steps_last_hour: 12 })).toBe(
      "CPU 42% of 8 cores · 12 steps in the last hour",
    );
    expect(workerLoad({ load: null, steps_last_hour: 1 })).toBe("1 step in the last hour");
  });
  it("explains why a job waits", () => {
    const workers = [
      { name: "mac", steps: ["transcribe"] },
      { name: "gpu", steps: ["transcribe", "diarize"] },
      { name: "server", steps: ["analyze"] },
    ];
    const r = waitingReason("transcribe", workers, {
      mac: "busy",
      gpu: "silent",
      server: "idle",
    });
    expect(r.stuck).toBe(false);
    expect(r.text).toBe("mac is busy, gpu is silent, server doesn’t run transcribe.");
    const paused = waitingReason("transcribe", [{ ...workers[0], paused: true }, workers[2]], {
      mac: "paused",
      server: "idle",
    });
    expect(paused).toEqual({ stuck: true, text: "mac is paused, server doesn’t run transcribe." });
    expect(
      waitingReason("transcribe", workers, {
        mac: "silent",
        gpu: "silent",
        server: "idle",
      }).stuck,
    ).toBe(true);
  });
});

describe("live list", () => {
  it("keeps the order while scrolled down and holds new rows back", () => {
    expect(reconcileOrder([3, 2, 1], [{ id: 5 }, { id: 4 }, { id: 3 }, { id: 2 }, { id: 1 }], false)).toEqual({
      order: [3, 2, 1],
      pending: [5, 4],
    });
    expect(reconcileOrder([3, 2, 1], [{ id: 4 }, { id: 3 }, { id: 1 }], true)).toEqual({
      order: [4, 3, 1],
      pending: [],
    });
    expect(reconcileOrder([], [{ id: 2 }, { id: 1 }], false)).toEqual({
      order: [2, 1],
      pending: [],
    });
  });
  it("applies a change in place, adds new matching rows and drops ones that no longer match", () => {
    const rows = [
      { id: 2, status: "running", log: ["x"] },
      { id: 1, status: "queued" },
    ];
    expect(applyJobEvent(rows, { id: 2, status: "succeeded" })[0]).toEqual({
      id: 2,
      status: "succeeded",
      log: ["x"],
    });
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
    const b = {
      id: 1,
      label: "Reprocess",
      status: "running",
      progress: {
        counts: { succeeded: 20, failed: 1, running: 2 },
        done: 21,
        total: 30,
        remaining: 9,
      },
    };
    expect(batchPhase(b)).toMatchObject({
      icon: "running",
      text: "21 of 39 · 1 failed",
      action: "pause",
    });
    expect(batchPhase({ ...b, status: "sample done" })).toMatchObject({
      icon: "gate",
      action: "continue",
    });
    expect(batchPhase({ ...b, status: "finished" })).toMatchObject({
      icon: "failed",
      action: "retry",
    });
  });
  it("says which outputs a cancel keeps", () => {
    expect(cancelSummary(job({ status: "running", step_index: 2 }))).toMatchObject({
      kept: ["Transcribe", "Diarize", "Analyze"],
      dropped: ["Summarize", "Report"],
    });
    expect(cancelSummary(job({ status: "queued", step_index: 1 }))).toMatchObject({
      kept: ["Transcribe"],
      dropped: ["Diarize", "Analyze", "Summarize", "Report"],
    });
  });
});

describe("a run's whole log, as it streams", () => {
  it("adds what's new and says when lines were missed", () => {
    const have = ["a", "b", "c"];
    expect(appendLog(have, 3, ["d", "e"])).toEqual(["a", "b", "c", "d", "e"]);
    expect(appendLog(have, 1, ["b", "c", "d"])).toEqual(["a", "b", "c", "d"]); // overlaps what's here
    expect(appendLog(have, 0, ["a", "b"])).toBe(have); // nothing new
    expect(appendLog(have, 5, ["f"])).toBeNull(); // a gap: read from line 3
  });
});

describe("how each step went", () => {
  const log = [
    "10:00:00 thinking",
    "10:00:01 analysed",
    "10:00:01 analyze done in 1.2s",
    "10:00:01 summarize skipped: no LLM is configured",
    "10:00:02 writing",
  ];
  const records = {
    steps: ["analyze", "summarize", "report", "export"],
    step_runs: [
      {
        outcome: "done" as const,
        note: "analysed",
        seconds: 1.2,
        log_from: 0,
        log_to: 3,
        started_at: "2026-09-30T10:00:00+00:00",
        finished_at: "2026-09-30T10:00:01+00:00",
        worker: "w1",
        outputs: [{ key: "notes", template: 7, version: 2, model: "m" }],
      },
      { outcome: "skipped" as const, note: "no LLM is configured", seconds: 0, log_from: 3, log_to: 4 },
      { outcome: "running" as const, started_at: "2026-09-30T10:00:01+00:00", log_from: 4 },
    ],
  };
  it("takes times, outcomes, notes, outputs and each step's lines from the run's records", () => {
    const { lines, byStep } = runSteps(records, log);
    expect(lines).toHaveLength(5);
    expect(byStep[0]).toMatchObject({ outcome: "done", note: "analysed", seconds: 1.2, worker: "w1" });
    expect(byStep[0].lines.map((l) => l.text)).toEqual(["thinking", "analysed", "analyze done in 1.2s"]);
    expect(byStep[0].outputs).toEqual([{ key: "notes", template: 7, version: 2, model: "m" }]);
    expect(byStep[1]).toMatchObject({ outcome: "skipped", note: "no LLM is configured" });
    expect(byStep[2].outcome).toBeUndefined(); // still running: its lines so far
    expect(byStep[2].lines.map((l) => l.text)).toEqual(["writing"]);
    expect(byStep[3]).toEqual({ lines: [] }); // hasn't run
    expect(stepStates({ status: "running", steps: records.steps, step_index: 2 }, byStep)).toEqual([
      "done",
      "skipped",
      "running",
      "waiting",
    ]);
  });
  it("numbers lines from where the log it has starts", () => {
    // only the last two lines have loaded: they are lines 3 and 4 of the run
    const { byStep } = runSteps(records, log.slice(3), 3);
    expect(byStep[0].lines).toEqual([]);
    expect(byStep[1].lines.map((l) => l.text)).toEqual(["summarize skipped: no LLM is configured"]);
    expect(byStep[2].lines.map((l) => l.text)).toEqual(["writing"]);
  });
  it("reads older runs from their log", () => {
    const { byStep } = runSteps({ steps: records.steps }, log);
    expect(byStep[0]).toMatchObject({ outcome: "done", seconds: 1.2, note: "analysed" });
    expect(byStep[1]).toMatchObject({ outcome: "skipped", note: "no LLM is configured" });
  });
  it("says what made an output", () => {
    const name = (id: number) => (id === 7 ? "Meeting notes" : undefined);
    expect(outputText({ key: "notes", template: 7, version: 2, model: "m" }, name)).toBe(
      "outputs.notes · Meeting notes v2 · model m",
    );
    expect(outputText({ key: "export_x", template: 9 })).toBe("outputs.export_x · template #9");
    expect(outputText({ key: "k" })).toBe("outputs.k");
  });
});

describe("pipelines and time left", () => {
  it("names a run's pipeline and version", () => {
    expect(pipelineLabel({ id: 3, version: 2, name: "Notes" })).toBe("Notes v2");
    expect(pipelineLabel({ name: "Standard" })).toBe("Standard steps");
    expect(pipelineLabel(null)).toBeNull();
    expect([
      pipelineKey({ id: 3, version: 2, name: "Notes" }),
      pipelineKey({ name: "Standard" }),
      pipelineKey(null),
    ]).toEqual(["p3:2", "standard", "chosen"]);
  });
  it("offers each pipeline version among the runs, then the standard and chosen steps", () => {
    const runs = [
      { pipeline: { id: 3, version: 1, name: "Notes" } },
      { pipeline: null },
      { pipeline: { id: 3, version: 2, name: "Notes" } },
      { pipeline: { name: "Standard" } },
      { pipeline: { id: 1, version: 4, name: "Calls" } },
      { pipeline: { id: 3, version: 2, name: "Notes" } },
    ];
    expect(pipelineOptions(runs)).toEqual([
      { value: "p1:4", label: "Calls v4" },
      { value: "p3:2", label: "Notes v2" },
      { value: "p3:1", label: "Notes v1" },
      { value: "standard", label: "Standard steps" },
      { value: "chosen", label: "Steps chosen directly" },
    ]);
  });
  it("says about how long an active run has left", () => {
    expect(timeLeft({ status: "running", eta_seconds: 240 })).toBe("about 4 min left");
    expect(timeLeft({ status: "queued", eta_seconds: 45 })).toBe("about 45 s left");
    expect(timeLeft({ status: "running", eta_seconds: 3 })).toBe("should finish shortly");
    expect(timeLeft({ status: "running", eta_seconds: null })).toBeNull();
    expect(timeLeft({ status: "succeeded", eta_seconds: 10 })).toBeNull();
    expect([usually(90), usually(0.4), usually(null)]).toEqual(["usually ~2 min", null, null]);
    expect([approx(0.4), approx(42), approx(600)]).toEqual(["<1 s", "~42 s", "~10 min"]);
  });
});

describe("step labels", () => {
  it("names an unnamed LLM step after the output it saves", () => {
    const specs = stepSpecs([
      { type: "llm", key: "meeting_notes", template: 1 },
      { type: "llm", key: "x", name: "Brief" },
      "transcribe",
    ]);
    expect(specs.map((s) => s.label)).toEqual(["Meeting notes", "Brief", "Transcribe"]);
  });
});
