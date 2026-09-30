/**
 * Summaries come in two shapes: the built-in Summarize step ({summary, topics, action_items, people, sentiment,
 * importance}) and prompt-template outputs such as Meeting notes ({tldr, decisions, action_items:[{task, owner, due}],
 * open_questions}). Both become one document: a TL;DR, glyph sections (· key points, ◆ decisions, ☐ action items,
 * ? open questions), chip groups and small facts. Unknown keys are kept as generic sections, never dropped.
 */

export type SummaryItem = {
  text: string;
  who?: string | null;
  due?: string | null;
  t?: number | null;
};
export type SummarySection = {
  key: string;
  title: string;
  glyph: string;
  items: SummaryItem[];
};
export type SummaryDoc = {
  tldr: string | null;
  sections: SummarySection[];
  chips: { key: string; title: string; items: string[] }[];
  facts: { label: string; value: string }[];
  /** Nothing readable in it. */
  empty: boolean;
};

const TLDR = ["tldr", "tl_dr", "summary", "abstract", "overview", "synopsis"];
const SECTIONS: Record<string, { title: string; glyph: string; order: number }> = {
  key_points: { title: "Key points", glyph: "·", order: 0 },
  highlights: { title: "Highlights", glyph: "·", order: 0 },
  points: { title: "Key points", glyph: "·", order: 0 },
  decisions: { title: "Decisions", glyph: "◆", order: 1 },
  action_items: { title: "Action items", glyph: "☐", order: 2 },
  actions: { title: "Action items", glyph: "☐", order: 2 },
  next_steps: { title: "Next steps", glyph: "☐", order: 2 },
  todos: { title: "Action items", glyph: "☐", order: 2 },
  open_questions: { title: "Open questions", glyph: "?", order: 3 },
  questions: { title: "Open questions", glyph: "?", order: 3 },
  risks: { title: "Risks", glyph: "!", order: 4 },
};
const CHIPS: Record<string, string> = {
  topics: "Topics",
  people: "People mentioned",
  tags: "Tags",
  keywords: "Keywords",
  entities: "Entities",
};
const TEXT_KEYS = [
  "task",
  "text",
  "title",
  "point",
  "item",
  "decision",
  "question",
  "description",
  "summary",
  "name",
  "action",
];
const WHO_KEYS = ["owner", "who", "assignee", "speaker", "by", "person"];
const DUE_KEYS = ["due", "deadline", "when", "date"];
const TIME_KEYS = ["t", "time", "at", "start", "timestamp", "t0", "start_ms"];

export function humanKey(k: string): string {
  const t = k.replace(/[_-]+/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : k;
}

/** Milliseconds from "14:32", "1:02:03", seconds (small numbers) or ms (large numbers). */
export function parseTime(v: unknown, key?: string): number | null {
  if (typeof v === "number" && Number.isFinite(v) && v >= 0)
    return key === "start_ms" || key === "t0" || v > 36_000 ? Math.round(v) : Math.round(v * 1000);
  if (typeof v === "string") {
    const m = v.trim().match(/^\[?(\d{1,2}(?::\d{1,2}){1,2})\]?$/);
    if (m) return m[1].split(":").reduce((a, p) => a * 60 + Number(p), 0) * 1000;
  }
  return null;
}

function item(v: unknown): SummaryItem | null {
  if (v == null) return null;
  if (typeof v === "string" || typeof v === "number") {
    const text = String(v).trim();
    const m = text.match(/^\[(\d{1,2}(?::\d{1,2}){1,2})\]\s*(.+)$/);
    return text ? (m ? { text: m[2], t: parseTime(m[1]) } : { text }) : null;
  }
  if (typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const tk = TEXT_KEYS.find((k) => typeof o[k] === "string" && (o[k] as string).trim());
  const text = tk
    ? String(o[tk]).trim()
    : (Object.values(o).find((x) => typeof x === "string" && x.trim()) as string | undefined);
  if (!text) return null;
  const wk = WHO_KEYS.find((k) => typeof o[k] === "string" && (o[k] as string).trim());
  const dk = DUE_KEYS.find((k) => typeof o[k] === "string" && (o[k] as string).trim());
  const timeKey = TIME_KEYS.find((k) => o[k] != null && parseTime(o[k], k) != null);
  return {
    text,
    who: wk ? String(o[wk]) : null,
    due: dk ? String(o[dk]) : null,
    t: timeKey ? parseTime(o[timeKey], timeKey) : null,
  };
}

export function summaryDoc(value: unknown): SummaryDoc {
  const doc: SummaryDoc = {
    tldr: null,
    sections: [],
    chips: [],
    facts: [],
    empty: true,
  };
  if (value == null) return doc;
  if (typeof value === "string") {
    doc.tldr = value.trim() || null;
    doc.empty = !doc.tldr;
    return doc;
  }
  if (Array.isArray(value)) {
    const items = value.map(item).filter((x): x is SummaryItem => Boolean(x));
    if (items.length) doc.sections.push({ key: "items", title: "Items", glyph: "·", items });
    doc.empty = !items.length;
    return doc;
  }
  if (typeof value !== "object") return doc;
  const o = value as Record<string, unknown>;
  const order: { s: SummarySection; order: number }[] = [];
  for (const [k, v] of Object.entries(o)) {
    const key = k.toLowerCase();
    if (!doc.tldr && TLDR.includes(key) && typeof v === "string" && v.trim()) {
      doc.tldr = v.trim();
      continue;
    }
    if (key in CHIPS && Array.isArray(v)) {
      const items = v.map((x) => (typeof x === "string" ? x.trim() : (item(x)?.text ?? ""))).filter(Boolean);
      if (items.length) doc.chips.push({ key, title: CHIPS[key], items });
      continue;
    }
    if (key === "sentiment" && typeof v === "string" && v.trim()) {
      doc.facts.push({ label: "Tone", value: v.trim() });
      continue;
    }
    if (key === "importance" && typeof v === "number") {
      doc.facts.push({ label: "Importance", value: `${Math.round(v)} of 5` });
      continue;
    }
    const known = SECTIONS[key];
    if (Array.isArray(v)) {
      const items = v.map(item).filter((x): x is SummaryItem => Boolean(x));
      if (items.length)
        order.push({
          s: {
            key,
            title: known?.title ?? humanKey(k),
            glyph: known?.glyph ?? "·",
            items,
          },
          order: known?.order ?? 10,
        });
    } else if (typeof v === "string" && v.trim()) {
      order.push({
        s: {
          key,
          title: known?.title ?? humanKey(k),
          glyph: "",
          items: [{ text: v.trim() }],
        },
        order: known?.order ?? 9,
      });
    } else if (typeof v === "number" || typeof v === "boolean") {
      doc.facts.push({ label: humanKey(k), value: String(v) });
    } else if (v && typeof v === "object") {
      const rows = Object.entries(v as Record<string, unknown>)
        .filter(([, x]) => typeof x === "string" || typeof x === "number")
        .map(([kk, x]) => ({ text: `${humanKey(kk)}: ${x}` }));
      if (rows.length)
        order.push({
          s: { key, title: humanKey(k), glyph: "·", items: rows },
          order: 11,
        });
    }
  }
  doc.sections = order.sort((a, b) => a.order - b.order).map((x) => x.s);
  doc.empty = !doc.tldr && !doc.sections.length && !doc.chips.length;
  return doc;
}
