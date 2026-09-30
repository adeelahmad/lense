import type { Job, RecordingSummary } from "@/app/openapi-client/types.gen";
import {
  NO_FILTERS,
  elapsed,
  emotionMix,
  importanceInfo,
  isUnnamedSpeaker,
  latestJobs,
  matchesFilters,
  matchesView,
  rangeIds,
  sortRows,
  speakerList,
  statusView,
  totalDuration,
} from "@/components/library/model";

const NOW = Date.parse("2026-09-30T12:00:00Z");

function rec(p: Partial<RecordingSummary> & { id: number }): RecordingSummary {
  return { space: 1, media_kind: "audio", namespace: "podcasts", status: "analyzed", ...p } as RecordingSummary;
}

function job(p: Partial<Job> & { id: number }): Job {
  return { status: "succeeded", steps: [], ...p } as Job;
}

describe("statusView", () => {
  it("shows the backend status with its tone", () => {
    expect(statusView({ status: "analyzed" })).toMatchObject({ label: "analyzed", tone: "green" });
    expect(statusView({ status: "transcribed" })).toMatchObject({ label: "transcribed", tone: "intent" });
    expect(statusView({ status: null })).toMatchObject({ label: "new", tone: "neutral" });
  });

  it("overlays a running job: step, time since it started, progress", () => {
    const v = statusView({ status: "new" }, job({ id: 1, status: "running", next_step: "transcribe", progress: 0.4, started_at: "2026-09-30T11:56:00Z" } as Partial<Job> & { id: number }), NOW);
    expect(v.sub).toEqual({ text: "Transcribe · 4 min", tone: "muted" });
    expect(v.progress).toBeCloseTo(0.4);
  });

  it("keeps a sliver of progress visible at the first step", () => {
    expect(statusView({ status: "new" }, job({ id: 1, status: "running", next_step: "diarize", progress: 0 }), NOW).progress).toBeGreaterThan(0);
  });

  it("says a queued job waits for a worker", () => {
    expect(statusView({ status: "new" }, job({ id: 1, status: "queued", next_step: "transcribe", step_index: 0 })).sub?.text).toBe("Waiting for a worker");
    expect(statusView({ status: "transcribed" }, job({ id: 1, status: "queued", next_step: "diarize", step_index: 1 })).sub?.text).toBe("Diarize · waiting for a worker");
  });

  it("offers Retry for a failed job, with the error as a tooltip", () => {
    const v = statusView({ status: "analyzed" }, job({ id: 7, status: "failed", next_step: "summarize", error: "Provider timed out\nTraceback…" }));
    expect(v.sub).toEqual({ text: "Summarize failed", tone: "red", title: "Provider timed out" });
    expect(v.retry).toEqual({ kind: "job", job: 7 });
  });

  it("flags voice matches waiting for review when nothing is running", () => {
    expect(statusView({ status: "analyzed" }, undefined, NOW, 1).sub).toEqual({ text: "◆ 1 voice match to review", tone: "gold" });
    expect(statusView({ status: "analyzed" }, undefined, NOW, 2).sub?.text).toBe("◆ 2 voice matches to review");
    expect(statusView({ status: "new" }, job({ id: 1, status: "queued", step_index: 0 }), NOW, 2).sub?.text).toBe("Waiting for a worker");
    expect(matchesView(rec({ id: 9 }), "attention", undefined, 1)).toBe(true);
  });

  it("offers Reprocess for a recording in error without a failed job", () => {
    const v = statusView({ status: "error", error: "no transcript text found" });
    expect(v).toMatchObject({ label: "error", tone: "red", retry: { kind: "reprocess" } });
    expect(v.sub?.text).toBe("no transcript text found");
  });
});

describe("latestJobs", () => {
  it("keeps the newest job per recording (the list comes newest first)", () => {
    const m = latestJobs([job({ id: 3, recording: 1, status: "running" }), job({ id: 2, recording: 1, status: "failed" }), job({ id: 1, recording: 2 })]);
    expect(m.get(1)?.id).toBe(3);
    expect(m.get(2)?.id).toBe(1);
  });
});

describe("speakers", () => {
  it("splits, trims and de-duplicates names in order", () => {
    expect(speakerList("Host A, Host B,Host A,,?").map((s) => s.name)).toEqual(["Host A", "Host B"]);
    expect(speakerList("")).toEqual([]);
    expect(speakerList(null)).toEqual([]);
  });

  it("marks made-up labels as unnamed", () => {
    expect(isUnnamedSpeaker("Speaker 2")).toBe(true);
    expect(isUnnamedSpeaker("SPEAKER_00")).toBe(true);
    expect(isUnnamedSpeaker("Dana Kovář")).toBe(false);
    expect(speakerList("Dana, Speaker 7")[1]).toMatchObject({ name: "Speaker 7", unnamed: true, index: 1 });
  });
});

