/** The timeline's model (docs/timeline.md): filters to and from the address, the query they make, kind families,
 * grouping runs of the same actor and kind, and merging new items in. No React here, so it is tested on its own. */

import type { ActivityKind, ActivityMark, StreamActivity, TimelineViewState } from "@/app/openapi-client/types.gen";

export type Activity = StreamActivity;
export type Mark = { reviewed: boolean; important: boolean; hidden: boolean };

export type Filters = {
  /** Kinds of activity (the kinds under each count too); none: every kind but the noisy ones. */
  kinds: string[];
  /** Who did it, by actor id (lens:source/12, lens:account/1). */
  actor: string[];
  /** Namespaces by name. */
  namespace: string[];
  /** An entity's name or id, as activities tag it. */
  tag: string;
  /** Dates (YYYY-MM-DD, the viewer's own days), both ends included. */
  from: string;
  to: string;
  /** An automation's id: what it did or caused. */
  automation: string;
  /** Words in the summary. */
  q: string;
  unreviewed: boolean;
  important: boolean;
  /** Latest (newest first) or For you: the newest reordered by what you react to, comment on and mark. */
  order: Order;
};

export type Order = "latest" | "for_you";

export const NO_FILTERS: Filters = {
  kinds: [],
  actor: [],
  namespace: [],
  tag: "",
  from: "",
  to: "",
  automation: "",
  q: "",
  unreviewed: false,
  important: false,
  order: "latest",
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const list = (v: string | null) =>
  (v ?? "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/** Filters from the address's query (unknown or malformed values are dropped). */
export function fromParams(p: URLSearchParams): Filters {
  const date = (k: string) => (DATE.test(p.get(k) ?? "") ? (p.get(k) as string) : "");
  const auto = p.get("automation") ?? "";
  return {
    kinds: list(p.get("kinds")),
    actor: list(p.get("actor")),
    namespace: list(p.get("ns")),
    tag: (p.get("tag") ?? "").trim(),
    from: date("from"),
    to: date("to"),
    automation: /^\d+$/.test(auto) ? auto : "",
    q: (p.get("q") ?? "").trim(),
    unreviewed: p.get("unreviewed") === "1",
    important: p.get("important") === "1",
    order: p.get("order") === "for_you" ? "for_you" : "latest",
  };
}

/** The address's query for filters: only what is set, in a fixed order, so a view can be linked. */
export function toParams(f: Filters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.kinds.length) p.set("kinds", f.kinds.join(","));
  if (f.actor.length) p.set("actor", f.actor.join(","));
  if (f.namespace.length) p.set("ns", f.namespace.join(","));
  if (f.tag) p.set("tag", f.tag);
  if (f.from) p.set("from", f.from);
  if (f.to) p.set("to", f.to);
  if (f.automation) p.set("automation", f.automation);
  if (f.q) p.set("q", f.q);
  if (f.unreviewed) p.set("unreviewed", "1");
  if (f.important) p.set("important", "1");
  if (f.order === "for_you") p.set("order", "for_you");
  return p;
}

/** Whether any filter is set; the order is a view, not a filter. */
export function isFiltered(f: Filters): boolean {
  return toParams({ ...f, order: "latest" }).toString() !== "";
}

/** How many filters are set, for the phone's Filters button. */
export function filterCount(f: Filters): number {
  return (
    f.kinds.length +
    f.actor.length +
    f.namespace.length +
    [f.tag, f.from || f.to, f.automation, f.q, f.unreviewed, f.important].filter(Boolean).length
  );
}

/** The start (from) or end (to) of a day of the viewer's, as an ISO time in UTC. */
export function dayBound(day: string, end: boolean): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = end ? new Date(y, m - 1, d, 23, 59, 59, 999) : new Date(y, m - 1, d, 0, 0, 0, 0);
  return t.toISOString();
}

export type ListQuery = {
  kinds?: string[];
  actor?: string[];
  namespace?: string[];
  tag?: string;
  since?: string;
  until?: string;
  q?: string;
  where?: string;
  reviewed?: boolean;
  important?: boolean;
  hidden: boolean;
  order?: Order;
};

