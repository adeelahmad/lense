import {
  actionGroup,
  detailText,
  filterEntries,
  personText,
  targetHref,
  targetText,
  toCsv,
  type AuditEntry,
  type Lookup,
} from "@/components/admin/audit-model";
import { aliveWorkers, waitingForWorker } from "@/components/admin/health";
import { describePending, tempPassword, withPending, type Person } from "@/components/admin/people-model";
import { daysError, expiresOn, tokenExpiry } from "@/components/account/token-model";
import { rolesSummary } from "@/components/app-shell/account-menu";
import { isUnreachable, retryDelay, unreachableCode } from "@/components/errors/error-states";
import {
  buildPatches,
  crossErrors,
  FIELDS,
  fieldId,
  gazetteerErrors,
  parse,
  serverError,
  show,
  toUi,
  why,
  type Change,
  type SettingsView,
} from "@/components/settings/model";
import { ApiError } from "@/lib/api/browser";
import { passwordShortBy } from "@/lib/definitions";

jest.mock("next-auth/react", () => ({
  useSession: jest.fn(),
  signOut: jest.fn(),
  signIn: jest.fn(),
  getSession: jest.fn(),
}));

const spec = (id: string) => FIELDS.find((f) => fieldId(f) === id)!;

describe("settings fields", () => {
  it("shows and parses values like the backend stores them", () => {
    expect(toUi(spec("server.session_hours"), 168)).toBe("7");
    expect(parse(spec("server.session_hours"), "7")).toEqual({ value: 168 });
    expect(parse(spec("server.session_hours"), "0.5")).toEqual({ value: 12 });
    expect(toUi(spec("server.allowed_hosts"), ["a.org", "b.org"])).toBe("a.org\nb.org");
    expect(parse(spec("server.allowed_hosts"), " a.org \n\n b.org ")).toEqual({
      value: ["a.org", "b.org"],
    });
    expect(toUi(spec("speakers.cross_namespace"), "off")).toBe(false);
    expect(parse(spec("speakers.cross_namespace"), true)).toEqual({
      value: "suggest",
    });
    expect(parse(spec("diarize.min_speakers"), "")).toEqual({ value: null });
    expect(parse(spec("diarize.min_speakers"), "1.5")).toEqual({
      error: "Use a whole number",
    });
    expect(parse(spec("speakers.match_threshold"), "1.2")).toEqual({
      error: "Use 1 or less",
    });
    expect(parse(spec("llm.base_url"), "  ")).toEqual({ value: null });
    // the models people may pick in Chat: one per line (empty: whatever the server lists)
    expect(parse(spec("llm.chat_models"), "gpt-x\n\n  claude-y  \n")).toEqual({ value: ["gpt-x", "claude-y"] });
    expect(parse(spec("llm.chat_models"), "")).toEqual({ value: [] });
    expect(parse(spec("video.frame_width"), "480")).toEqual({ value: 480 });
  });

  it("offers every OCR engine the server takes, docTR among them", () => {
    expect(spec("video.ocr_engine").options?.map((o) => o.value)).toEqual([
      "auto",
      "tesseract",
      "apple-vision",
      "rapidocr",
      "doctr",
      "none",
    ]);
  });

  it("checks rules across fields", () => {
    expect(
      crossErrors({
        "speakers.match_threshold": 0.75,
        "speakers.review_threshold": 0.8,
      }),
    ).toEqual({
      "speakers.review_threshold": "Thresholds must satisfy 0 ≤ review ≤ match ≤ 1",
    });
    expect(crossErrors({ "server.allowed_hosts": [] })["server.allowed_hosts"]).toMatch(/at least one host/);
    expect(crossErrors({ "uploads.extensions": [] })).toEqual({ "uploads.extensions": "Pick at least one type" });
    expect(crossErrors({ "uploads.extensions": [".mp3"] })).toEqual({});
    expect(crossErrors({ "server.allowed_hosts": ["https://a.org"] })["server.allowed_hosts"]).toMatch(
      /isn’t a host name/,
    );
    expect(
      crossErrors({
        "server.allowed_hosts": ["*", "lens.halden-labs.com", "127.0.0.1"],
      }),
    ).toEqual({});
    expect(
      crossErrors({
        "server.embed_frame_ancestors": ["'self'", "https://blog.x.org", "blog.x.org"],
      })["server.embed_frame_ancestors"],
    ).toMatch(/blog\.x\.org/);
    expect(
      crossErrors({
        "iiif.provider.homepage": "https://x.org",
        "iiif.provider.name": null,
      }),
    ).toEqual({ "iiif.provider.name": "The provider needs a name" });
    expect(crossErrors({ "diarize.min_speakers": 4, "diarize.max_speakers": 2 })).toHaveProperty([
      "diarize.max_speakers",
    ]);
  });

  it("finds problems in the custom vocabulary", () => {
    expect(
      gazetteerErrors(["Corvid-2 | PRODUCT", "Meridian | ORG", "Halden Freight | ORG", "Ostrava Metals ORG"]),
    ).toBe("Line 4: missing “|” between name and type");
    expect(gazetteerErrors(["Northwind | COMPANY"])).toMatch(/Line 1: “COMPANY” isn’t a type/);
    expect(gazetteerErrors(["just a topic", "Corvid-2 | product"])).toBeNull();
  });

  it("builds one PUT body per backend section, keeping nested dicts whole", () => {
    const view: SettingsView = {
      transcribe: {
        values: { whisper: { model: "large-v3", compute_type: "default" } },
      },
      iiif: { values: { provider: null } },
      reports: { values: {} },
      graph: { values: {} },
    };
    const change = (id: string, after: unknown): Change => ({
      id,
      field: spec(id),
      before: null,
      after,
    });
    expect(
      buildPatches(
        [
          change("transcribe.whisper.model", "medium"),
          change("reports.audio", "embed"),
          change("graph.max_nodes", 200),
          change("iiif.provider.name", null),
        ],
        view,
      ),
    ).toEqual({
      transcribe: { whisper: { model: "medium", compute_type: "default" } },
      reports: { audio: "embed" },
      graph: { max_nodes: 200 },
      iiif: { provider: null },
    });
  });

  it("puts the backend's refusals under the right field", () => {
    expect(serverError("speakers", "thresholds must satisfy 0 ≤ review ≤ match ≤ 1").field).toBe(
      "speakers.review_threshold",
    );
    const lock = serverError(
      "server",
      "that list leaves out 127.0.0.1, the address you're using, and would lock you out",
    );
    expect(lock.field).toBe("server.allowed_hosts");
    expect(lock.message).toMatch(/leaves out 127\.0\.0\.1.*refused/);
    expect(serverError("transcribe", "transcribe.engine must be one of: mlx-whisper, sensevoice, whisper")).toEqual({
      field: "transcribe.engine",
      message: "Transcribe.engine must be one of: mlx-whisper, sensevoice, whisper",
    });
    expect(serverError("iiif", "iiif.viewers should be list").field).toBeNull();
    expect(
      serverError("server", "server.trusted_proxies: frontend isn't an address or a range like 10.0.0.0/8"),
    ).toEqual({
      field: "server.trusted_proxies",
      message: "Server.trusted_proxies: frontend isn't an address or a range like 10.0.0.0/8",
    });
  });

  it("summarises changes for the review dialog", () => {
    expect(show(spec("server.session_hours"), 720)).toBe("30 days");
    expect(show(spec("llm.api_key"), "sk-x")).toBe("(changed)");
    expect(show(spec("llm.api_key"), "")).toBe("cleared");
    expect(show(spec("reports.audio"), "embed")).toBe("Embed");
    expect(show(spec("iiif.rights"), "http://creativecommons.org/licenses/by/4.0/")).toBe("CC BY 4.0");
    expect(
      why({
        id: "server.embed_frame_ancestors",
        field: spec("server.embed_frame_ancestors"),
        before: ["'self'"],
        after: ["'self'", "https://docs.lens.local"],
      }),
    ).toBe("Adds https://docs.lens.local.");
  });

  it("handles telemetry: off by default, prices as lines, an endpoint to turn it on", () => {
    expect(spec("telemetry.enabled").kind).toBe("switch");
    const prices = spec("telemetry.prices");
    expect(toUi(prices, { "gpt-4o-mini": { input: 0.15, output: 0.6 }, local: { input: 0, output: 0 } })).toBe(
      "gpt-4o-mini 0.15 0.6\nlocal 0 0",
    );
    expect(parse(prices, " gpt-4o-mini 0.15 0.60 \n\n my model 1 2")).toEqual({
      value: { "gpt-4o-mini": { input: 0.15, output: 0.6 }, "my model": { input: 1, output: 2 } },
    });
    expect(parse(prices, "gpt-4o 2.5")).toEqual({
      error: "Line 1: write the model, then its input and output price, like gpt-4o-mini 0.15 0.60",
    });
    expect(parse(prices, "a 1 2\nb -1 2")).toHaveProperty("error");
    expect(parse(prices, "")).toEqual({ value: {} });
    expect(show(prices, { a: { input: 1, output: 2 } })).toBe("1 model");
    expect(show(prices, {})).toBe("none");
    expect(crossErrors({ "telemetry.enabled": true, "telemetry.endpoint": null })["telemetry.endpoint"]).toMatch(
      /address to send/,
    );
    expect(crossErrors({ "telemetry.enabled": false, "telemetry.endpoint": null })).toEqual({});
    expect(crossErrors({ "telemetry.endpoint": "localhost:4318" })["telemetry.endpoint"]).toMatch(/http/);
    expect(crossErrors({ "telemetry.endpoint": "http://c:4318/v1/traces" })["telemetry.endpoint"]).toMatch(
      /without \/v1/,
    );
    const on = { id: "telemetry.enabled", field: spec("telemetry.enabled"), before: false, after: true };
    expect(why(on)).toMatch(/start sending/);
    expect(why({ ...on, before: true, after: false })).toMatch(/Nothing more is sent/);
  });
});

