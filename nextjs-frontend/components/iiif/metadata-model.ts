/**
 * Descriptive metadata as the backend keeps it (app/domain/metadata.py): language maps for label, summary and
 * attribution, label/value pairs, rights, provider, date, languages, people, subjects, identifiers, links and access.
 * Pure helpers used by the metadata editor and the IIIF panel: validation mirroring the backend, what changed, and
 * plain-words descriptions of history entries.
 */
import { RIGHTS_RX, rightsShort } from "@/components/iiif/rights";

export type LangMap = Record<string, string[]>;
export type Person = {
  name: string;
  role?: string | null;
  uri?: string | null;
  speaker?: number | null;
};
export type Subject = {
  label: string;
  uri?: string | null;
  entity?: number | null;
};
export type Identifier = { type?: string | null; value: string };
export type Pair = { label: LangMap; value: LangMap };
export type Provider = {
  name: string;
  homepage?: string | null;
  logo?: string | null;
};
export type Related = { id: string; label?: string | null };
export type Access = "public" | "transcript" | "signed-in" | "private";

export type Meta = {
  label?: LangMap | null;
  summary?: LangMap | null;
  metadata?: Pair[] | null;
  rights?: string | null;
  attribution?: LangMap | null;
  provider?: Provider | null;
  navDate?: string | null;
  language?: string[] | null;
  creators?: Person[] | null;
  contributors?: Person[] | null;
  subjects?: Subject[] | null;
  identifiers?: Identifier[] | null;
  homepage?: string | null;
  related?: Related[] | null;
  access?: Access | null;
};

export type Field = keyof Meta;
export const FIELDS: Field[] = [
  "label",
  "summary",
  "metadata",
  "rights",
  "attribution",
  "provider",
  "navDate",
  "language",
  "creators",
  "contributors",
  "subjects",
  "identifiers",
  "homepage",
  "related",
  "access",
];

export const FIELD_LABEL: Record<Field, string> = {
  label: "Title",
  summary: "Summary",
  metadata: "Label / value pairs",
  rights: "Rights",
  attribution: "Required attribution",
  provider: "Provider",
  navDate: "Date",
  language: "Languages spoken",
  creators: "Creators",
  contributors: "Contributors",
  subjects: "Subjects",
  identifiers: "Identifiers",
  homepage: "Related link (homepage)",
  related: "Related links",
  access: "Access",
};

/** Which IIIF / Dublin Core property each field feeds, as shown next to the section titles. */
export const FIELD_MAPS: Partial<Record<Field, string>> = {
  label: "label · dc:title",
  summary: "summary · dc:description",
  navDate: "navDate · dc:date",
  creators: "dc:creator",
  contributors: "dc:contributor",
  subjects: "dc:subject · schema:about",
  rights: "rights",
  attribution: "requiredStatement",
  provider: "provider",
  identifiers: "dc:identifier",
  homepage: "homepage",
  metadata: "metadata[]",
  language: "dc:language",
};

export const ACCESS: {
  value: Access;
  label: string;
  hint: string;
  anon: string;
}[] = [
  {
    value: "public",
    label: "Public",
    hint: "anyone",
    anon: "Anyone gets everything: metadata, transcript, captions and audio.",
  },
  {
    value: "transcript",
    label: "Transcript open",
    hint: "audio after sign-in",
    anon: "Anonymous people get metadata, transcript and captions. Audio asks them to sign in (IIIF Authorization Flow).",
  },
  {
    value: "signed-in",
    label: "Signed-in",
    hint: "members sign in",
    anon: "Anonymous people get the metadata only. Transcript and audio ask them to sign in.",
  },
  {
    value: "private",
    label: "Private",
    hint: "not published",
    anon: "Not published: the Manifest is only visible to people with a role in this namespace.",
  },
];

export const LANG_RX = /^(none|[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,8})*)$/;
const URI_RX = /^https?:\/\/[^\s<>"]+$/;

export function isEmpty(v: unknown): boolean {
  if (v == null) return true;
  if (typeof v === "string") return !v.trim();
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v as object).length === 0;
  return false;
}

/** The first text in a language map (what a viewer shows when your language isn't there). */
export function first(map: LangMap | null | undefined): string {
  for (const vals of Object.values(map ?? {})) if (vals?.[0]) return vals[0];
  return "";
}

/** The text in one language. */
export function inLang(map: LangMap | null | undefined, lang: string): string {
  return map?.[lang]?.[0] ?? "";
}

/** Sets the text in one language; empty text removes the language. Returns null when nothing is left. */
export function withLang(map: LangMap | null | undefined, lang: string, text: string): LangMap | null {
  const out: LangMap = { ...(map ?? {}) };
  if (text.trim()) out[lang] = [text, ...(out[lang] ?? []).slice(1)];
  else delete out[lang];
  return Object.keys(out).length ? out : null;
}

