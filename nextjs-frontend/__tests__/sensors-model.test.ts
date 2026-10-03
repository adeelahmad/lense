import {
  connectText,
  daysText,
  fillHours,
  groups,
  handlingChange,
  handlingText,
  hubText,
  lastText,
  parseDays,
  pushUrl,
  sparkPath,
} from "@/components/sensors/sensor-model";

describe("sensor model", () => {
  it("says how long things are kept", () => {
    expect(daysText(null)).toBe("for good");
    expect(daysText(0)).toBe("for good");
    expect(daysText(7)).toBe("a week");
    expect(daysText(14)).toBe("2 weeks");
    expect(daysText(30)).toBe("30 days");
    expect(daysText(365)).toBe("a year");
    expect(daysText(730)).toBe("2 years");
    expect(handlingText({ store: "all", raw_days: 30, rollup_days: 365, important_days: 180 })).toBe(
      "Every reading for 30 days, warnings for 180 days, hourly summaries for a year",
    );
    expect(handlingText({ store: "changes", raw_days: 7, rollup_days: null, triage: true, digest: true })).toBe(
      "Changes for a week, hourly summaries for good, log lines sorted by the decision model, a daily digest",
    );
    expect(handlingText({ store: "summary", rollup_days: 730 })).toBe("Hourly summaries for 2 years");
    expect(handlingText({ store: "none" })).toBe("Counted, nothing kept");
  });

  it("sends only what a handling form changed, null going back to the default", () => {
    expect(handlingChange({}, { store: null, raw_days: null })).toEqual({});
    expect(handlingChange({ raw_days: 30 }, { raw_days: 30, digest: true })).toEqual({ digest: true });
    expect(handlingChange({ raw_days: 30, store: "changes" }, { raw_days: null, store: "changes" })).toEqual({
      raw_days: null,
    });
    expect(parseDays("")).toBeNull();
    expect(parseDays(" 0 ")).toBe(0);
    expect(parseDays("14")).toBe(14);
    expect(parseDays("1.5")).toBe("bad");
    expect(parseDays("-1")).toBe("bad");
    expect(parseDays("99999")).toBe("bad");
  });

  it("puts new sensors in the inbox, files apart, ignored ones out of the way", () => {
    const all = [
      { id: 1, family: "files", status: "active" },
      { id: 2, family: "stream", status: "new" },
      { id: 3, family: "stream", status: "active" },
      { id: 4, family: "stream", status: "paused" },
      { id: 5, family: "stream", status: "ignored" },
    ];
    const g = groups(all);
    expect(g.files.map((s) => s.id)).toEqual([1]);
    expect(g.inbox.map((s) => s.id)).toEqual([2]);
    expect(g.streams.map((s) => s.id)).toEqual([3, 4]);
    expect(g.ignored.map((s) => s.id)).toEqual([5]);
  });

  it("tells devices where to send", () => {
    expect(connectText("mqtt", "lens.local", { mqtt_port: 1883 })).toBe("mqtt://lens.local:1883");
    expect(connectText("syslog", "lens.local", {})).toBe("lens.local:5514 (UDP or TCP)");
    expect(pushUrl("https://lens.example/", "abc")).toBe("https://lens.example/api/v1/sensors/push/abc");
    expect(pushUrl("lens.example", "abc")).toBe("https://lens.example/api/v1/sensors/push/abc");
  });

  it("shows a stream's last value", () => {
    expect(lastText({ kind: "boolean", last_value: 1 })).toBe("on");
    expect(lastText({ kind: "number", last_value: 21.456 })).toBe("21.46");
    expect(lastText({ kind: "number", last_value: 1500.4 })).toBe("1500");
    expect(lastText({ kind: "log", last_text: "link up" })).toBe("link up");
    expect(lastText({ kind: "json" })).toBe("—");
  });

  it("draws a sparkline with gaps where nothing arrived", () => {
    expect(sparkPath([], 100, 10)).toBe("");
    expect(sparkPath([1, 2, null, 3], 30, 10)).toBe("M0 10L10 5M30 0");
    expect(sparkPath([5, 5], 10, 10)).toBe("M0 5L10 5");
    const now = new Date("2026-10-03T05:30:00Z");
    const filled = fillHours([{ hour: "2026-10-03T04", n: 1 }], 3, now);
    expect(filled).toEqual([null, { hour: "2026-10-03T04", n: 1 }, null]);
  });

  it("says whether the hub listens", () => {
    const base = { mqtt: true, syslog: true, mqtt_port: 1883, syslog_port: 5514 };
    expect(hubText({ ...base, enabled: false, processes: [] }).title).toBe("The hub is off.");
    expect(hubText({ ...base, enabled: true, processes: [] }).tone).toBe("warning");
    expect(
      hubText({
        ...base,
        enabled: true,
        processes: [{ mqtt: { running: true, port: 1883 }, syslog: { running: true, port: 5514 } }],
      }),
    ).toEqual({ tone: "success", title: "The hub is listening.", body: "MQTT on port 1883 · syslog on port 5514" });
    expect(
      hubText({ ...base, enabled: true, processes: [{ mqtt: { running: false, error: "port 1883: in use" } }] }),
    ).toEqual({ tone: "error", title: "The hub couldn’t start everything.", body: "MQTT: port 1883: in use" });
  });
});
