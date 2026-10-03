/**
 * Sensors (docs/sensors.md): everything that feeds Lens. File sensors are the storage, email and calendar sources;
 * stream sensors are MQTT devices, syslog senders, webhooks and bridges to other brokers, whose readings are kept for
 * as long as their handling says. Pure helpers for the Sensors pages, kept apart so they can be tested.
 */
import type { Tone } from "@/components/ui/badge";

export type StreamType = "mqtt" | "syslog" | "webhook" | "bridge";
export type Status = "new" | "active" | "paused" | "ignored";
export type StoreMode = "all" | "changes" | "summary" | "none";
export type Handling = {
  store?: StoreMode | null;
  raw_days?: number | null;
  rollup_days?: number | null;
  important_days?: number | null;
  max_per_minute?: number | null;
  triage?: boolean | null;
  digest?: boolean | null;
};
export const HANDLING_KEYS = [
  "store",
  "raw_days",
  "rollup_days",
  "important_days",
  "max_per_minute",
  "triage",
  "digest",
] as const;
export type HandlingKey = (typeof HANDLING_KEYS)[number];

export const STREAM_TYPES: StreamType[] = ["mqtt", "syslog", "webhook", "bridge"];

export const STORE_OPTIONS: { value: StoreMode; label: string; hint: string }[] = [
  { value: "all", label: "Every reading", hint: "Each reading is kept for the days below" },
  {
    value: "changes",
    label: "Only changes",
    hint: "A reading is kept when it differs from the last one, and once an hour anyway",
  },
  { value: "summary", label: "Hourly summaries only", hint: "Counts, lowest, highest and average per hour" },
  { value: "none", label: "Nothing", hint: "Readings are counted, then dropped" },
];

export const STATUS: Record<Status, { label: string; tone: Tone; hint: string }> = {
  new: { label: "New", tone: "intent", hint: "Found by the hub; nobody has looked at it yet" },
  active: { label: "Recording", tone: "green", hint: "Its readings are kept as its handling says" },
  paused: { label: "Paused", tone: "gate", hint: "Nothing it sends is kept until you resume it" },
  ignored: { label: "Ignored", tone: "neutral", hint: "Nothing kept, and out of the way" },
};

export const SEVERITIES = ["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"];

export const LABELS: Record<"routine" | "notable" | "alert", { label: string; tone: Tone }> = {
  routine: { label: "Routine", tone: "neutral" },
  notable: { label: "Notable", tone: "gate" },
  alert: { label: "Alert", tone: "red" },
};

/** "30 days", "a year", "for good". */
export function daysText(d: number | null | undefined): string {
  if (d == null || d === 0) return "for good";
  if (d % 365 === 0) return d === 365 ? "a year" : `${d / 365} years`;
  if (d === 7) return "a week";
  if (d % 7 === 0 && d < 60) return `${d / 7} weeks`;
  return d === 1 ? "a day" : `${d} days`;
}

/** One line on what a sensor keeps: "Every reading for 30 days, hourly summaries for a year". */
export function handlingText(h: Handling | null | undefined): string {
  if (!h) return "";
  const store = h.store ?? "all";
  if (store === "none") return "Counted, nothing kept";
  const parts: string[] = [];
  if (store === "summary") parts.push(`Hourly summaries ${forText(h.rollup_days)}`);
  else {
    parts.push(`${store === "changes" ? "Changes" : "Every reading"} ${forText(h.raw_days)}`);
    if (h.important_days && h.raw_days && h.important_days > h.raw_days)
      parts.push(`warnings ${forText(h.important_days)}`);
    parts.push(`hourly summaries ${forText(h.rollup_days)}`);
  }
  if (h.triage) parts.push("log lines sorted by the decision model");
  if (h.digest) parts.push("a daily digest");
  return parts.join(", ");
}

function forText(d: number | null | undefined) {
  const t = daysText(d);
  return t === "for good" ? t : `for ${t}`;
}

/**
 * The change a handling form makes: only what differs from what the sensor sets itself. A value equal to the
 * settings' default with nothing set before is left out; clearing a value sends null (back to the default).
 */
export function handlingChange(own: Handling, form: Handling): Handling {
  const out: Handling = {};
  for (const k of HANDLING_KEYS) {
    const a = own[k] ?? null;
    const b = form[k] ?? null;
    if (a !== b) (out as Record<string, unknown>)[k] = b;
  }
  return out;
}

/** A whole number of days from a text box: "" is null (the default), anything else must be 0 or more. */
export function parseDays(text: string): number | null | "bad" {
  const t = text.trim();
  if (!t) return null;
  if (!/^\d+$/.test(t)) return "bad";
  const n = Number(t);
  return n <= 36500 ? n : "bad";
}