/** "none" means the language isn't known. */
export function langName(code: string): string {
  if (code === "none") return "any language";
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** The languages title and summary are written in, the default first, then the rest in order of appearance. */
export function langsOf(meta: Meta, extra: string[] = [], preferred?: string): string[] {
  const seen = [...Object.keys(meta.label ?? {}), ...Object.keys(meta.summary ?? {}), ...extra];
  const out = [...new Set(seen)];
  if (preferred && out.includes(preferred)) return [preferred, ...out.filter((l) => l !== preferred)];
  return out.length ? out : [preferred ?? "none"];
}

/** ✓ title and summary · ◐ one of them · ○ neither (the language chips). */
export function langStatus(meta: Meta, lang: string): "full" | "partial" | "empty" {
  const t = Boolean(inLang(meta.label, lang).trim());
  const s = Boolean(inLang(meta.summary, lang).trim());
  return t && s ? "full" : t || s ? "partial" : "empty";
}

/** "Title in pt is filled, summary in pt is empty": languages where viewers fall back to another language. */
export function langGaps(meta: Meta): string[] {
  return langsOf(meta).filter((l) => l !== "none" && langStatus(meta, l) === "partial");
}

export function uriError(v: string | null | undefined, what = "address"): string | null {
  if (!v || !v.trim()) return null;
  if (URI_RX.test(v.trim())) return null;
  if (/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(v.trim())) return "Not a valid URI — add https://";
  return `Use an http(s) ${what}`;
}

/** Client-side checks, mirroring the backend's clean(); keyed by field. */
export function validate(meta: Meta): Partial<Record<Field, string>> {
  const e: Partial<Record<Field, string>> = {};
  for (const f of ["label", "summary", "attribution"] as const) {
    const bad = Object.keys(meta[f] ?? {}).find((l) => !LANG_RX.test(l));
    if (bad) e[f] = `“${bad}” isn’t a language code (use none when it’s unknown)`;
  }
  if (meta.metadata?.some((p) => !first(p.label).trim() || !first(p.value).trim()))
    e.metadata = "Every pair needs a label and a value";
  if (meta.rights && !RIGHTS_RX.test(meta.rights.trim()))
    e.rights = "Pick a Creative Commons licence or a RightsStatements.org statement";
  if (meta.provider) {
    const p = meta.provider;
    if (!p.name?.trim()) e.provider = "The provider needs a name";
    else e.provider = uriError(p.homepage, "homepage") ?? uriError(p.logo, "logo address") ?? undefined;
  }
  if (meta.navDate && Number.isNaN(Date.parse(meta.navDate))) e.navDate = "Use a date like 2026-09-30";
  if (meta.language?.some((l) => l === "none" || !LANG_RX.test(l)))
    e.language = "Use language codes such as en, de or pt-BR";
  for (const f of ["creators", "contributors"] as const) {
    const people = meta[f] ?? [];
    if (people.some((p) => !p.name?.trim())) e[f] = "Every person needs a name";
    else {
      const bad = people.map((p) => uriError(p.uri, "link")).find(Boolean);
      if (bad) e[f] = bad;
    }
  }
  if (meta.subjects?.some((s) => !s.label?.trim())) e.subjects = "Every subject needs a label";
  if (meta.identifiers?.some((i) => !i.value?.trim())) e.identifiers = "Identifiers need a value";
  const home = uriError(meta.homepage);
  if (home) e.homepage = home;
  const rel = (meta.related ?? [])
    .map((r) => uriError(r.id, "link") ?? (r.id?.trim() ? null : "Every link needs an address"))
    .find(Boolean);
  if (rel) e.related = rel;
  for (const k of Object.keys(e) as Field[]) if (!e[k]) delete e[k];
  return e;
}

function norm(v: unknown): unknown {
  if (isEmpty(v)) return null;
  if (Array.isArray(v)) return v.map(norm);
  if (v && typeof v === "object")
    return Object.fromEntries(
      Object.entries(v as Record<string, unknown>)
        .filter(([, x]) => !isEmpty(x))
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, x]) => [k, norm(x)]),
    );
  return typeof v === "string" ? v.trim() : v;
}

export function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(norm(a)) === JSON.stringify(norm(b));
}

/** Fields whose value differs between what was loaded and the draft. */
export function dirtyFields(original: Meta, draft: Meta): Field[] {
  return FIELDS.filter((f) => !same(original[f], draft[f]));
}

/** The `set` body for saving: each changed field's value, or null to clear it. */
export function patchFor(draft: Meta, fields: Field[]): Partial<Record<Field, unknown>> {
  return Object.fromEntries(fields.map((f) => [f, isEmpty(draft[f]) ? null : draft[f]]));
}

export type PublishState = "draft" | "private" | "published" | "attention";

/**
 * Draft (not published, with problems that block publishing) · private (not published) · published ·
 * needs attention (published, with validation problems).
 */
