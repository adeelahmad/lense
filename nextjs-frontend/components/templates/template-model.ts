/**
 * Templates (Jinja, rendered by the backend against one recording): the variables a template can use, a check for
 * unknown variables with a "did you mean", autocomplete, the output schema, and the versions diff.
 */

export type Variable = { key: string; type: string };

/** What templates.context() gives a template, as the editor lists it. */
export const VARIABLES: Variable[] = [
  { key: "recording.title", type: "string" },
  { key: "recording.duration", type: "duration · h:mm:ss" },
  { key: "recording.namespace", type: "string" },
  { key: "recording.recorded_at", type: "date" },
  { key: "recording.language", type: "string" },
  { key: "speakers", type: "list · name, talk_ms, words, wpm" },
  { key: "transcript", type: "text with speaker labels (capped)" },
  { key: "full_transcript", type: "text, not capped" },
  { key: "segments", type: "list · time, speaker, text, emotion" },
  { key: "sections", type: "list · title, time" },
  { key: "entities", type: "list · name, type, mentions" },
  { key: "keywords", type: "list of words" },
  { key: "summary", type: "the built-in summary" },
  { key: "outputs", type: "earlier LLM steps’ results" },
  { key: "stats", type: "talk time and pace" },
];

/** Extra completions (fields of the variables above). */
const MORE: Variable[] = [
  { key: "recording.id", type: "number" },
  { key: "recording.duration_ms", type: "number" },
  { key: "recording.source", type: "audio or transcript" },
  { key: "recording.status", type: "string" },
  { key: "speakers[0].name", type: "string" },
  { key: "sections[0].title", type: "string" },
  { key: "summary.tldr", type: "string" },
];

export const ROOTS = ["recording", "speakers", "segments", "full_transcript", "transcript", "sections", "entities", "keywords", "summary", "stats", "outputs"];
const BUILTINS = new Set(["loop", "range", "dict", "lipsum", "cycler", "joiner", "namespace", "caller", "varargs", "kwargs", "self", "super"]);
const WORDS = new Set(["and", "or", "not", "in", "is", "if", "else", "true", "false", "none", "True", "False", "None", "recursive"]);

export function completions(prefix: string, declared: string[] = []): Variable[] {
  const p = prefix.toLowerCase();
  const all = [...VARIABLES, ...MORE, ...declared.map((d) => ({ key: d, type: "set in this template" }))];
  const seen = new Set<string>();
  return all.filter((v) => (p ? v.key.toLowerCase().startsWith(p) || v.key.toLowerCase().includes(`.${p}`) : true) && !seen.has(v.key) && Boolean(seen.add(v.key))).slice(0, 8);
}

