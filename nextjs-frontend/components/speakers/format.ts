import { speakerColor } from "@/components/ui/badge";

/** Talk time the way the registry reads it: "51 h 12 m", "2 h 05 m", "6 m 40 s", "51 m", "18 s". */
export function talkTime(ms: number | null | undefined): string {
  const s = Math.max(0, Math.round((ms ?? 0) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = s % 60;
  if (h) return `${h} h ${String(m).padStart(2, "0")} m`;
  if (m) return x ? `${m} m ${x} s` : `${m} m`;
  return `${x} s`;
}

/** Automatic labels (Speaker 7, SPEAKER_00, S1, CH0) mean nobody has named this voice yet. */
export function isUnnamed(s: { name?: string | null; label?: string | null }): boolean {
  return !s.name && /^(?:speaker_?\s?\d+|spk_?\d+|s\d+|ch\d+|unknown)$/i.test((s.label ?? "").trim());
}

/** A speaker's colour everywhere outside a single recording: stable by id. */
export function speakerTone(id: number | null | undefined): string {
  return id == null ? "var(--text-secondary)" : speakerColor(id - 1);
}

/** "A", "RM", or the number of an unnamed "Speaker 7". */
export function speakerInitials(name: string): string {
  const num = /^speaker\s+(\d+)$/i.exec(name.trim());
  if (num) return num[1];
  const w = name.split(/\s+/).filter(Boolean);
  if (w.length === 2 && /^(host|guest)$/i.test(w[0]) && w[1].length <= 2) return w[1].toUpperCase();
  return w
    .map((x) => x[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** Similarity in words: the diamond is gold because a person decides. */
export function similarityWord(score: number, match = 0.75): "likely" | "unsure" {
  return score >= match ? "likely" : "unsure";
}

/** Talk time per month from a speaker's recordings: [{month: "2026-09", ms}], oldest first, last `n` months. */
export function talkByMonth(rows: { recorded_at?: string | null; talk_ms?: number | null }[], n = 6, today = new Date()): { month: string; ms: number }[] {
  const months: string[] = [];
  const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1));
  for (let i = 0; i < n; i++) {
    months.unshift(d.toISOString().slice(0, 7));
    d.setUTCMonth(d.getUTCMonth() - 1);
  }
  const sums = new Map(months.map((m) => [m, 0]));
  for (const r of rows) {
    const m = (r.recorded_at ?? "").slice(0, 7);
    if (sums.has(m)) sums.set(m, (sums.get(m) ?? 0) + (r.talk_ms ?? 0));
  }
  return months.map((month) => ({ month, ms: sums.get(month) ?? 0 }));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Sep" from "2026-09". */
export function monthLabel(month: string): string {
  return MONTHS[(Number(month.split("-")[1]) || 1) - 1] ?? month;
}
