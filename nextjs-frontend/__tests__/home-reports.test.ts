import type {
  AccessRequest,
  ApiToken,
  Job,
  RecordingSummary,
  Source,
  Speaker,
  Watch,
} from "@/app/openapi-client/types.gen";
import { buildAttention, greeting } from "@/components/home/attention";
import { latestJobs } from "@/components/library/model";
import {
  cloudSizes,
  monthBars,
  rangeBounds,
  rangeLabel,
  rangeQuery,
  talkTime,
  undatedNote,
} from "@/components/reports/model";
import { htmlName, isTemplateReport, prepareReportHtml } from "@/components/reports/report-html";

const NOW = Date.parse("2026-09-30T12:00:00Z");
const nsById = new Map([
  [1, "podcasts"],
  [2, "customer-calls"],
]);

describe("needs attention", () => {
  const jobs = latestJobs([
    {
      id: 5,
      recording: 12,
      space: 1,
      status: "failed",
      next_step: "summarize",
      error: "Provider timed out",
      title: "Episode 12",
      worker: "lab-gpu",
      finished_at: "2026-09-14T10:00:00Z",
    } as Job,
    {
      id: 4,
      recording: 13,
      space: 1,
      status: "failed",
      title: "Episode 13",
    } as Job,
    {
      id: 3,
      recording: 13,
      space: 1,
      status: "failed",
      title: "Episode 13",
    } as Job,
    {
      id: 2,
      recording: 14,
      space: 2,
      status: "succeeded",
      title: "Episode 14",
    } as Job,
  ]);

  it("lists the latest failed job of each recording, with the namespace it needs rights in", () => {
    const items = buildAttention({
      latestJobs: jobs,
      recent: [],
      nsById,
      now: NOW,
    });
    expect(items.map((i) => i.key)).toEqual(["job-5", "job-4"]);
    expect(items[0]).toMatchObject({
      kind: "fail",
      title: "Summarize failed on Episode 12",
      namespace: "podcasts",
      action: { label: "Retry", do: { type: "retry-job", job: 5 } },
    });
    expect(items[0].meta).toContain("Provider timed out");
    expect(items[0].meta).toContain("lab-gpu");
  });

  it("drops a failure once a later job for the recording exists", () => {
    const later = latestJobs([
      { id: 6, recording: 12, space: 1, status: "running" } as Job,
      { id: 5, recording: 12, space: 1, status: "failed" } as Job,
    ]);
    expect(buildAttention({ latestJobs: later, recent: [], nsById })).toEqual([]);
  });

  it("adds recordings in error that have no failed job, sources, watches, reviews and expiring tokens", () => {
    const recent = [
      {
        id: 20,
        space: 2,
        media_kind: "transcript",
        status: "error",
        error: "no transcript text found",
        title: "Call",
        namespace: "customer-calls",
      } as RecordingSummary,
      {
        id: 12,
        space: 1,
        media_kind: "audio",
        status: "error",
        title: "Episode 12",
        namespace: "podcasts",
      } as RecordingSummary,
    ];
    const sources = [
      {
        id: 1,
        name: "calls-gw",
        type: "sftp",
        label: "SFTP",
        params: {},
        secrets: {},
        health: {
          ok: false,
          error: "dial tcp: i/o timeout",
          checked_at: "2026-09-30T10:00:00Z",
        },
      } as unknown as Source,
    ];
    const watches = [
      {
        id: 1,
        source: 1,
        path: "/in",
        space: 2,
        last_error: "same source",
      } as Watch,
      {
        id: 2,
        source: 2,
        path: "/Apps/CallRecorder",
        space: 2,
        source_name: "Dropbox",
        last_error: "token expired",
      } as Watch,
    ];
    const reviews = [
      {
        namespace: "podcasts",
        speakers: [
          {
            id: 7,
            label: "Speaker 7",
            display: "Speaker 7",
            suggestions: [{ id: 2, name: "Host B", score: 0.41 }],
          },
          {
            id: 8,
            label: "Speaker 8",
            display: "Speaker 8",
            suggestions: [{ id: 1, name: "Host A", score: 0.38 }],
          },
          { id: 9, label: "Speaker 9", display: "Speaker 9", suggestions: [] },
        ] as Speaker[],
      },
    ];
    const tokens = [
      {
        id: 1,
        name: "ci",
        scope: "read",
        prefix: "la_ab",
        created_at: "2026-01-01",
        expires_at: "2026-10-03T12:00:00Z",
      },
      {
        id: 2,
        name: "old",
        scope: "read",
        prefix: "la_cd",
        created_at: "2026-01-01",
        expires_at: "2026-12-01T00:00:00Z",
      },
    ] as ApiToken[];
    const items = buildAttention({
      latestJobs: jobs,
      recent,
      nsById,
      sources,
      watches,
      reviews,
      tokens,
      now: NOW,
    });
    const keys = items.map((i) => i.key);
    expect(keys).toEqual(["job-5", "job-4", "rec-20", "src-1", "watch-2", "review-podcasts", "token-1"]);
    expect(items.find((i) => i.key === "src-1")?.title).toBe("SFTP · calls-gw can’t be reached");
    expect(items.find((i) => i.key === "review-podcasts")).toMatchObject({
      kind: "gate",
      title: "Speaker 7 ↔ Host B · 0.41 · unsure",
      meta: "2 voice matches to review in podcasts",
    });
    expect(items.find((i) => i.key === "token-1")?.title).toBe("API token “ci” expires in 3 days");
  });

  it("asks owners to answer requests for access", () => {
    const requests = [
      {
        recording: 12,
        title: "Episode 13",
        namespace: "podcasts",
        account: 7,
        email: "ana@example.org",
        name: "Ana",
        message: "For my thesis on capsids.",
        at: new Date(NOW - 3_600_000).toISOString(),
        status: "pending",
      },
      { recording: 13, account: 8, email: "bo@example.org", status: "approved" },
    ] as AccessRequest[];
    const items = buildAttention({ latestJobs: new Map(), recent: [], nsById: new Map(), requests, now: NOW });
    expect(items).toEqual([
      {
        key: "request-12-7",
        kind: "gate",
        title: "Ana asked for access to Episode 13",
        meta: "“For my thesis on capsids.” · 1 hour ago · podcasts",
        action: { label: "Review", do: { type: "link", href: "/recordings/12#access" } },
        namespace: "podcasts",
      },
    ]);
  });

  it("greets by the time of day", () => {
    expect(greeting(new Date(2026, 8, 30, 9))).toBe("Good morning");
    expect(greeting(new Date(2026, 8, 30, 14))).toBe("Good afternoon");
    expect(greeting(new Date(2026, 8, 30, 21))).toBe("Good evening");
  });
});

