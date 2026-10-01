import type { Job } from "@/app/openapi-client/types.gen";
import {
  blockedBy,
  cleanTag,
  deletedToast,
  moveTargets,
  movedToast,
  tagsFromText,
  tagsOn,
  NO_FILTERS,
  activeFilterCount,
  dateFrom,
  elapsed,
  emotionMix,
  importanceInfo,
  isUnnamedSpeaker,
  languageName,
  latestJobs,
  libraryQuery,
  localDay,
  rangeIds,
  speakerChoices,
  speakerList,
  statusView,
  totalDuration,
} from "@/components/library/model";

const NOW = Date.parse("2026-09-30T12:00:00Z");

function job(p: Partial<Job> & { id: number }): Job {
  return { status: "succeeded", steps: [], ...p } as Job;
}

describe("statusView", () => {
  it("shows the backend status with its tone", () => {
    expect(statusView({ status: "analyzed" })).toMatchObject({
      label: "analyzed",
      tone: "green",
    });
    expect(statusView({ status: "transcribed" })).toMatchObject({
      label: "transcribed",
      tone: "intent",
    });
    expect(statusView({ status: null })).toMatchObject({
      label: "new",
      tone: "neutral",
    });
  });

  it("overlays a running job: step, time since it started, progress", () => {
    const v = statusView(
      { status: "new" },
      job({
        id: 1,
        status: "running",
        next_step: "transcribe",
        progress: 0.4,
        started_at: "2026-09-30T11:56:00Z",
      } as Partial<Job> & { id: number }),
      NOW,
    );
    expect(v.sub).toEqual({ text: "Transcribe · 4 min", tone: "muted" });
    expect(v.progress).toBeCloseTo(0.4);
  });

  it("keeps a sliver of progress visible at the first step", () => {
    expect(
      statusView({ status: "new" }, job({ id: 1, status: "running", next_step: "diarize", progress: 0 }), NOW).progress,
    ).toBeGreaterThan(0);
  });

  it("says a queued job waits for a worker", () => {
    expect(
      statusView(
        { status: "new" },
        job({
          id: 1,
          status: "queued",
          next_step: "transcribe",
          step_index: 0,
        }),
      ).sub?.text,
    ).toBe("Waiting for a worker");
    expect(
      statusView({ status: "transcribed" }, job({ id: 1, status: "queued", next_step: "diarize", step_index: 1 })).sub
        ?.text,
    ).toBe("Diarize · waiting for a worker");
  });

  it("offers Retry for a failed job, with the error as a tooltip", () => {
    const v = statusView(
      { status: "analyzed" },
      job({
        id: 7,
        status: "failed",
        next_step: "summarize",
        error: "Provider timed out\nTraceback…",
      }),
    );
    expect(v.sub).toEqual({
      text: "Summarize failed",
      tone: "red",
      title: "Provider timed out",
    });
    expect(v.retry).toEqual({ kind: "job", job: 7 });
  });

  it("flags voice matches waiting for review when nothing is running", () => {
    expect(statusView({ status: "analyzed" }, undefined, NOW, 1).sub).toEqual({
      text: "◆ 1 voice match to review",
      tone: "gold",
    });
    expect(statusView({ status: "analyzed" }, undefined, NOW, 2).sub?.text).toBe("◆ 2 voice matches to review");
    expect(statusView({ status: "new" }, job({ id: 1, status: "queued", step_index: 0 }), NOW, 2).sub?.text).toBe(
      "Waiting for a worker",
    );
  });

  it("offers Reprocess for a recording in error without a failed job", () => {
    const v = statusView({
      status: "error",
      error: "no transcript text found",
    });
    expect(v).toMatchObject({
      label: "error",
      tone: "red",
      retry: { kind: "reprocess" },
    });
    expect(v.sub?.text).toBe("no transcript text found");
  });
});

