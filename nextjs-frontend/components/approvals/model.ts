/**
 * The approvals inbox (docs/automations.md, Approvals): what waits for the person's pick, grouped by the automation
 * (or conversation) that asked, with what each would do, why, and when it expires. Pure helpers, so the page stays
 * small and these are tested on their own.
 */
import type { Approval } from "@/app/openapi-client/types.gen";
import { relative } from "@/lib/format";

export type ApprovalGroup = {
  key: string;
  title: string;
  /** Where the group is from: an automation's id, or a conversation to open. */
  automation?: number;
  chat?: number;
  items: Approval[];
};

export const DAY = 86_400_000;

export function fromAutomation(a: Approval): boolean {
  return a.origin?.automation != null;
}

/** Waiting approvals in groups: each automation's together, under its name, and the assistant's by conversation;
 * the groups in the order their newest arrived, newest first, as the items are. */
export function groupApprovals(items: Approval[]): ApprovalGroup[] {
  const groups = new Map<string, ApprovalGroup>();
  for (const a of items) {
    const auto = a.origin?.automation ?? null;
    // an event proposed from a date said in an asset (POST /dates/propose) belongs to no conversation
    const said = auto == null && a.chat == null && a.tool === "add_event";
    const key = auto != null ? `automation-${auto}` : said ? "said" : `chat-${a.chat ?? "none"}`;
    let g = groups.get(key);
    if (!g) {
      g =
        auto != null
          ? { key, title: a.automation || `Automation ${auto}`, automation: auto, items: [] }
          : said
            ? { key, title: "Events proposed from dates said", items: [] }
            : { key, title: "The assistant, in a conversation", chat: a.chat ?? undefined, items: [] };
      groups.set(key, g);
    }
    g.items.push(a);
  }
  return [...groups.values()];
}

/** What an action's effect means for the person, in words. */
export function effectLabel(effect: string | null | undefined): string | null {
  switch (effect) {
    case "out":
      return "Reaches outside Lens";
    case "lens":
      return "Changes something in Lens";
    case "self":
      return "Changes only the automation";
    case "read":
      return "Only reads";
    default:
      return null;
  }
}

/** When it expires and nothing runs: "Expires in 6 days", "Expires within a day", or "Expired". */
export function expiresLine(expires: string | null | undefined, now = Date.now()): string | null {
  if (!expires) return null;
  const t = Date.parse(expires);
  if (Number.isNaN(t)) return null;
  const left = t - now;
  if (left <= 0) return "Expired";
  if (left < DAY) return "Expires within a day";
  const days = Math.floor(left / DAY);
  return `Expires in ${days} day${days === 1 ? "" : "s"}`;
}

/** A param's value in one short line. */
export function shortValue(v: unknown, max = 160): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  const line = (s ?? "").replace(/\s+/g, " ").trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** What the action was given, one line per param, in the order the run filled them in. */
export function paramLines(params: Record<string, unknown> | null | undefined): { key: string; value: string }[] {
  return Object.entries(params ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([key, v]) => ({ key, value: shortValue(v) }));
}

/** The edit form's text for each param: strings as they are, anything else as JSON. */
export function draftOf(params: Record<string, unknown> | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(params ?? {})) out[k] = typeof v === "string" ? v : JSON.stringify(v);
  return out;
}

/** The changed params from the edit form, typed as they were (a number stays a number, a list a list); a field
 * emptied is null, which leaves that param out. Throws on JSON that doesn't read, naming the param. */
export function changedParams(
  draft: Record<string, string>,
  original: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const was = original ?? {};
  for (const [k, text] of Object.entries(draft)) {
    const before = was[k];
    const now = text.trim() === "" ? null : asTyped(k, text, before);
    if (JSON.stringify(now) !== JSON.stringify(before === "" ? null : (before ?? null))) out[k] = now;
  }
  return out;
}

function asTyped(key: string, text: string, before: unknown): unknown {
  if (typeof before === "string" || before === undefined || before === null) return text;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${key} isn’t valid: write it as before (${shortValue(before, 40)})`);
  }
}

/** A decided approval in one line: what became of it. */
export function outcomeLine(
  a: Pick<Approval, "status" | "summary" | "error" | "decided_by" | "decided_at">,
  now = Date.now(),
) {
  const what = a.summary || "An action";
  const when = a.decided_at ? ` · ${relative(a.decided_at, now)}` : "";
  const by = a.decided_by ? ` by ${a.decided_by}` : "";
  switch (a.status) {
    case "done":
      return `Approved${by}: ${what}${when}`;
    case "failed":
      return `Approved${by}, but it failed: ${what}${a.error ? ` (${shortValue(a.error, 120)})` : ""}${when}`;
    case "declined":
      return `Declined${by}: ${what}${when}`;
    case "expired":
      return `Expired with no pick: ${what}${when}`;
    case "running":
      return `Carrying it out: ${what}`;
    default:
      return what;
  }
}

/** The count in the top bar's badge and the nav: up to 99, then 99+. */
export function badgeCount(n: number | null | undefined): string | null {
  if (!n || n < 1) return null;
  return n > 99 ? "99+" : String(n);
}
