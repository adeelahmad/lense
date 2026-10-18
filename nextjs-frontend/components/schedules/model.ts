/**
 * The Schedules page (docs/schedules.md): everything that runs on a clock in one list, like a crontab. Rows come from
 * GET /api/v1/schedules; this file has what the page works out from them.
 */
import type { Schedule } from "@/app/openapi-client/types.gen";
import type { Tone } from "@/components/ui/badge";
import { PRESETS, presetOf, scheduleFor, type Preset } from "@/components/routines/routine-model";

export type Kind = Schedule["kind"];
export type Filter = "all" | Kind;

export const KIND_LABEL: Record<Kind, string> = { routine: "Routine", automation: "Automation", backups: "Backup" };

export const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "routine", label: "Routines" },
  { value: "automation", label: "Automations" },
  { value: "backups", label: "Backups" },
];

/** The rows a filter shows, in the server's order (soonest first). */
export function filtered(rows: Schedule[], f: Filter): Schedule[] {
  return f === "all" ? rows : rows.filter((r) => r.kind === f);
}

/** How many rows each filter shows; a filter with none isn't offered (All always is). */
export function counts(rows: Schedule[]): Record<Filter, number> {
  const c: Record<Filter, number> = { all: rows.length, routine: 0, automation: 0, backups: 0 };
  for (const r of rows) c[r.kind] += 1;
  return c;
}

/** What a row is, under its name: "Routine", "Automation · morning", with its namespaces. */
export function whatText(r: Schedule): string {
  const kind = r.trigger ? `${KIND_LABEL[r.kind]} · ${r.trigger}` : KIND_LABEL[r.kind];
  const ns = r.namespace_names;
  if (r.kind === "backups") return kind;
  return `${kind} · ${ns == null ? "every namespace" : ns.filter(Boolean).join(", ") || "no namespace"}`;
}

const LAST: Record<string, { label: string; tone: Tone }> = {
  done: { label: "Done", tone: "green" },
  partly: { label: "Partly done", tone: "gate" },
  error: { label: "Failed", tone: "red" },
  failed: { label: "Failed", tone: "red" },
  running: { label: "Running", tone: "intent" },
  waiting: { label: "Waiting", tone: "gate" },
  held: { label: "Waiting for you", tone: "gate" },
  released: { label: "Let through", tone: "neutral" },
  skipped: { label: "Skipped", tone: "neutral" },
  limited: { label: "Over a limit", tone: "gate" },
};

/** How a last run went, as a badge. */
export function lastOf(status: string | null | undefined): { label: string; tone: Tone } {
  return LAST[status ?? ""] ?? { label: status ?? "—", tone: "neutral" };
}

/** Why a row has no next run, when it isn't simply switched off. */
export function noNext(r: Schedule): string | null {
  if (r.running) return "Running now";
  if (!r.enabled) return null;
  if (r.state === "done") return "Its time has passed";
  if (r.state === "missed") return "Missed while Lens was off";
  if (r.state === "failed") return "Couldn’t be set";
  if (r.on === "manual") return r.kind === "backups" ? "No schedule" : "Only by hand";
  return null;
}

/** The schedule choices a row offers: a routine may run only by hand and the backup may have no schedule; an
 * automation's schedule trigger always has one. */
export function presetsFor(r: Schedule): { value: Preset; label: string }[] {
  return PRESETS.filter((p) => p.value !== "manual" || r.kind !== "automation").map((p) =>
    p.value === "manual" && r.kind === "backups" ? { value: p.value, label: "No scheduled backups" } : p,
  );
}

export type Draft = { preset: Preset; custom: string; at: string; timezone: string };

/** The editor's starting state for a row: its preset, its cron when custom, its time (an at trigger) and zone. */
export function draftOf(r: Schedule): Draft {
  return {
    preset: r.on === "at" ? "custom" : presetOf(r.cron),
    custom: r.cron ?? "",
    at: (r.at ?? "").slice(0, 16),
    timezone: r.timezone || "UTC",
  };
}

/** What PATCH /schedules/{id} gets from the editor: only what changed. Null when nothing did. */
export function changesOf(r: Schedule, d: Draft): { cron?: string | null; at?: string; timezone?: string } | null {
  const out: { cron?: string | null; at?: string; timezone?: string } = {};
  const tz = d.timezone.trim() || "UTC";
  if (r.on === "at") {
    if (d.at && d.at !== (r.at ?? "").slice(0, 16)) out.at = d.at;
  } else {
    const cron = scheduleFor(d.preset, d.custom);
    if (cron !== (r.cron ?? null)) out.cron = cron;
  }
  if (tz !== (r.timezone || "UTC")) out.timezone = tz;
  return Object.keys(out).length ? out : null;
}
