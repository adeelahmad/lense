/**
 * NSFW classification as the web app shows it (docs/nsfw.md): what a resource's state says, the Library's filter, and
 * a namespace's own choices.
 */
import type { NsfwChoices, NsfwState } from "@/app/openapi-client/types.gen";

/** The Library's NSFW filter: "any" leaves out what a namespace hides, the others are the API's `sensitive`. */
export type SensitiveFilter = "any" | "include" | "only" | "exclude";

export const SENSITIVE_LABEL: Record<SensitiveFilter, string> = {
  any: "As each namespace shows them",
  include: "Include hidden ones",
  only: "Only flagged NSFW",
  exclude: "Leave flagged ones out",
};

export const SENSITIVE_CHIP: Record<SensitiveFilter, string> = {
  any: "NSFW",
  include: "NSFW: include hidden",
  only: "NSFW only",
  exclude: "No NSFW",
};

/** "0.93" */
export const score = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

/** One line on what classification found: "Flagged NSFW: highest score 0.93 (threshold 0.80), 12 of 300 frames". */
export function nsfwText(n: NsfwState, paged = false): string {
  const what = paged ? "page" : "frame";
  const over = n.checked != null && n.of != null && n.of > 1 ? `, ${n.checked} of ${n.of} ${what}s scored` : "";
  const by = n.score != null ? `highest score ${score(n.score)} (threshold ${score(n.threshold)})${over}` : null;
  if (n.manual === true) return `Marked NSFW by hand${by ? `; the model's ${by}` : ""}`;
  if (n.manual === false) return `Marked not NSFW by hand${by ? `; the model's ${by}` : ""}`;
  return `${n.flagged ? "Flagged NSFW" : "Not NSFW"}${by ? `: ${by}` : ""}`;
}

/** A namespace's own on/off: "archive" follows Settings → NSFW. */
export type OwnSwitch = "archive" | "on" | "off";

export function ownSwitch(own: NsfwChoices | null | undefined): OwnSwitch {
  return own?.enabled == null ? "archive" : own.enabled ? "on" : "off";
}

export function switchValue(v: OwnSwitch): boolean | null {
  return v === "archive" ? null : v === "on";
}

/** A threshold typed in a box: null when empty (follow the archive's), undefined when it isn't 0.5 to 0.99. */
export function parseThreshold(text: string): number | null | undefined {
  const t = text.trim();
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) && v >= 0.5 && v <= 0.99 ? Math.round(v * 100) / 100 : undefined;
}