/** The automation filter as a match tree: what the automation did (its actor) or what one of its runs caused. */
export function automationWhere(id: string): string {
  return JSON.stringify({
    any: [
      { path: "actor.id", op: "equals", value: `lens:automation/${id}` },
      { path: "lens.cause.automation", op: "equals", value: Number(id) },
    ],
  });
}

/** GET /api/v1/activities's query for filters; hidden activities are always left out. */
export function toQuery(f: Filters): ListQuery {
  const q: ListQuery = { hidden: false };
  if (f.kinds.length) q.kinds = f.kinds;
  if (f.actor.length) q.actor = f.actor;
  if (f.namespace.length) q.namespace = f.namespace;
  if (f.tag) q.tag = f.tag;
  if (f.from) q.since = new Date(Date.parse(dayBound(f.from, false)) - 1).toISOString(); // since is "after"
  if (f.to) q.until = dayBound(f.to, true);
  if (f.q) q.q = f.q;
  if (f.automation) q.where = automationWhere(f.automation);
  if (f.unreviewed) q.reviewed = false;
  if (f.important) q.important = true;
  if (f.order === "for_you") q.order = "for_you";
  return q;
}

// ---------- kinds ----------
export type Family =
  "Email" | "Calendar" | "Files" | "Notes" | "Shared" | "Sensors" | "Chat" | "Automations" | "System";
export const FAMILIES: Family[] = [
  "Email",
  "Calendar",
  "Files",
  "Notes",
  "Shared",
  "Sensors",
  "Chat",
  "Automations",
  "System",
];

const FAMILY_OF: [RegExp, Family][] = [
  [/^email\./, "Email"],
  [/^calendar_event\./, "Calendar"],
  [/^(asset|file|upload|web_page|import)\./, "Files"],
  [/^(note|comment|entity)\./, "Notes"],
  [/^share\./, "Shared"],
  [/^sensor\./, "Sensors"],
  [/^chat\./, "Chat"],
  [/^(automation|approval|routine|webhook)\./, "Automations"],
];

/** The family a kind's chip sits under, from its name's noun. */
export function familyOf(kind: string): Family {
  return FAMILY_OF.find(([rx]) => rx.test(kind))?.[1] ?? "System";
}

/** The kinds by family, in the families' order, each family's kinds by label. */
export function kindsByFamily(kinds: Pick<ActivityKind, "name" | "label" | "noisy">[]): [Family, typeof kinds][] {
  return FAMILIES.map((f) => [
    f,
    kinds.filter((k) => familyOf(k.name) === f).sort((a, b) => a.label.localeCompare(b.label)),
  ]).filter(([, ks]) => ks.length > 0) as [Family, typeof kinds][];
}

// ---------- marks ----------
export const UNMARKED: Mark = { reviewed: false, important: false, hidden: false };

export function markOf(marks: Record<string, ActivityMark | Mark | undefined>, id: string): Mark {
  const m = marks[id];
  return m ? { reviewed: !!m.reviewed, important: !!m.important, hidden: !!m.hidden } : UNMARKED;
}

// ---------- grouping ----------
export const GROUP_GAP_MS = 30 * 60 * 1000;

export type Group = {
  /** The first (newest) item's id. */
  key: string;
  items: Activity[];
};

/** Runs of consecutive items with the same actor and kind, each within 30 minutes of the one before, as one group
 * each (a first mailbox sync reads as one line); every other item is a group of its own. */
export function group(items: Activity[], gapMs = GROUP_GAP_MS): Group[] {
  const out: Group[] = [];
  for (const a of items) {
    const last = out[out.length - 1];
    const prev = last?.items[last.items.length - 1];
    if (
      prev &&
      prev.kind === a.kind &&
      prev.actor.id === a.actor.id &&
      Math.abs(Date.parse(prev.published) - Date.parse(a.published)) <= gapMs
    )
      last.items.push(a);
    else out.push({ key: a.id, items: [a] });
  }
  return out;
}