export function levenshtein(a: string, b: string): number {
  const d = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let prev = d[0];
    d[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = d[j];
      d[j] = Math.min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return d[b.length];
}

function suggest(name: string, known: string[]): string | undefined {
  let best: string | undefined;
  let score = Infinity;
  for (const k of known) {
    const s = k.startsWith(name) || name.startsWith(k) ? Math.abs(k.length - name.length) * 0.5 : levenshtein(name, k);
    if (s < score) {
      score = s;
      best = k;
    }
  }
  return score <= Math.max(2, Math.floor(name.length / 3)) ? best : undefined;
}

export type Problem = { line: number; index: number; length: number; name: string; expr: string; message: string; suggestion?: string };

const TAG = /\{\{([\s\S]*?)\}\}|\{%-?([\s\S]*?)-?%\}|\{#[\s\S]*?#\}/g;

/** The identifiers an expression reads (not attributes, filters, tests or keyword arguments), with offsets. */
export function readNames(expr: string): { name: string; at: number }[] {
  const masked = expr.replace(/'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"/g, (m) => " ".repeat(m.length));
  const out: { name: string; at: number }[] = [];
  const rx = /[A-Za-z_][A-Za-z0-9_]*/g;
  let m: RegExpExecArray | null;
  while ((m = rx.exec(masked))) {
    const before = masked.slice(0, m.index).trimEnd();
    const after = masked.slice(m.index + m[0].length).trimStart();
    if (/[.|]$/.test(before) || /\d$/.test(before)) continue;
    if (/(^|\s)is(\s+not)?$/.test(before)) continue;
    if (/^=(?!=)/.test(after)) continue;
    if (WORDS.has(m[0])) continue;
    out.push({ name: m[0], at: m.index });
  }
  return out;
}

/** Names a template defines itself: loop targets, set, with, macro names and arguments. */
export function declaredNames(body: string): string[] {
  const names = new Set<string>();
  for (const m of body.matchAll(TAG)) {
    const st = (m[2] ?? "").trim();
    let d: RegExpExecArray | null;
    if ((d = /^for\s+(.+?)\s+in\s/.exec(st))) d[1].split(",").forEach((x) => names.add(x.trim().replace(/[()]/g, "")));
    else if ((d = /^set\s+([\w\s,]+?)\s*(=|$)/.exec(st))) d[1].split(",").forEach((x) => names.add(x.trim()));
    else if ((d = /^with\s+(.+)$/.exec(st))) for (const part of d[1].split(",")) names.add(part.split("=")[0].trim());
    else if ((d = /^macro\s+(\w+)\s*\(([^)]*)\)/.exec(st))) {
      names.add(d[1]);
      d[2].split(",").forEach((x) => names.add(x.split("=")[0].trim()));
    }
  }
  names.delete("");
  return [...names];
}

/** Unknown variables, each with its line, where it is, and the closest known name. */
export function checkTemplate(body: string): Problem[] {
  const declared = declaredNames(body);
  const known = new Set([...ROOTS, ...declared, ...BUILTINS]);
  const problems: Problem[] = [];
  for (const m of body.matchAll(TAG)) {
    const start = m.index ?? 0;
    let expr: string | undefined;
    let offset = 0;
    if (m[1] != null) {
      expr = m[1];
      offset = start + 2;
    } else if (m[2] != null) {
      const inner = m[2];
      const lead = m[0].indexOf(inner);
      const st = inner.trimStart();
      const pad = inner.length - st.length;
      const kw = /^(if|elif|for|set|with)\b/.exec(st)?.[1];
      if (!kw) continue;
      let part = st.slice(kw.length);
      let skip = kw.length;
      if (kw === "for") {
        const i = part.search(/\sin\s/);
        if (i < 0) continue;
        skip += i + 4;
        part = part.slice(i + 4);
      } else if (kw === "set" || kw === "with") {
        const i = part.indexOf("=");
        if (i < 0) continue;
        skip += i + 1;
        part = part.slice(i + 1);
      }
      expr = part;
      offset = start + lead + pad + skip;
    }
    if (expr == null) continue;
    for (const n of readNames(expr)) {
      if (known.has(n.name)) continue;
      const index = offset + n.at;
      const line = body.slice(0, index).split("\n").length;
      const suggestion = suggest(n.name, [...ROOTS, ...declared]);
      const shown = m[1] != null ? `{{ ${expr.trim()} }}` : n.name;
      problems.push({
        line,
        index,
        length: n.name.length,
        name: n.name,
        expr: shown,
        suggestion,
        message: `Line ${line}: ${shown} isn’t a variable.${suggestion ? ` Did you mean {{ ${suggestion} }}?` : ""}`,
      });
    }
  }
  return problems;
}

/** The identifier being typed inside an open {{ at the caret, if any ("rec" in "{{ rec|"). */
export function completionContext(text: string, caret: number): { from: number; prefix: string } | null {
  const before = text.slice(0, caret);
  const open = before.lastIndexOf("{{");
  if (open < 0 || before.lastIndexOf("}}") > open) return null;
  const m = /([A-Za-z_][\w.[\]0-9]*)?$/.exec(before);
  const prefix = m?.[1] ?? "";
  const from = caret - prefix.length;
  if (from < open + 2) return null;
  const between = before.slice(open + 2, from);
  if (/[|.]\s*$/.test(between) && prefix === "") return null;
  if (/\|\s*$/.test(between)) return null;
  return { from, prefix };
}

// ---------- output schema ----------

export type SchemaParse = { ok: true; value: Record<string, unknown> } | { ok: false; error: string; line?: number };

export function parseSchema(text: string): SchemaParse {
  if (!text.trim()) return { ok: false, error: "Enter a JSON Schema, at least {\"type\": \"object\"}." };
  try {
    const v = JSON.parse(text) as Record<string, unknown>;
    if (!v || typeof v !== "object" || Array.isArray(v) || v.type !== "object") return { ok: false, error: "The output schema must be a JSON Schema with \"type\": \"object\"." };
    return { ok: true, value: v };
  } catch (e) {
    const msg = (e as Error).message;
    const pos = /position (\d+)/.exec(msg);
    const line = pos ? text.slice(0, Number(pos[1])).split("\n").length : (Number(/line (\d+)/.exec(msg)?.[1]) || undefined);
    return { ok: false, error: `${line ? `Line ${line}: ` : ""}${msg.replace(/^JSON\.parse: /, "").replace(/ in JSON at position \d+.*$/, "")}`, line };
  }
}

type Schema = { type?: string | string[]; properties?: Record<string, Schema>; required?: string[]; items?: Schema; minItems?: number; maxItems?: number; enum?: unknown[] };

function typeOf(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  if (typeof v === "number") return Number.isInteger(v) ? "integer" : "number";
  return typeof v;
}

/** Check a model's result against the output schema (types, required keys, list sizes, enums). */
export function checkAgainstSchema(value: unknown, schema: Schema, path = ""): string[] {
  const out: string[] = [];
  const where = path || "the result";
  const types = schema.type == null ? null : Array.isArray(schema.type) ? schema.type : [schema.type];
  const t = typeOf(value);
  if (types && !types.some((x) => x === t || (x === "number" && t === "integer"))) return [`${where} should be ${types.join(" or ")}, not ${t}`];
  if (schema.enum && !schema.enum.some((e) => JSON.stringify(e) === JSON.stringify(value))) out.push(`${where} isn’t one of the allowed values`);
  if (t === "object" && value) {
    const o = value as Record<string, unknown>;
    for (const k of schema.required ?? []) if (!(k in o)) out.push(`${path ? `${path}.` : ""}${k} is missing`);
    for (const [k, s] of Object.entries(schema.properties ?? {})) if (k in o) out.push(...checkAgainstSchema(o[k], s, path ? `${path}.${k}` : k));
  }
  if (t === "array") {
    const a = value as unknown[];
    if (schema.minItems != null && a.length < schema.minItems) out.push(`${where} has ${a.length} items; at least ${schema.minItems}`);
    if (schema.maxItems != null && a.length > schema.maxItems) out.push(`${where} has ${a.length} items; at most ${schema.maxItems}`);
    if (schema.items) a.forEach((x, i) => out.push(...checkAgainstSchema(x, schema.items!, `${path}[${i}]`)));
  }
  return out;
}

/** "tldr (string), key_points (3–6), decisions, actions (with owner)" */
export function schemaSummary(schema: Schema): string {
  return Object.entries(schema.properties ?? {})
    .map(([k, s]) => {
      if (s.type === "array") {
        const range = s.minItems != null || s.maxItems != null ? `${s.minItems ?? 0}–${s.maxItems ?? "∞"}` : "";
        const inner = s.items?.required?.length ? `with ${s.items.required.join(", ")}` : "";
        const extra = [range, inner].filter(Boolean).join(", ");
        return extra ? `${k} (${extra})` : k;
      }
      return s.type ? `${k} (${Array.isArray(s.type) ? s.type.join("/") : s.type})` : k;
    })
    .join(", ");
}

// ---------- versions ----------

export type DiffRow = { kind: "hunk"; text: string } | { kind: "line"; sign: " " | "+" | "-"; a?: number; b?: number; text: string };

/** Rows of a unified diff with old and new line numbers. */
export function parseUnifiedDiff(text: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let a = 0;
  let b = 0;
  for (const line of text.split("\n")) {
    if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("\\")) continue;
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
    if (h) {
      a = Number(h[1]);
      b = Number(h[2]);
      rows.push({ kind: "hunk", text: line });
      continue;
    }
    if (!rows.length && line === "") continue;
    const sign = line[0];
    if (sign === "+") rows.push({ kind: "line", sign: "+", b: b++, text: line.slice(1) });
    else if (sign === "-") rows.push({ kind: "line", sign: "-", a: a++, text: line.slice(1) });
    else if (sign === " ") rows.push({ kind: "line", sign: " ", a: a++, b: b++, text: line.slice(1) });
  }
  return rows;
}

/** A line diff of two texts (for the unsaved draft, which the diff endpoint can't see). */
export function lineDiff(before: string, after: string): DiffRow[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;
  const lcs = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      rows.push({ kind: "line", sign: " ", a: i + 1, b: j + 1, text: a[i] });
      i++;
      j++;
    } else if (j < m && (i >= n || lcs[i][j + 1] >= lcs[i + 1][j])) {
      rows.push({ kind: "line", sign: "+", b: j + 1, text: b[j] });
      j++;
    } else {
      rows.push({ kind: "line", sign: "-", a: i + 1, text: a[i] });
      i++;
    }
  }
  return rows;
}

/** Keep the changed lines and a little context around them. */
export function trimContext(rows: DiffRow[], context = 3): DiffRow[] {
  const changed = rows.map((r) => r.kind === "line" && r.sign !== " ");
  if (!changed.some(Boolean)) return [];
  return rows.filter((_, i) => changed.slice(Math.max(0, i - context), i + context + 1).some(Boolean));
}