describe("audit log", () => {
  const look: Lookup = {
    people: { "priya@lens.local": "Priya Raman" },
    accounts: { 7: "Sam Whitaker" },
    namespaces: { 2: "customer-calls" },
  };
  const entries: AuditEntry[] = [
    {
      at: "2026-09-30T14:02:00+00:00",
      email: "priya@lens.local",
      action: "settings.save",
      target: "server",
      detail: ["session_hours", "allowed_hosts"],
    },
    {
      at: "2026-09-29T09:00:00+00:00",
      email: "priya@lens.local",
      action: "member.set",
      target: "space:2",
      detail: { account: 7, role: "viewer" },
    },
    {
      at: "2026-08-01T09:00:00+00:00",
      email: "lena@lens.local",
      action: "share.revoke",
      target: "recording:12",
      detail: null,
    },
    {
      at: "2026-09-28T09:00:00+00:00",
      email: "priya@lens.local",
      action: "settings.save",
      target: "llm",
      detail: ["api_key", "timeout"],
    },
  ];

  it("groups the backend's action names", () => {
    expect(
      ["settings.save", "member.set", "share.revoke", "watch.create", "metadata.bulk", "email", "mystery"].map(
        actionGroup,
      ),
    ).toEqual(["settings", "access", "sharing", "sources", "content", "access", "other"]);
  });

  it("writes targets and details in plain words, never secrets", () => {
    expect(targetText("server", "settings.save", look)).toBe("Settings · Access & embedding");
    expect(targetText("space:2", "member.set", look)).toBe("customer-calls");
    expect(targetText("recording:12", "share.revoke", look)).toBe("Recording 12");
    expect(targetHref("server", "settings.save", look)).toBe("/settings/access");
    expect(targetHref("recording:12", "x", look)).toBe("/resources/12");
    expect(detailText(entries[0], look)).toBe("Session length, Allowed hosts");
    expect(detailText(entries[3], look)).toBe("API key (changed), Timeout");
    expect(detailText(entries[1], look)).toBe("Sam Whitaker → viewer");
    expect(detailText({ at: "", action: "user.update", detail: {} }, look)).toBe(
      "New password set; their sessions ended",
    );
    expect(detailText({ at: "", action: "user.update", detail: { disabled: true } }, look)).toBe("disabled");
    expect(personText("priya@lens.local", look)).toBe("Priya Raman");
    expect(personText(null, look)).toBe("System");
  });

  it("filters by person, kind and date, and exports CSV", () => {
    const now = Date.parse("2026-09-30T15:00:00Z");
    expect(
      filterEntries(entries, { person: "priya@lens.local", groups: ["settings"], sinceDays: 30 }, now),
    ).toHaveLength(2);
    expect(filterEntries(entries, { person: null, groups: [], sinceDays: 30 }, now)).toHaveLength(3);
    expect(filterEntries(entries, { person: null, groups: ["sharing"], sinceDays: null }, now)).toHaveLength(1);
    const csv = toCsv(entries.slice(0, 1), look).split("\n");
    expect(csv[0]).toBe("time,person,email,action,target,details");
    expect(csv[1]).toBe(
      '2026-09-30T14:02:00+00:00,Priya Raman,priya@lens.local,settings.save,Settings · Access & embedding,"Session length, Allowed hosts"',
    );
  });
});