describe("namespace overview numbers", () => {
  const now = new Date(2026, 8, 30, 12);

  it("builds the ranges and their labels", () => {
    const six = rangeBounds("6m", now);
    expect(six.from?.getMonth()).toBe(3);
    expect(rangeLabel(six)).toBe("Apr – Sep 2026");
    expect(rangeLabel(rangeBounds("12m", now))).toBe("Oct 2025 – Sep 2026");
    expect(rangeLabel(rangeBounds("ytd", now))).toBe("Jan – Sep 2026");
    expect(rangeLabel(rangeBounds("all", now, new Date(2025, 11, 1).toISOString()))).toBe("Dec 2025 – Sep 2026");
    // the stats' first day is a local date, whatever the time zone
    expect(rangeLabel(rangeBounds("all", now, "2025-12-01"))).toBe("Dec 2025 – Sep 2026");
    expect(rangeLabel(rangeBounds("all", now, null))).toBe("Until Sep 2026");
  });

  it("asks the stats for whole months up to today", () => {
    expect(rangeQuery("6m", now)).toEqual({ from: "2026-04-01", to: "2026-09-30" });
    expect(rangeQuery("12m", now)).toEqual({ from: "2025-10-01", to: "2026-09-30" });
    expect(rangeQuery("ytd", now)).toEqual({ from: "2026-01-01", to: "2026-09-30" });
    expect(rangeQuery("all", now)).toEqual({});
  });

  it("draws the server's months, the latest 24", () => {
    const months = monthBars([
      { month: "2026-04", recordings: 0, ms: 0 },
      { month: "2026-05" },
      { month: "2026-06", recordings: 1, ms: 600_000 },
    ]);
    expect(months.map((m) => [m.key, m.label, m.year, m.recordings, m.ms])).toEqual([
      ["2026-04", "Apr", 2026, 0, 0],
      ["2026-05", "May", 2026, 0, 0],
      ["2026-06", "Jun", 2026, 1, 600_000],
    ]);
    const long = Array.from({ length: 30 }, (_, i) => ({
      month: `${2024 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`,
    }));
    expect(monthBars(long).map((m) => m.key)).toEqual(long.slice(-24).map((m) => m.month));
    expect(monthBars(undefined)).toEqual([]);
  });

  it("explains recordings without a date", () => {
    expect(undatedNote(0, "all")).toBeNull();
    expect(undatedNote(1, "all")).toBe("1 recording has no date: counted above, but in no month.");
    expect(undatedNote(3, "6m")).toBe("3 recordings have no date, so they aren’t in any date range.");
    expect(undatedNote(1, "ytd")).toBe("1 recording has no date, so it isn’t in any date range.");
  });

  it("formats talk time and sizes the entity cloud", () => {
    expect(talkTime(40_000)).toBe("40 s");
    expect(talkTime(51 * 60_000)).toBe("51 m");
    expect(talkTime(2 * 3.6e6)).toBe("2 h");
    expect(talkTime(51 * 3.6e6)).toBe("51 h");
    const c = cloudSizes([
      { name: "Corvid-2", mentions: 31 },
      { name: "honesty", mentions: 5 },
      { name: "Meridian", mentions: 23 },
    ]);
    expect(c.map((x) => x.size)).toEqual([22, 13, 19]);
    expect(c.map((x) => x.strong)).toEqual([true, false, false]);
  });
});

describe("report HTML", () => {
  it("adds a base and, for template reports, a no-scripts policy right after <head>", () => {
    const out = prepareReportHtml("<html><head><title>x</title></head><body><script>1</script></body></html>", {
      scripts: false,
      newTab: true,
      origin: "https://lens.example",
    });
    expect(out).toMatch(
      /^<html><head><base href="https:\/\/lens\.example\/" target="_blank"><meta http-equiv="Content-Security-Policy" content="script-src 'none'/,
    );
    expect(prepareReportHtml("<p>hi</p>", { scripts: true, origin: "https://a" })).toBe(
      '<base href="https://a/"><p>hi</p>',
    );
  });

  it("knows template reports and makes file names", () => {
    expect(isTemplateReport("/reports/podcasts/ep-12-1--one-page-brief.html")).toBe(true);
    expect(isTemplateReport("/reports/podcasts/ep-12-1.html?exp=1&sig=a--b")).toBe(false);
    expect(htmlName("Episode 12 — Reading a model system card")).toBe("episode-12-reading-a-model-system-card.html");
    expect(htmlName("")).toBe("report.html");
  });
});