describe("importance and emotions", () => {
  it("maps 1–5 onto Low · Medium · High", () => {
    expect(importanceInfo(5)).toMatchObject({ bars: 3, label: "High" });
    expect(importanceInfo(4)?.label).toBe("High");
    expect(importanceInfo(3)).toMatchObject({ bars: 2, label: "Medium" });
    expect(importanceInfo(1)).toMatchObject({ bars: 1, label: "Low" });
    expect(importanceInfo("high")?.label).toBe("High");
    expect(importanceInfo(null)).toBeNull();
    expect(importanceInfo("n/a")).toBeNull();
  });

  it("drops Unknown and non-numbers from the emotion mix", () => {
    expect(emotionMix({ Unknown: 100, Neutral: 30, Happy: 0, Fear: "x" })).toEqual({ Neutral: 30 });
  });
});

describe("filters", () => {
  const rows = [
    rec({ id: 1, title: "Episode 13 — When evals saturate", speakers: "Host A,Host B", recorded_at: "2026-09-30T09:02:00Z", duration_ms: 52 * 60000 }),
    rec({ id: 2, title: "Renewal call — Ostrava Metals", namespace: "customer-calls", speakers: "Dana Kovář,Speaker 2", status: "transcribed", recorded_at: "2026-09-12T08:41:00Z", duration_ms: 22 * 60000 }),
    rec({ id: 3, title: "Interview 07", namespace: "research-interviews", media_kind: "transcript", status: "error", recorded_at: "2026-05-01T10:00:00Z", duration_ms: 65 * 60000 }),
  ];
  const f = (p: Partial<typeof NO_FILTERS>) => rows.filter((r) => matchesFilters(r, { ...NO_FILTERS, ...p }, undefined, NOW)).map((r) => r.id);

  it("matches every word of the query in title, namespace or speakers", () => {
    expect(f({ q: "renewal ostrava" })).toEqual([2]);
    expect(f({ q: "host a" })).toEqual([1]);
    expect(f({ q: "research" })).toEqual([3]);
  });

  it("filters by status, speaker, date, duration and media", () => {
    expect(f({ statuses: ["error", "transcribed"] })).toEqual([2, 3]);
    expect(f({ speaker: "Dana Kovář" })).toEqual([2]);
    expect(f({ date: "today" })).toEqual([1]);
    expect(f({ date: "30d" })).toEqual([1, 2]);
    expect(f({ duration: "medium" })).toEqual([2]);
    expect(f({ duration: "xlong" })).toEqual([3]);
    expect(f({ media: "transcript" })).toEqual([3]);
  });

  it("uses the job for the processing and failed statuses and views", () => {
    const running = job({ id: 9, recording: 1, status: "running" });
    expect(matchesFilters(rows[0], { ...NO_FILTERS, statuses: ["processing"] }, running, NOW)).toBe(true);
    expect(matchesFilters(rows[1], { ...NO_FILTERS, statuses: ["processing"] }, undefined, NOW)).toBe(false);
    expect(matchesView(rows[0], "processing", running)).toBe(true);
    expect(matchesView(rows[2], "attention", undefined)).toBe(true);
    expect(matchesView(rows[1], "attention", job({ id: 4, status: "failed" }))).toBe(true);
    expect(matchesView(rows[1], "attention", undefined)).toBe(false);
  });
});

describe("sorting and selection", () => {
  const rows = [rec({ id: 1, title: "b", duration_ms: 5 }), rec({ id: 2, title: "A", duration_ms: null }), rec({ id: 3, title: "c", duration_ms: 9 })];

  it("sorts either way, with missing values last", () => {
    expect(sortRows(rows, "title", "asc").map((r) => r.id)).toEqual([2, 1, 3]);
    expect(sortRows(rows, "duration", "desc").map((r) => r.id)).toEqual([3, 1, 2]);
    expect(sortRows(rows, "duration", "asc").map((r) => r.id)).toEqual([1, 3, 2]);
  });

  it("selects a range in either direction", () => {
    expect(rangeIds([10, 11, 12, 13], 3, 1)).toEqual([11, 12, 13]);
    expect(rangeIds([10, 11, 12, 13], 0, 1)).toEqual([10, 11]);
  });
});

describe("durations", () => {
  it("formats elapsed time and totals", () => {
    expect(elapsed("2026-09-30T11:59:40Z", NOW)).toBe("<1 min");
    expect(elapsed("2026-09-30T09:55:00Z", NOW)).toBe("2 h 5 min");
    expect(elapsed(null, NOW)).toBeNull();
    expect(totalDuration(0)).toBe("0 min");
    expect(totalDuration(17_000)).toBe("<1 min");
    expect(totalDuration(12 * 60000)).toBe("12 min");
    expect(totalDuration(84 * 60000)).toBe("1.4 h");
    expect(totalDuration(96 * 3.6e6)).toBe("96 h");
  });
});