describe("people", () => {
  const people: Person[] = [
    { id: 1, email: "priya@lens.local", name: "Priya Raman", admin: true },
    {
      id: 2,
      email: "sam@lens.local",
      name: "Sam Whitaker",
      roles: { podcasts: "viewer" },
    },
  ];

  it("collects role changes, dropping edits back to the saved value", () => {
    let m = withPending(new Map(), people, {
      uid: 2,
      ns: "customer-calls",
      role: "viewer",
    });
    expect(m.size).toBe(1);
    expect(describePending([...m.values()][0], people)).toBe("Sam Whitaker · customer-calls — → Viewer");
    m = withPending(m, people, { uid: 2, ns: "customer-calls", role: null });
    expect(m.size).toBe(0);
    m = withPending(m, people, { uid: 2, admin: true });
    expect(describePending([...m.values()][0], people)).toBe("Sam Whitaker · platform admin off → on");
    expect(withPending(m, people, { uid: 2, admin: false }).size).toBe(0);
  });

  it("suggests readable temporary passwords", () => {
    let i = 0;
    const seq = [0, 1, 31, 2];
    expect(tempPassword(() => seq[i++])).toBe("amber-anchor-41-apple");
    expect(tempPassword().length).toBeGreaterThanOrEqual(10);
  });
});

