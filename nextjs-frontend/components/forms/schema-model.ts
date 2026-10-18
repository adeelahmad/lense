/**
 * Forms from a JSON Schema object (an action's params, a tool's arguments): which control each property gets, how its
 * value is written in the control and read back, and the `{{ }}` variable picker's place in a template.
 */

export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  format?: string;
  maxLength?: number;
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
};

/** text: one line; long: a template box; number; choice: one of an enum; toggle; list: one item a line; json: an
 * object written as JSON; any: a value of no set type (text, or JSON when it parses as a number, object…). */
export type FieldKind = "text" | "long" | "number" | "choice" | "toggle" | "list" | "json" | "any";

export type SchemaField = {
  key: string;
  label: string;
  help?: string;
  kind: FieldKind;
  required: boolean;
  default?: unknown;
  options?: string[];
  /** Whether a `{{ }}` template can stand for the value (filled in when it runs). */
  template: boolean;
};

const LONG = 1000;

/** The fields of an object schema, in its order. `required` adds to the schema's own list. */
export function fieldsOf(schema: JsonSchema | undefined, required: string[] = []): SchemaField[] {
  const need = new Set([...(schema?.required ?? []), ...required]);
  return Object.entries(schema?.properties ?? {}).map(([key, p]) => {
    const type = Array.isArray(p.type) ? p.type.find((t) => t !== "null") : p.type;
    const kind: FieldKind = p.enum
      ? "choice"
      : type === "boolean"
        ? "toggle"
        : type === "integer" || type === "number"
          ? "number"
          : type === "array"
            ? "list"
            : type === "object"
              ? "json"
              : type === "string"
                ? p.format === "prompt" || (p.maxLength ?? 0) >= LONG
                  ? "long"
                  : "text"
                : "any";
    return {
      key,
      label: p.title || key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
      ...(p.description ? { help: p.description } : {}),
      kind,
      required: need.has(key),
      ...(p.default !== undefined ? { default: p.default } : {}),
      ...(p.enum ? { options: p.enum.map(String) } : {}),
      template: kind !== "choice" && kind !== "toggle",
    };
  });
}

export const templated = (v: unknown) => typeof v === "string" && v.includes("{{");

/** A value as its control shows it. */
export function toText(f: SchemaField, v: unknown): string {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  if (f.kind === "list" && Array.isArray(v))
    return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join("\n");
  if (f.kind === "json" || typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
}

/** What a control's text means as the value (undefined: unset, so the default applies). */
export function fromText(f: SchemaField, text: string): unknown {
  const t = text.trim();
  if (!t) return undefined;
  if (templated(t) && f.kind !== "long" && f.kind !== "text") return t;
  switch (f.kind) {
    case "number":
      return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : t;
    case "list":
      return text
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean);
    case "json":
    case "any":
      try {
        const v = JSON.parse(t);
        return f.kind === "any" && typeof v === "string" ? text : v;
      } catch {
        return f.kind === "any" ? text : t;
      }
    default:
      return text;
  }
}

/** What's wrong with a value for its field, or "". */
export function fieldProblem(f: SchemaField, v: unknown): string {
  if (v === undefined || v === "") return f.required && f.default === undefined ? `Fill in ${f.label}.` : "";
  if (templated(v) && f.template) return "";
  if (f.kind === "number" && typeof v !== "number") return "A number, or a {{ }} template.";
  if (f.kind === "json" && (typeof v !== "object" || v === null || Array.isArray(v)))
    return 'An object in JSON, like {"key": "value"}.';
  if (f.kind === "choice" && !f.options?.includes(String(v))) return `One of ${f.options?.join(", ")}.`;
  return "";
}

/** Values with unset ones left out. */
export function setValue(values: Record<string, unknown>, key: string, v: unknown): Record<string, unknown> {
  const next = { ...values };
  if (v === undefined) delete next[key];
  else next[key] = v;
  return next;
}

/** A `{{` being typed just before the caret: where it starts and what's typed after it, or null. */
export function openVariable(text: string, caret: number): { start: number; query: string } | null {
  const m = /\{\{\s*([A-Za-z0-9_.]*)$/.exec(text.slice(0, caret));
  return m ? { start: m.index, query: m[1] } : null;
}

/** The text with the `{{` being typed replaced by `{{ path }}`, and where the caret goes. */
export function insertVariable(text: string, caret: number, path: string): { text: string; caret: number } {
  const at = openVariable(text, caret);
  const start = at ? at.start : caret;
  const rest = text.slice(caret).replace(/^[A-Za-z0-9_.]*\s*\}\}/, "");
  const put = `{{ ${path} }}`;
  return { text: text.slice(0, start) + put + rest, caret: start + put.length };
}
