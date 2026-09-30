/**
 * Home's "Needs attention": only what the person can act on, each linking to where it gets fixed. Built from failed
 * jobs, recordings in error, unreachable sources and failing watched folders (admins/owners), voice matches to review
 * (editors) and API tokens about to expire. Resolved items drop out on their own because the data no longer has them.
 */
import type { ApiToken, Job, RecordingSummary, Source, Speaker, Watch } from "@/app/openapi-client/types.gen";
import { stepLabel } from "@/components/library/model";
import { sourceTypeLabel } from "@/components/library/source-labels";
import { relative, shortDate } from "@/lib/format";

export type AttentionKind = "fail" | "gate";

export type AttentionAction =
  | { type: "retry-job"; job: number }
  | { type: "reprocess"; recording: number }
  | { type: "test-source"; source: number }
  | { type: "link"; href: string; namespace?: string };

export type AttentionItem = {
  key: string;
  kind: AttentionKind;
  title: string;
  meta: string;
  action: { label: string; do: AttentionAction };
  /** Namespace the action needs editor rights in (null: none needed beyond seeing it). */
  namespace?: string | null;
};

function short(s: string | null | undefined, max = 90): string {
  const line = (s ?? "").split("\n")[0].trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

function extra(o: object, key: string): string | undefined {
  const v = (o as Record<string, unknown>)[key];
  return typeof v === "string" ? v : undefined;
}

const DAY = 86_400_000;

export function buildAttention(input: {
  latestJobs: Map<number, Job>;
  recent: RecordingSummary[];
  nsById: Map<number, string>;
  sources?: Source[];
  watches?: Watch[];
  reviews?: { namespace: string; speakers: Speaker[] }[];
  tokens?: ApiToken[];
  now?: number;
}): AttentionItem[] {
  const now = input.now ?? Date.now();
  const out: AttentionItem[] = [];

  // Failed jobs: the latest job of the recording failed (a later retry or rerun clears it).
  const failedRecordings = new Set<number>();
  for (const j of input.latestJobs.values()) {
    if (j.status !== "failed" || j.recording == null) continue;
    failedRecordings.add(j.recording);
    const when = extra(j, "finished_at") ?? extra(j, "updated_at");
    const worker = extra(j, "worker");
    out.push({
      key: `job-${j.id}`,
      kind: "fail",
      title: `${stepLabel(j.next_step)} failed on ${j.title || `recording ${j.recording}`}`,
      meta: [short(j.error) || "No error message", when ? shortDate(when) : null, worker].filter(Boolean).join(" · "),
      action: { label: "Retry", do: { type: "retry-job", job: j.id } },
      namespace: j.space != null ? (input.nsById.get(j.space) ?? null) : null,
    });
  }

  // Recordings in error without a failed job to retry (e.g. an import that couldn't be read).
  for (const r of input.recent) {
    if ((r.status || "").toLowerCase() !== "error" || failedRecordings.has(r.id)) continue;
    const j = input.latestJobs.get(r.id);
    if (j && (j.status === "queued" || j.status === "running")) continue;
    out.push({
      key: `rec-${r.id}`,
      kind: "fail",
      title: `Processing failed on ${r.title || `recording ${r.id}`}`,
      meta: [short(r.error) || "No error message", r.namespace].filter(Boolean).join(" · "),
      action: {
        label: "Reprocess",
        do: { type: "reprocess", recording: r.id },
      },
      namespace: r.namespace ?? null,
    });
  }

  for (const s of input.sources ?? []) {
    if (!s.health || s.health.ok !== false) continue;
    out.push({
      key: `src-${s.id}`,
      kind: "fail",
      title: `${sourceTypeLabel(s.type, s.label)} · ${s.name} can’t be reached`,
      meta: [
        short(s.health.error) || "The connection test failed",
        s.health.checked_at ? relative(s.health.checked_at, now) : null,
      ]
        .filter(Boolean)
        .join(" · "),
      action: {
        label: "Test again",
        do: { type: "test-source", source: s.id },
      },
    });
  }

  const badSources = new Set((input.sources ?? []).filter((s) => s.health?.ok === false).map((s) => s.id));
  for (const w of input.watches ?? []) {
    if (!w.last_error || badSources.has(w.source)) continue;
    out.push({
      key: `watch-${w.id}`,
      kind: "fail",
      title: `Watching ${w.source_name ?? "a source"} · ${w.path || "/"} failed`,
      meta: [short(w.last_error), w.last_scan_at ? relative(w.last_scan_at, now) : null].filter(Boolean).join(" · "),
      action: { label: "Open Sources", do: { type: "link", href: "/sources" } },
    });
  }

  for (const r of input.reviews ?? []) {
    const pending = r.speakers.filter((s) => (s.suggestions ?? []).length > 0);
    if (!pending.length) continue;
    let best: { from: Speaker; to: string; score: number } | null = null;
    for (const s of pending)
      for (const g of s.suggestions ?? [])
        if (!best || g.score > best.score) best = { from: s, to: g.name, score: g.score };
    if (!best) continue;
    out.push({
      key: `review-${r.namespace}`,
      kind: "gate",
      title: `${best.from.display} ↔ ${best.to} · ${best.score.toFixed(2)} · unsure`,
      meta: `${pending.length} voice ${pending.length === 1 ? "match" : "matches"} to review in ${r.namespace}`,
      action: {
        label: "Review",
        do: { type: "link", href: "/speakers", namespace: r.namespace },
      },
    });
  }

  for (const t of input.tokens ?? []) {
    if (!t.expires_at) continue;
    const left = Date.parse(t.expires_at) - now;
    if (Number.isNaN(left) || left < 0 || left > 7 * DAY) continue;
    const days = Math.ceil(left / DAY);
    out.push({
      key: `token-${t.id}`,
      kind: "gate",
      title: `API token “${t.name}” expires ${days <= 1 ? "within a day" : `in ${days} days`}`,
      meta: [
        `${t.prefix}…`,
        t.scope,
        t.last_used_at ? `last used ${relative(t.last_used_at, now)}` : "never used",
      ].join(" · "),
      action: {
        label: "Manage tokens",
        do: { type: "link", href: "/account/tokens" },
      },
    });
  }

  return out;
}

export function greeting(d: Date): string {
  const h = d.getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}
