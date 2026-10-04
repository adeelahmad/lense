/**
 * Topics as the API returns them (GET /topics), and the vocabulary as a tree: a topic shows under each of its broader
 * topics, top topics (no broader one) at the root. Search flattens it to the topics that match.
 */

export type TopicItem = {
  id: number;
  namespace?: string | null;
  label: string;
  alt: string[];
  definition?: string | null;
  broader: number[];
  related: number[];
  recordings: number;
  narrower: number;
  from_entity?: number | null;
};

export type TreeRow = { topic: TopicItem; depth: number; path: string; hasChildren: boolean };

/** Other labels typed as a comma-separated list: trimmed, empty ones and repeats dropped. */
export function splitLabels(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(",")) {
    const v = raw.trim().replace(/\s+/g, " ");
    if (v && !seen.has(v.toLowerCase())) {
      seen.add(v.toLowerCase());
      out.push(v);
    }
  }
  return out;
}

const byLabel = (a: TopicItem, b: TopicItem) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" });

/**
 * The rows of the tree with the `open` paths expanded. A path is the ids from the root joined with "/", so a topic
 * under two broader topics has two rows (and two paths) and each opens on its own. A topic whose broader topics are
 * all unknown counts as top; cycles (which the API refuses) are cut.
 */
export function topicTree(items: TopicItem[], open: Set<string>): TreeRow[] {
  const ids = new Set(items.map((t) => t.id));
  const children = new Map<number, TopicItem[]>();
  const top: TopicItem[] = [];
  for (const t of items) {
    const parents = t.broader.filter((b) => ids.has(b));
    if (!parents.length) top.push(t);
    for (const b of parents) children.set(b, [...(children.get(b) ?? []), t]);
  }
  const rows: TreeRow[] = [];
  const walk = (list: TopicItem[], depth: number, prefix: string, seen: Set<number>) => {
    for (const t of [...list].sort(byLabel)) {
      if (seen.has(t.id)) continue;
      const path = prefix ? `${prefix}/${t.id}` : String(t.id);
      const kids = children.get(t.id) ?? [];
      rows.push({ topic: t, depth, path, hasChildren: kids.length > 0 });
      if (kids.length && open.has(path)) walk(kids, depth + 1, path, new Set([...seen, t.id]));
    }
  };
  walk(top, 0, "", new Set());
  return rows;
}

/** Topics whose label or other labels contain the text (case-insensitive), labels that start with it first. */
export function findTopics(items: TopicItem[], text: string): TopicItem[] {
  const q = text.trim().toLowerCase();
  if (!q) return [];
  const hit = (t: TopicItem) => [t.label, ...t.alt].some((x) => x.toLowerCase().includes(q));
  const starts = (t: TopicItem) => t.label.toLowerCase().startsWith(q);
  return items.filter(hit).sort((a, b) => Number(starts(b)) - Number(starts(a)) || byLabel(a, b));
}

/** The topics a topic may name as broader: not itself, and none it is already broader than (directly or not). */
export function broaderChoices(items: TopicItem[], id: number | null): TopicItem[] {
  if (id === null) return [...items].sort(byLabel);
  const below = new Set<number>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const t of items)
      if (!below.has(t.id) && t.broader.some((b) => below.has(b))) {
        below.add(t.id);
        grew = true;
      }
  }
  return items.filter((t) => !below.has(t.id)).sort(byLabel);
}