/** What a kind's activities are called, one and many, and the verb an actor does them with. */
const NOUNS: Record<string, [string, string, string]> = {
  "asset.added": ["added", "asset", "assets"],
  "email.received": ["received", "email", "emails"],
  "calendar_event.added": ["added", "calendar event", "calendar events"],
  "calendar_event.changed": ["changed", "calendar event", "calendar events"],
  "feed_post.received": ["received", "post", "posts"],
  "file.added": ["added", "file", "files"],
  "file.changed": ["updated", "file", "files"],
  "upload.added": ["uploaded", "file", "files"],
  "web_page.saved": ["saved", "web page", "web pages"],
  "import.received": ["imported", "item", "items"],
  "asset.processed": ["processed", "asset", "assets"],
  "asset.failed": ["failed on", "asset", "assets"],
  "asset.deleted": ["deleted", "asset", "assets"],
  "comment.added": ["commented", "time", "times"],
  "note.saved": ["saved", "note", "notes"],
  "entity.changed": ["changed", "entity", "entities"],
  "sensor.reading": ["sent", "reading", "readings"],
  "sensor.alert": ["raised", "alert", "alerts"],
  "chat.message": ["sent", "message", "messages"],
  "webhook.received": ["received", "webhook call", "webhook calls"],
  "automation.ran": ["ran", "time", "times"],
  "routine.ran": ["ran", "time", "times"],
};

/** One line for a group: the item's own summary, or "Work Gmail received 37 emails". */
export function groupSummary(g: Group, label?: string): string {
  const first = g.items[0];
  if (g.items.length === 1) return first.summary;
  const n = g.items.length;
  const words = NOUNS[first.kind];
  if (words) return `${first.actor.name} ${words[0]} ${n} ${words[2]}`;
  return `${first.actor.name}: ${n} × ${(label ?? first.kind).toLowerCase()}`;
}

// ---------- new items ----------
/** The items in `incoming` not already among `shown`. */
export function unseen(shown: Activity[], incoming: Activity[]): Activity[] {
  const have = new Set(shown.map((a) => a.id));
  return incoming.filter((a) => !have.has(a.id));
}

/** New items merged on top of those shown: no doubles, newest first. */
export function mergeNew(shown: Activity[], incoming: Activity[]): Activity[] {
  const fresh = unseen(shown, incoming);
  const byTime = (a: Activity, b: Activity) => Date.parse(b.published) - Date.parse(a.published);
  return [...[...fresh].sort(byTime), ...shown];
}

/** The newest time among items, for the next look's `since`. */
export function newest(items: Activity[]): string | undefined {
  let best: string | undefined;
  for (const a of items) if (!best || Date.parse(a.published) > Date.parse(best)) best = a.published;
  return best;
}

// ---------- links ----------
/** Where an activity's object opens in the web app: its own url, or one made from its lens: reference. */
export function objectHref(a: Activity): string | null {
  const url = a.object?.url;
  if (typeof url === "string" && url.startsWith("/")) return url;
  const m = /^lens:([a-z_]+)\/(.+)$/.exec(a.object?.id ?? "");
  if (!m) return null;
  const [, table, id] = m;
  const to: Record<string, string> = {
    recording: `/resources/${id}`,
    sensor: "/sensors",
    storage_source: "/sources",
    chat: `/chat/${id}`,
    entity: `/entities/${id}`,
    topic: `/topics/${id}`,
    automation_run: "/activity",
  };
  return to[table] ?? null;
}

/** The object's own fields to show on expand: the scalars and short lists, without the ones shown already. */
export function objectFields(a: Activity): [string, string][] {
  const skip = new Set(["type", "id", "name", "url"]);
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(a.object ?? {})) {
    if (skip.has(k) || v == null || v === "") continue;
    if (Array.isArray(v)) {
      if (v.every((x) => typeof x !== "object")) out.push([k, v.slice(0, 10).join(", ")]);
    } else if (typeof v !== "object") out.push([k, String(v)]);
  }
  return out;
}