/** Where a device sends to, for the add dialog and a sensor's page. */
export function connectText(
  type: StreamType,
  host: string,
  hub: { mqtt_port?: number | null; syslog_port?: number | null },
  token?: string | null,
): string {
  switch (type) {
    case "mqtt":
      return `mqtt://${host}:${hub.mqtt_port ?? 1883}`;
    case "syslog":
      return `${host}:${hub.syslog_port ?? 5514} (UDP or TCP)`;
    case "webhook":
      return pushUrl(host, token ?? "<token>");
    default:
      return "";
  }
}

/** A webhook's address with its token in it, for devices that can only be given a URL. */
export function pushUrl(origin: string, token: string): string {
  const base = origin.includes("://") ? origin.replace(/\/+$/, "") : `https://${origin}`;
  return `${base}/api/v1/sensors/push/${token}`;
}

/** A curl line that sends one reading to a webhook. */
export function curlExample(origin: string, token: string): string {
  return `curl -X POST "${pushUrl(origin, token)}/temperature" -d 21.5`;
}

type Groupable = { family: string; status?: string | null };

/** The Sensors page's sections: the inbox (new), streams being kept, files (sources), and the ignored ones. */
export function groups<T extends Groupable>(all: T[]): { inbox: T[]; streams: T[]; files: T[]; ignored: T[] } {
  const out = { inbox: [] as T[], streams: [] as T[], files: [] as T[], ignored: [] as T[] };
  for (const s of all) {
    if (s.family === "files") out.files.push(s);
    else if (s.status === "new") out.inbox.push(s);
    else if (s.status === "ignored") out.ignored.push(s);
    else out.streams.push(s);
  }
  return out;
}

/** What a stream last said: "21.5", "on", a line of text. */
export function lastText(st: { kind?: string | null; last_value?: number | null; last_text?: string | null }): string {
  if (st.kind === "boolean" && st.last_value != null) return st.last_value ? "on" : "off";
  if (st.last_value != null) return formatNumber(st.last_value);
  return st.last_text ?? "—";
}

export function formatNumber(v: number): string {
  if (Number.isInteger(v)) return String(v);
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2).replace(/0$/, "") : v.toPrecision(3);
}

/** An SVG path through hourly points, scaled into w × h (the y axis pointing down). Gaps are left as gaps. */
export function sparkPath(points: (number | null | undefined)[], w: number, h: number): string {
  const vals = points.filter((v): v is number => typeof v === "number");
  if (!vals.length) return "";
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const step = points.length > 1 ? w / (points.length - 1) : 0;
  let d = "";
  let pen = false;
  points.forEach((v, i) => {
    if (typeof v !== "number") {
      pen = false;
      return;
    }
    const x = +(i * step).toFixed(1);
    const y = +(hi === lo ? h / 2 : h - ((v - lo) / span) * h).toFixed(1);
    d += `${pen ? "L" : "M"}${x} ${y}`;
    pen = true;
  });
  return d;
}

/** Hourly points with the missing hours put back as nulls, so a quiet night is a gap and not a straight line. */
export function fillHours<T extends { hour: string }>(points: T[], hours: number, now = new Date()): (T | null)[] {
  const by = new Map(points.map((p) => [p.hour, p]));
  const end = new Date(now);
  end.setUTCMinutes(0, 0, 0);
  const out: (T | null)[] = [];
  for (let i = hours - 1; i >= 0; i--) {
    const key = new Date(end.getTime() - i * 3_600_000).toISOString().slice(0, 13);
    out.push(by.get(key) ?? null);
  }
  return out;
}

/** The hub's state in a line: whether it listens, and on what. */
export function hubText(hub: {
  enabled: boolean;
  mqtt: boolean;
  mqtt_port?: number | null;
  syslog: boolean;
  syslog_port?: number | null;
  processes: Record<string, unknown>[];
}): { tone: "info" | "success" | "warning" | "error"; title: string; body: string } {
  if (!hub.enabled)
    return {
      tone: "info",
      title: "The hub is off.",
      body: "Turn it on in Settings → Sensors to let devices send MQTT and syslog to Lens. Webhooks and bridges work either way.",
    };
  const errors: string[] = [];
  const listening: string[] = [];
  for (const p of hub.processes) {
    for (const what of ["mqtt", "syslog"] as const) {
      const st = p[what] as { running?: boolean; port?: number; error?: string } | undefined;
      if (st?.running) listening.push(`${what === "mqtt" ? "MQTT" : "syslog"} on port ${st.port}`);
      else if (st?.error) errors.push(`${what === "mqtt" ? "MQTT" : "Syslog"}: ${st.error}`);
    }
  }
  if (!hub.processes.length)
    return {
      tone: "warning",
      title: "The hub is on, but no worker is running it.",
      body: "It runs in Lens’s workers (`lens worker`, or the worker container). Start one and it listens within seconds.",
    };
  if (errors.length)
    return { tone: "error", title: "The hub couldn’t start everything.", body: [...new Set(errors)].join(" · ") };
  return {
    tone: "success",
    title: "The hub is listening.",
    body: [...new Set(listening)].join(" · ") || "Bridges only: MQTT and syslog are off.",
  };
}