describe("system health", () => {
  it("knows which workers are alive and what's waiting for one", () => {
    const now = Date.parse("2026-09-30T14:00:00Z");
    const workers = [
      {
        name: "mac-mini",
        steps: ["transcribe"],
        heartbeat_at: "2026-09-30T13:59:30Z",
      },
      { name: "old", steps: ["diarize"], heartbeat_at: "2026-09-30T13:30:00Z" },
    ];
    const alive = aliveWorkers(workers, now);
    expect(alive.map((w) => w.name)).toEqual(["mac-mini"]);
    expect(
      waitingForWorker(
        [{ next_step: "transcribe" }, { next_step: "diarize" }, { steps: [{ type: "analyze" }] }],
        alive,
      ),
    ).toBe(2);
  });
});

describe("account", () => {
  const now = Date.parse("2026-09-30T12:00:00Z");
  it("labels token expiry", () => {
    expect(tokenExpiry(null, now)).toEqual({ label: "never", state: "never" });
    expect(tokenExpiry("2026-10-03T12:00:00Z", now)).toEqual({
      label: "in 3 days",
      state: "soon",
    });
    expect(tokenExpiry("2026-12-29T12:00:00Z", now).state).toBe("ok");
    expect(tokenExpiry("2026-04-04T12:00:00Z", now)).toEqual({
      label: "expired 4 Apr",
      state: "expired",
    });
    expect(expiresOn(90, now)).toBe("Expires 29 Dec 2026");
    expect(expiresOn(0, now)).toBe("Never expires");
    expect(daysError("90")).toBeNull();
    expect(daysError("4000")).toMatch(/0 to 3650/);
    expect(daysError("1.5")).toMatch(/whole days/);
    // within what admins allow
    const lim = { default_days: 30, max_days: 60, never_expire: false };
    expect(daysError("60", lim)).toBeNull();
    expect(daysError("61", lim)).toBe("Use whole days from 1 to 60");
    expect(daysError("0", lim)).toBe("Keys have to expire: use 1 to 60 days");
    expect(daysError("", lim)).toBe("Enter a number of days");
    expect(daysError("0", { ...lim, never_expire: true })).toBeNull();
  });

  it("summarises roles for the account menu", () => {
    expect(rolesSummary(false, { podcasts: "editor", "customer-calls": "viewer" })).toBe(
      "Editor in podcasts · Viewer in customer-calls",
    );
    expect(rolesSummary(false, {})).toBe("No namespaces yet");
    expect(rolesSummary(false, { podcasts: "viewer" }, ["research"])).toBe(
      "Viewer in podcasts · Some collections of research",
    );
    expect(rolesSummary(false, {}, ["research"])).toBe("Some collections of research");
    expect(rolesSummary(true, {})).toMatch(/Platform admin/);
    expect(
      rolesSummary(false, {
        a: "viewer",
        b: "viewer",
        c: "viewer",
        d: "owner",
      }),
    ).toMatch(/\+1 more$/);
  });

  it("checks the password rule as you type", () => {
    expect(passwordShortBy("")).toBeNull();
    expect(passwordShortBy("lens-arch")).toBe("9 of 10 characters — add at least 1 more");
    expect(passwordShortBy("long enough pw")).toBeNull();
  });
});

describe("errors", () => {
  it("knows an unreachable server and backs off retries", () => {
    expect(isUnreachable(new ApiError(0, "x"))).toBe(true);
    expect(isUnreachable(new ApiError(502, "x"))).toBe(true);
    expect(isUnreachable(new ApiError(404, "x"))).toBe(false);
    expect(isUnreachable(new TypeError("Failed to fetch"))).toBe(true);
    expect([0, 1, 2, 3].map(retryDelay)).toEqual([8, 16, 30, 30]);
    expect(unreachableCode(new ApiError(503, "x"))).toBe("503 Service Unavailable");
    expect(unreachableCode(new TypeError("Failed to fetch"))).toBe("network error");
  });
});
