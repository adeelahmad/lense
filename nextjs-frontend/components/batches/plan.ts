import type { BatchRun, BatchSelection } from "@/app/openapi-client/types.gen";

/** Where recordings come from and what runs on them, as a "Run on…" link carries them. */
export type Plan = {
  selection: BatchSelection;
  run: BatchRun;
  label?: string | null;
};

export function planFromParams(p: URLSearchParams): Plan {
  const ints = (k: string) =>
    (p.get(k) ?? "")
      .split(",")
      .map((x) => Number(x.trim()))
      .filter((n) => Number.isInteger(n) && n > 0);
  const selection: BatchSelection = {};
  const recs = ints("recordings");
  const ents = ints("entity");
  const spk = ints("speaker");
  const col = ints("collection")[0];
  const ns = p.get("ns") || undefined;
  const q = p.get("q")?.trim();
  const extra = Object.fromEntries(
    (["from", "to", "media", "status"] as const).map((k) => [k, p.get(k)?.trim()]).filter(([, v]) => v),
  ) as Record<string, string>;
  if (col) selection.collection = col;
  else if (recs.length) selection.recordings = recs;
  else if (q || Object.keys(extra).length || ents.length > 1)
    // A filter: fixed when the run is confirmed. Keys follow collections (namespaces, speakers, entities, from, to, q, media, status).
    selection.filter = {
      ...(q ? { q } : {}),
      ...(ns ? { namespaces: [ns] } : {}),
      ...(spk.length ? { speakers: spk } : {}),
      ...(ents.length ? { entities: ents } : {}),
      ...extra,
    };
  else {
    if (ns) selection.namespace = ns;
    if (ents.length === 1) selection.entity = ents[0];
    if (spk.length) selection.speaker = spk[0];
  }
  const run: BatchRun = {};
  const tpl = ints("template")[0];
  const pipe = ints("pipeline")[0];
  const steps = (p.get("steps") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (tpl) run.template = tpl;
  else if (pipe) run.pipeline = pipe;
  else if (steps.length) run.steps = steps;
  return { selection, run, label: p.get("label") };
}

/** Collection reports: combine the run's results once it finishes (kept in this browser until then). */
const KEY = (id: number) => `lens.combine.${id}`;

export function rememberCombine(id: number, instructions: string) {
  try {
    localStorage.setItem(KEY(id), instructions);
  } catch {
    /* storage unavailable: the Combine button still works */
  }
}

export function pendingCombine(id: number): string | null {
  try {
    return localStorage.getItem(KEY(id));
  } catch {
    return null;
  }
}

export function forgetCombine(id: number) {
  try {
    localStorage.removeItem(KEY(id));
  } catch {
    /* storage unavailable */
  }
}