describe("latestJobs", () => {
  it("keeps the newest job per recording (the list comes newest first)", () => {
    const m = latestJobs([
      job({ id: 3, recording: 1, status: "running" }),
      job({ id: 2, recording: 1, status: "failed" }),
      job({ id: 1, recording: 2 }),
    ]);
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
    expect(speakerList("Dana, Speaker 7")[1]).toMatchObject({
      name: "Speaker 7",
      unnamed: true,
      index: 1,
    });
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

describe("the list query", () => {
  const byDate = { key: "date", dir: "desc" } as const;

  it("asks for everything, newest first, when nothing is set", () => {
    expect(libraryQuery(NO_FILTERS, "all", byDate, null, NOW)).toEqual({ sort: "-date" });
    expect(libraryQuery(NO_FILTERS, "all", { key: "title", dir: "asc" }, "podcasts", NOW)).toEqual({
      sort: "title",
      ns: "podcasts",
    });
  });

  it("turns each filter into its parameter", () => {
    const q = libraryQuery(
      {
        q: "  renewal ostrava ",
        statuses: ["error", "processing"],
        speaker: { name: "Dana Kovář", ids: [4, 19] },
        date: "30d",
        duration: "medium",
        media: "transcript",
        tags: ["board", "Q3"],
        origins: ["upload", "source:4"],
        languages: ["en", "none"],
      },
      "all",
      { key: "duration", dir: "desc" },
      null,
      NOW,
    );
    expect(q).toEqual({
      sort: "-duration",
      q: "renewal ostrava",
      status: ["error", "processing"],
      speaker: [4, 19],
      from: localDay(NOW - 30 * 86_400_000),
      min_duration: 600,
      max_duration: 1800,
      media: "transcript",
      tag: ["board", "Q3"],
      origin: ["upload", "source:4"],
      language: ["en", "none"],
    });
  });

  it("asks for the recordings you edited on Edited by me", () => {
    expect(libraryQuery(NO_FILTERS, "mine", byDate, "podcasts", NOW)).toEqual({
      sort: "-date",
      ns: "podcasts",
      edited_by: "me",
    });
  });

  it("names languages", () => {
    expect(languageName("en")).toBe("English");
    expect(languageName("de")).toBe("German");
    expect(languageName("pt-BR")).toBe("Brazilian Portuguese");
    expect(languageName(null)).toBe("Not known");
    expect(languageName("none")).toBe("Not known");
    expect(languageName("zz-not-a-code!")).toBe("zz-not-a-code!");
  });

  it("leaves out the open end of a duration range", () => {
    expect(libraryQuery({ ...NO_FILTERS, duration: "short" }, "all", byDate, null, NOW)).toMatchObject({
      max_duration: 600,
    });
    expect(libraryQuery({ ...NO_FILTERS, duration: "short" }, "all", byDate, null, NOW)).not.toHaveProperty(
      "min_duration",
    );
    expect(libraryQuery({ ...NO_FILTERS, duration: "xlong" }, "all", byDate, null, NOW)).toEqual({
      sort: "-date",
      min_duration: 3600,
    });
  });

  it("asks for the tabs' recordings", () => {
    expect(libraryQuery(NO_FILTERS, "attention", byDate, null, NOW)).toEqual({ sort: "-date", attention: true });
    expect(libraryQuery(NO_FILTERS, "processing", byDate, null, NOW)).toEqual({ sort: "-date", processing: true });
  });

  it("counts date ranges in local days", () => {
    expect(dateFrom("any", NOW)).toBeUndefined();
    expect(dateFrom("today", NOW)).toBe(localDay(NOW));
    expect(dateFrom("7d", NOW)).toBe(localDay(NOW - 7 * 86_400_000));
    expect(dateFrom("1y", NOW)).toBe(localDay(NOW - 365 * 86_400_000));
    expect(localDay(new Date(2026, 0, 5, 23, 59).getTime())).toBe("2026-01-05");
  });

  it("counts active filters", () => {
    expect(activeFilterCount(NO_FILTERS)).toBe(0);
    expect(activeFilterCount({ ...NO_FILTERS, q: " x ", media: "video", speaker: { name: "A", ids: [1] } })).toBe(3);
  });
});

describe("speaker choices", () => {
  it("merges speakers by name across namespaces, most recordings first", () => {
    const choices = speakerChoices([
      { id: 1, display: "Alice", recordings: 3 },
      { id: 2, display: "Bob", recordings: 5 },
      { id: 7, display: "Alice", recordings: 4 },
      { id: 8, display: "  ", recordings: 9 },
      { id: 9, display: "Carol" },
    ]);
    expect(choices).toEqual([
      { name: "Alice", ids: [1, 7], recordings: 7 },
      { name: "Bob", ids: [2], recordings: 5 },
      { name: "Carol", ids: [9], recordings: 0 },
    ]);
  });
});

describe("selection", () => {
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

describe("deleting from the Library", () => {
  it("says which selected recordings a role short blocks, and where", () => {
    const rows = [{ namespace: "pods" }, { namespace: "calls" }, { namespace: "calls" }, { namespace: null }];
    expect(blockedBy(rows, (ns) => ns === "pods")).toEqual({ count: 3, namespaces: ["calls", "?"] });
    expect(blockedBy(rows, () => true)).toEqual({ count: 0, namespaces: [] });
  });

  it("sums up what went, and why the rest didn't", () => {
    expect(deletedToast(3, [])).toEqual({
      title: "Deleted 3 recordings",
      body: "The media files stay where they are, and won’t be imported again.",
      tone: "green",
    });
    const busy = { title: "Board call", message: "A job is working on it." };
    expect(deletedToast(2, [busy])).toEqual({
      title: "Deleted 2 of 3 recordings",
      body: "Board call: A job is working on it.",
      tone: "red",
    });
    expect(deletedToast(0, [busy])).toMatchObject({ title: "Couldn’t delete the recording" });
    expect(deletedToast(0, [busy, busy])).toMatchObject({
      title: "Couldn’t delete 2 recordings",
      body: "Board call: A job is working on it. (and 1 more)",
    });
  });
});

describe("moving from the Library", () => {
  it("offers the namespaces this person edits, but not the only one the selection is in", () => {
    expect(moveTargets(["calls", "pods", "research"], ["pods", "pods"])).toEqual(["calls", "research"]);
    expect(moveTargets(["calls", "pods"], ["pods", "calls"])).toEqual(["calls", "pods"]); // from both: either
    expect(moveTargets(["pods"], ["pods"])).toEqual([]);
  });

  it("sums up what moved where, and why the rest didn't", () => {
    expect(movedToast(2, "calls", [])).toEqual({
      title: "Moved 2 recordings to calls",
      body: "Analysis runs again in calls; their access and IIIF stay as they were.",
      tone: "green",
    });
    const same = { title: "Board call", message: "calls already has the same file." };
    expect(movedToast(1, "calls", [same])).toEqual({
      title: "Moved 1 of 2 recordings to calls",
      body: "Board call: calls already has the same file.",
      tone: "red",
    });
    expect(movedToast(0, "calls", [same])).toMatchObject({ title: "Couldn’t move the recording" });
  });
});

describe("tags", () => {
  it("reads tags typed into one box", () => {
    expect(cleanTag("  Q3   review ")).toBe("Q3 review");
    expect(tagsFromText(" board, Q3  review,\nBoard ,, ")).toEqual(["board", "Q3 review"]);
    expect(tagsFromText("")).toEqual([]);
  });

  it("counts the tags on selected recordings, ignoring case", () => {
    expect(tagsOn([{ tags: ["board", "Q3"] }, { tags: ["Board"] }, { tags: null }, {}])).toEqual([
      { tag: "board", count: 2 },
      { tag: "Q3", count: 1 },
    ]);
  });

  it("counts a tags filter once", () => {
    expect(activeFilterCount({ ...NO_FILTERS, tags: ["a", "b"] })).toBe(1);
  });
});