/** A field's name for people: content_type → Content type. */
export function fieldLabel(k: string): string {
  const s = k.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------- asking about items ----------
/** What "Ask about this" hands the page chat for items: for each, its id (so the assistant can mark it), kind and
 * time, who did it and its line, where it opens and its fields. Whole items only, up to `max` characters. */
export function askQuote(items: Activity[], max = 4000): string {
  const out: string[] = [];
  let size = 0;
  for (const a of items) {
    const href = objectHref(a);
    const lines = [
      `Timeline item ${a.id} (${a.kind}, ${a.published})`,
      `${a.actor.name}: ${a.summary}`,
      ...(href ? [`Opens at ${href}`] : []),
      ...objectFields(a).map(([k, v]) => `${fieldLabel(k)}: ${v}`),
    ];
    const text = lines.join("\n");
    if (out.length && size + text.length + 2 > max) break;
    out.push(text);
    size += text.length + 2;
  }
  return out.join("\n\n").slice(0, max);
}

// ---------- the last view, per browser ----------
export const LAST_KEY = "lens.timeline.filters";

export function rememberFilters(f: Filters): void {
  try {
    localStorage.setItem(LAST_KEY, toParams(f).toString());
  } catch {
    /* storage unavailable */
  }
}

export function lastFilters(): Filters | null {
  try {
    const s = localStorage.getItem(LAST_KEY);
    return s ? fromParams(new URLSearchParams(s)) : null;
  } catch {
    return null;
  }
}

/** A saved view's filters as the API keeps them (docs/timeline.md, Saved views). */
export function toViewState(f: Filters): TimelineViewState {
  return {
    kinds: f.kinds,
    actor: f.actor,
    namespace: f.namespace,
    tag: f.tag,
    from_day: f.from || null,
    to_day: f.to || null,
    automation: f.automation ? Number(f.automation) : null,
    q: f.q,
    unreviewed: f.unreviewed,
    important: f.important,
    order: f.order,
  };
}

/** The page's filters from a saved view's. */
export function fromViewState(s: Partial<TimelineViewState>): Filters {
  return {
    kinds: s.kinds ?? [],
    actor: s.actor ?? [],
    namespace: s.namespace ?? [],
    tag: s.tag ?? "",
    from: s.from_day ?? "",
    to: s.to_day ?? "",
    automation: s.automation != null ? String(s.automation) : "",
    q: s.q ?? "",
    unreviewed: !!s.unreviewed,
    important: !!s.important,
    order: s.order === "for_you" ? "for_you" : "latest",
  };
}

/** Whether two sets of filters show the same (the order counts too). */
export function sameFilters(a: Filters, b: Filters): boolean {
  return toParams(a).toString() === toParams(b).toString();
}

/** A name to offer when saving filters as a view: what they pick, in words ("Work · Email received · Unreviewed"). */
export function viewName(f: Filters, labels: Record<string, string> = {}): string {
  const parts = [
    ...(f.q ? [`“${f.q}”`] : []),
    ...f.namespace,
    ...f.kinds.map((k) => labels[k] ?? k),
    ...(f.tag ? [f.tag] : []),
    ...(f.unreviewed ? ["Unreviewed"] : []),
    ...(f.important ? ["Important"] : []),
    ...(f.order === "for_you" ? ["For you"] : []),
  ];
  return (parts.join(" · ") || "My view").slice(0, 60);
}

// ---------- snoozes ----------
/** How long a snooze from the page lasts, by how the menu says it ("Mute Work Gmail for a week"). */
export const SNOOZES = { "an hour": 3_600_000, "a day": 86_400_000, "a week": 7 * 86_400_000 } as const;
export type Snooze = keyof typeof SNOOZES;

/** A `datetime-local` input's value for a moment, in the browser's own time zone (YYYY-MM-DDTHH:mm). */
export function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Where a snooze to a time you pick starts: tomorrow at nine in the morning, local time. */
export function tomorrowMorning(now = new Date()): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0);
}

/** A snooze's end from now, as the `until` of a mute. */
export function snoozeUntil(span: Snooze, now = Date.now()): string {
  return new Date(now + SNOOZES[span]).toISOString();
}