export function publishState(access: string | null | undefined, problems: number): PublishState {
  if (!access || access === "private") return problems ? "draft" : "private";
  return problems ? "attention" : "published";
}

export const PUBLISH_BADGE: Record<PublishState, { label: string; tone: "neutral" | "green" | "gate"; hint: string }> =
  {
    draft: { label: "Draft", tone: "neutral", hint: "not published yet" },
    private: { label: "Private", tone: "neutral", hint: "not published" },
    published: {
      label: "Published",
      tone: "green",
      hint: "live at its Manifest URL",
    },
    attention: {
      label: "Needs attention",
      tone: "gate",
      hint: "published, validation errors",
    },
  };

function names(list: { name?: string; label?: string; value?: string }[] | null | undefined): string[] {
  return (list ?? []).map((x) => x.name ?? x.label ?? x.value ?? "").filter(Boolean);
}

function listChange(noun: string, a: unknown, b: unknown): string {
  const before = names(a as never);
  const after = names(b as never);
  const added = after.filter((x) => !before.includes(x));
  const removed = before.filter((x) => !after.includes(x));
  const parts: string[] = [];
  if (added.length) parts.push(added.length === 1 ? `Added ${noun} “${added[0]}”` : `Added ${added.length} ${noun}s`);
  if (removed.length)
    parts.push(removed.length === 1 ? `Removed ${noun} “${removed[0]}”` : `Removed ${removed.length} ${noun}s`);
  return parts.join(", ") || `${noun[0].toUpperCase()}${noun.slice(1)}s edited`;
}

/** One change in plain words: "Rights: none → CC BY-NC 4.0", "Added subject “Corvid-2”", "Summary · en edited". */
export function describeChange(field: string, a: unknown, b: unknown): string {
  const f = field as Field;
  const label = FIELD_LABEL[f] ?? field;
  if (b === undefined && a !== undefined) return `${label}: back to the derived value`;
  if (b === null || (isEmpty(b) && !isEmpty(a))) return `${label} cleared`;
  switch (f) {
    case "rights":
      return `Rights: ${rightsShort(a as string)} → ${rightsShort(b as string)}`;
    case "access":
      return `Access: ${(a as string) || "default"} → ${b as string}`;
    case "navDate":
      return `Date: ${String(a ?? "none").slice(0, 10)} → ${String(b).slice(0, 10)}`;
    case "subjects":
      return listChange("subject", a, b);
    case "creators":
      return listChange("creator", a, b);
    case "contributors":
      return listChange("contributor", a, b);
    case "identifiers":
      return listChange("identifier", a, b);
    case "language":
      return `Languages: ${((a as string[]) ?? []).join(", ") || "none"} → ${((b as string[]) ?? []).join(", ")}`;
    case "label":
    case "summary":
    case "attribution": {
      const am = (a ?? {}) as LangMap;
      const bm = (b ?? {}) as LangMap;
      const langs = [...new Set([...Object.keys(am), ...Object.keys(bm)])].filter((l) => !same(am[l], bm[l]));
      const which = langs.filter((l) => l !== "none");
      return `${label}${which.length ? ` · ${which.join(", ")}` : ""} ${isEmpty(a) ? "set" : "edited"}`;
    }
    default:
      return `${label} ${isEmpty(a) ? "set" : "edited"}`;
  }
}

/** A history entry in plain words; its changed fields are described one by one. */
export function describeEdit(edit: {
  changed: string[];
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}): string {
  const parts = edit.changed.map((k) => describeChange(k, edit.before[k], edit.after[k]));
  return parts.join("; ") || "No visible change";
}

export type Problem = { field?: string; message: string };
export type Profile = {
  required?: string[];
  vocabularies?: { subjects?: string[]; language?: string[] };
};

/** The namespace profile's checks, as the backend runs them: required fields, then controlled vocabularies. */
export function profileProblems(meta: Meta, profile: Profile | null | undefined, ns?: string | null): Problem[] {
  const out: Problem[] = (profile?.required ?? [])
    .filter((f) => isEmpty(meta[f as Field]))
    .map((f) => ({
      field: f,
      message: `${FIELD_LABEL[f as Field] ?? f} is required by the ${ns ?? "namespace"} profile`,
    }));
  for (const [f, vocab] of Object.entries(profile?.vocabularies ?? {})) {
    const values =
      f === "subjects"
        ? (meta.subjects ?? []).map((s) => s.label)
        : ((meta[f as Field] as string[] | null | undefined) ?? []);
    for (const x of values)
      if (!(vocab ?? []).includes(x))
        out.push({
          field: f,
          message: `“${x}” isn’t in this namespace’s list`,
        });
  }
  return out;
}

/** Fields that changed on the server since you started editing, and that you changed too. */
export function conflictingFields(mine: Field[], theirs: string[]): Field[] {
  return mine.filter((f) => theirs.includes(f));
}
