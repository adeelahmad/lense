/**
 * Custom metadata fields: what each type is called and takes, values as typed into a form and as the API keeps them,
 * the checks the server makes (said before saving), and how a value reads. Fields are defined on a namespace or a
 * collection and describe the resources, collections or files inside it.
 */
import type { FieldDef, FieldValue } from "@/app/openapi-client/types.gen";

export type FieldType = FieldDef["type"];
export type FieldTarget = FieldDef["target"];

export const TYPES: { value: FieldType; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "longtext", label: "Long text" },
  { value: "number", label: "Number" },
  { value: "date", label: "Date" },
  { value: "boolean", label: "Yes or no" },
  { value: "choice", label: "One of a list" },
  { value: "choices", label: "Some of a list" },
  { value: "link", label: "Link" },
];
export const TYPE_LABEL = Object.fromEntries(TYPES.map((t) => [t.value, t.label])) as Record<FieldType, string>;

export const TARGETS: { value: FieldTarget; label: string }[] = [
  { value: "resource", label: "Resources" },
  { value: "collection", label: "Collections" },
  { value: "file", label: "Files" },
];
export const TARGET_LABEL = Object.fromEntries(TARGETS.map((t) => [t.value, t.label])) as Record<FieldTarget, string>;

export const LABEL_MAX = 80;
export const TEXT_MAX = 500;
export const LONGTEXT_MAX = 5000;
const DATE_RX = /^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$/;
const LINK_RX = /^https?:\/\/\S+$/;

export const isChoice = (t: FieldType) => t === "choice" || t === "choices";

/** What a form holds for a field: text for most types, true/false/null for yes or no, a list for choices. */
export type Raw = string | boolean | null | string[];
export type Draft = Record<number, Raw>;

/** A saved value as the form shows it. */
export function toRaw(f: Pick<FieldDef, "type">, v: unknown): Raw {
  if (f.type === "boolean") return typeof v === "boolean" ? v : null;
  if (f.type === "choices") return Array.isArray(v) ? v.map(String) : [];
  return v == null ? "" : String(v);
}

export function toDraft(values: FieldValue[]): Draft {
  return Object.fromEntries(values.map((x) => [x.field.id, toRaw(x.field, x.value)]));
}

/** Why a value won't do, as the server would say; null when it's fine (empty is fine: no value). */
export function valueProblem(f: Pick<FieldDef, "type" | "label" | "options">, raw: Raw): string | null {
  if (raw == null || raw === "" || (Array.isArray(raw) && !raw.length)) return null;
  const v = typeof raw === "string" ? raw.trim() : raw;
  switch (f.type) {
    case "text":
      return String(v).length > TEXT_MAX ? `${f.label} can have up to ${TEXT_MAX} characters.` : null;
    case "longtext":
      return String(v).length > LONGTEXT_MAX ? `${f.label} can have up to ${LONGTEXT_MAX} characters.` : null;
    case "number":
      return v === "" || !Number.isFinite(Number(v)) ? `${f.label} is a number.` : null;
    case "date": {
      if (!DATE_RX.test(String(v))) return `${f.label} is a date: YYYY, YYYY-MM or YYYY-MM-DD.`;
      if (String(v).length === 10) {
        const d = new Date(`${v}T00:00:00Z`);
        if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== v)
          return `${f.label}: ${v} isn't a day of the calendar.`;
      }
      return null;
    }
    case "link":
      return LINK_RX.test(String(v)) ? null : `${f.label} is a link starting with http:// or https://.`;
    case "choice":
      return (f.options ?? []).includes(String(v)) ? null : `${f.label} is one of: ${(f.options ?? []).join(", ")}.`;
    default:
      return null;
  }
}

/** A form's value as the API takes it: null to clear. */
export function fromRaw(f: Pick<FieldDef, "type" | "options">, raw: Raw): unknown {
  if (raw == null || (Array.isArray(raw) && !raw.length)) return null;
  if (typeof raw === "boolean") return raw;
  if (Array.isArray(raw)) return (f.options ?? []).filter((o) => raw.includes(o));
  const v = f.type === "longtext" ? raw.trim() : raw.trim().replace(/\s+/g, " ");
  if (!v) return null;
  if (f.type === "number") return Number(v);
  return v;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** The values to send: only the fields whose value changed, keyed by field id. */
export function changedValues(values: FieldValue[], draft: Draft): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const x of values) {
    if (!(x.field.id in draft)) continue;
    const next = fromRaw(x.field, draft[x.field.id]);
    if (!same(next, x.value ?? null)) out[String(x.field.id)] = next;
  }
  return out;
}

/** A value in words: Yes or No, options joined, numbers and text as they are; "—" for none. */
export function valueText(f: Pick<FieldDef, "type">, v: unknown): string {
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/** Where a field is defined: the namespace, or a collection's path in it. */
export function whereDefined(f: Pick<FieldDef, "collection" | "collection_path">, ns: string): string {
  return f.collection == null ? ns : [ns, ...(f.collection_path ?? [])].join(" › ");
}

/** Options as typed: one per line (or separated by commas), trimmed, each once. */
export function parseOptions(text: string): string[] {
  const out: string[] = [];
  for (const o of text.split(/[\n,]/)) {
    const v = o.trim().replace(/\s+/g, " ");
    if (v && !out.some((x) => x.toLowerCase() === v.toLowerCase())) out.push(v);
  }
  return out;
}

/** Why a new field's definition won't do, or null. */
export function definitionProblem(d: { label: string; type: FieldType; options: string }): string | null {
  if (!d.label.trim()) return "Name the field.";
  if (d.label.trim().length > LABEL_MAX) return `A field's name can have up to ${LABEL_MAX} characters.`;
  if (isChoice(d.type) && !parseOptions(d.options).length) return "A choice field needs at least one option.";
  return null;
}

/** Fields defined right here (on the namespace, or on this collection), in their order. */
export function definedHere(fields: FieldDef[], collection: number | null): FieldDef[] {
  return fields.filter((f) => (f.collection ?? null) === collection);
}

/** The Library's field filter, in words: "Format: Lecture", "Interviewer: any value". */
export function fieldFilterLabel(f: { label: string; value: string }): string {
  return `${f.label}: ${f.value.trim() ? f.value.trim() : "any value"}`;
}
