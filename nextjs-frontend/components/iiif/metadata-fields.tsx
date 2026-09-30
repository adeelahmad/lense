"use client";

import { useQuery } from "@tanstack/react-query";
import { Building2, Plus, Tag, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { Entities } from "@/app/openapi-client";
import {
  FIELD_MAPS,
  inLang,
  LANG_RX,
  langGaps,
  langName,
  langStatus,
  withLang,
  type Field,
  type Identifier,
  type Meta,
  type Pair,
  type Person,
  type Related,
  type Subject,
} from "@/components/iiif/metadata-model";
import { canonicalRights, RIGHTS, rightsFor } from "@/components/iiif/rights";
import { Button } from "@/components/ui/button";
import { Field as FormField, Input, Select, Textarea } from "@/components/ui/field";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

export type SetMeta = <K extends Field>(field: K, value: Meta[K]) => void;
export type FieldProps = { draft: Meta; set: SetMeta; errors: Partial<Record<Field, string>>; readOnly: boolean; readOnlyReason?: string };

/** "Title and summary  label · summary" with optional controls on the right. */
export function SectionHead({ title, maps, children, id }: { title: ReactNode; maps?: string; children?: ReactNode; id?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h3 id={id} className="flex-1 text-[14px] font-bold leading-tight text-fg">
        {title} {maps && <span className="font-mono text-[11px] font-medium text-fg-muted">{maps}</span>}
      </h3>
      {children}
    </div>
  );
}

function RemoveButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" aria-label={label} onClick={onClick} disabled={disabled} className="grid size-7 shrink-0 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg disabled:opacity-40">
      <X className="size-3.5" />
    </button>
  );
}

function AddButton({ children, onClick, disabled, reason }: { children: ReactNode; onClick: () => void; disabled?: boolean; reason?: string }) {
  return (
    <Button variant="ghost" size="xs" icon={<Plus />} onClick={onClick} disabled={disabled} disabledReason={disabled ? reason : undefined} className="self-start">
      {children}
    </Button>
  );
}

const GLYPH = { full: ["✓", "text-green-dark"], partial: ["◐", "text-gold-dark"], empty: ["○", "text-fg-muted"] } as const;

/** Title and summary per language, with language chips: ✓ both filled · ◐ one · ○ neither (MD1). */
export function TitleSummary({
  draft,
  set,
  errors,
  readOnly,
  langs,
  lang,
  onLang,
  onAddLang,
  onRemoveLang,
  suggested,
  required,
}: FieldProps & {
  langs: string[];
  lang: string;
  onLang: (l: string) => void;
  onAddLang: (l: string) => void;
  onRemoveLang: (l: string) => void;
  /** The summary comes from the Summarize step (nobody has saved one). */
  suggested: boolean;
  required: { label: boolean; summary: boolean };
}) {
  const [adding, setAdding] = useState(false);
  const [code, setCode] = useState("");
  const codeOk = LANG_RX.test(code.trim()) && code.trim() !== "none" && !langs.includes(code.trim());
  const gaps = langGaps(draft);
  const other = langs.find((l) => l !== lang && langStatus(draft, l) === "full") ?? langs.find((l) => l !== lang);
  const titleMissing = required.label && !inLang(draft.label, lang).trim() && !Object.keys(draft.label ?? {}).length;
  const summaryMissing = required.summary && !Object.keys(draft.summary ?? {}).length;

  return (
    <div className="flex flex-col gap-2">
      <SectionHead title="Title and summary" maps="label · summary">
        <div role="tablist" aria-label="Metadata languages" className="flex flex-wrap items-center gap-1.5">
          {langs.map((l) => {
            const [g, c] = GLYPH[langStatus(draft, l)];
            const on = l === lang;
            return (
              <button
                key={l}
                type="button"
                role="tab"
                aria-selected={on}
                title={langName(l)}
                onClick={() => onLang(l)}
                className={cn(
                  "inline-flex h-[26px] items-center gap-1 rounded-pill border px-[9px] text-[12px] font-semibold",
                  on ? "border-blue bg-blue-surface text-fg-accent" : "border-border bg-background text-fg-strong hover:bg-surface",
                )}
              >
                {l === "none" ? "any" : l}
                <span aria-hidden className={cn("font-extrabold", c)}>
                  {g}
                </span>
                <span className="sr-only">{{ full: "title and summary filled", partial: "partly filled", empty: "empty" }[langStatus(draft, l)]}</span>
              </button>
            );
          })}
          {adding ? (
            <form
              className="flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                if (!codeOk) return;
                onAddLang(code.trim());
                setCode("");
                setAdding(false);
              }}
            >
              <Input aria-label="Language code" placeholder="pt" value={code} onChange={(e) => setCode(e.target.value)} className="h-[26px] w-20 px-2 text-[12px]" autoFocus mono />
              <Button type="submit" size="xs" variant="secondary" disabled={!codeOk}>
                Add
              </Button>
              <RemoveButton label="Cancel adding a language" onClick={() => setAdding(false)} />
            </form>
          ) : (
            <button
              type="button"
              disabled={readOnly}
              onClick={() => setAdding(true)}
              className="h-[26px] rounded-pill border border-dashed border-border px-[9px] text-[12px] font-semibold text-fg-secondary hover:bg-surface disabled:opacity-50"
            >
              + Language
            </button>
          )}
        </div>
      </SectionHead>
      <FormField label={`Title · ${lang === "none" ? "any language" : lang}`} error={errors.label ?? (titleMissing ? "Required by this namespace’s profile" : null)}>
        {(f) => (
          <Input id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} value={inLang(draft.label, lang)} readOnly={readOnly} onChange={(e) => set("label", withLang(draft.label, lang, e.target.value))} />
        )}
      </FormField>
      <FormField
        label={`Summary · ${lang === "none" ? "any language" : lang}`}
        hint={suggested ? "Suggested from the Summarize step — edit freely" : undefined}
        error={errors.summary ?? (summaryMissing ? "Required by this namespace’s profile" : null)}
      >
        {(f) => (
          <Textarea id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} rows={4} value={inLang(draft.summary, lang)} readOnly={readOnly} onChange={(e) => set("summary", withLang(draft.summary, lang, e.target.value))} />
        )}
      </FormField>
      {gaps.length > 0 && (
        <div className="flex gap-2 rounded-[10px] border border-gold-border bg-gold-surface px-3 py-2 text-[12.5px] leading-[1.4] text-fg">
          <span aria-hidden className="font-extrabold text-gold-dark">
            ◆
          </span>
          <span>
            {gaps.map((g) => {
              const t = inLang(draft.label, g).trim();
              return (
                <span key={g} className="block">
                  {t ? "Title" : "Summary"} in <b>{g}</b> is filled, {t ? "summary" : "title"} in {g} is empty. Viewers fall back to {other && other !== g ? (other === "none" ? "another language" : other) : "another language"}.
                </span>
              );
            })}
          </span>
        </div>
      )}
      {langs.length > 1 && lang !== langs[0] && !readOnly && (
        <button type="button" onClick={() => onRemoveLang(lang)} className="self-start text-[12px] font-semibold text-red-dark hover:underline">
          Remove {lang === "none" ? "the language-less" : lang} title and summary
        </button>
      )}
    </div>
  );
}

/** Date (navDate) and the languages spoken. */
export function DateLanguages({ draft, set, errors, readOnly, fromTranscribe, vocabulary }: FieldProps & { fromTranscribe: boolean; vocabulary?: string[] }) {
  const [code, setCode] = useState("");
  const langs = draft.language ?? [];
  const add = () => {
    const c = code.trim();
    if (!c || c === "none" || !LANG_RX.test(c) || langs.includes(c)) return;
    set("language", [...langs, c]);
    setCode("");
  };
  const dateValue = (draft.navDate ?? "").slice(0, 10);
  return (
    <div className="grid gap-3.5 sm:grid-cols-2">
      <FormField label="Date (navDate)" hint="ISO 8601 · shown in viewers’ timelines" error={errors.navDate}>
        {(f) => (
          <Input
            id={f.id}
            aria-describedby={f.describedBy}
            invalid={f.invalid}
            type="date"
            value={dateValue}
            readOnly={readOnly}
            onChange={(e) => set("navDate", e.target.value || null)}
          />
        )}
      </FormField>
      <FormField label="Languages spoken" hint={errors.language ? undefined : fromTranscribe ? "detected by Transcribe" : vocabulary?.length ? `This namespace uses: ${vocabulary.join(", ")}` : undefined} error={errors.language}>
        {(f) => (
          <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-sm border border-border px-2 py-1.5">
            {langs.map((l) => (
              <span key={l} className="inline-flex h-[26px] items-center gap-1 rounded-xs bg-surface-neutral px-[9px] text-[12px] font-semibold">
                {langName(l)} · {l}
                {!readOnly && (
                  <button type="button" aria-label={`Remove ${langName(l)}`} onClick={() => set("language", langs.filter((x) => x !== l))} className="text-fg-muted hover:text-fg">
                    ×
                  </button>
                )}
              </span>
            ))}
            {!readOnly && (
              <input
                id={f.id}
                aria-describedby={f.describedBy}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    add();
                  }
                }}
                onBlur={add}
                placeholder={langs.length ? "" : "en, pt-BR…"}
                className="min-w-[70px] flex-1 bg-transparent text-[13px] outline-none placeholder:text-fg-muted"
              />
            )}
          </div>
        )}
      </FormField>
    </div>
  );
}

type Row = Person & { kind: "creators" | "contributors" };

/** Creators and contributors; people linked to the recording's speakers show it (MD1). */
export function People({ draft, set, errors, readOnly, speakers }: FieldProps & { speakers: { id: number; name: string }[] }) {
  const rows: Row[] = [...(draft.creators ?? []).map((p) => ({ ...p, kind: "creators" as const })), ...(draft.contributors ?? []).map((p) => ({ ...p, kind: "contributors" as const }))];
  const write = (next: Row[]) => {
    const strip = (r: Row): Person => ({ name: r.name, role: r.role, uri: r.uri, speaker: r.speaker });
    set("creators", next.filter((r) => r.kind === "creators").map(strip));
    set("contributors", next.filter((r) => r.kind === "contributors").map(strip));
  };
  const update = (i: number, patch: Partial<Row>) => write(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const linked = new Set(rows.map((r) => r.speaker).filter(Boolean));
  const unlinked = speakers.filter((s) => !linked.has(s.id));
  const speakerName = (id?: number | null) => speakers.find((s) => s.id === id)?.name;

  return (
    <div className="flex flex-col gap-2">
      <SectionHead title="Creators and contributors" maps="dc:creator · dc:contributor" />
      {rows.length === 0 && <p className="text-[13px] text-fg-muted">Nobody yet. Add the people who made or appear in this recording.</p>}
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,1fr)_28px] items-start gap-2 rounded-[10px] border border-border px-2.5 py-2 sm:grid-cols-[minmax(0,1.2fr)_130px_minmax(0,0.8fr)_minmax(0,1fr)_28px] sm:items-center">
          <div className="flex min-w-0 items-center gap-2">
            <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", r.speaker ? "bg-blue" : "bg-fg-muted")} />
            <Input aria-label="Name" value={r.name} readOnly={readOnly} onChange={(e) => update(i, { name: e.target.value })} className="h-8 text-[13.5px] font-semibold" />
          </div>
          <div className="row-start-2 flex flex-wrap gap-2 sm:contents">
            <Select
              aria-label="Creator or contributor"
              size="sm"
              value={r.kind}
              disabled={readOnly}
              onChange={(e) => update(i, { kind: e.target.value as Row["kind"] })}
              options={[
                { value: "creators", label: "Creator" },
                { value: "contributors", label: "Contributor" },
              ]}
              className="w-[130px]"
            />
            <Input aria-label="Role" placeholder="role, e.g. host" value={r.role ?? ""} readOnly={readOnly} onChange={(e) => update(i, { role: e.target.value || null })} className="h-8 text-[13px] sm:w-auto" />
            {r.speaker ? (
              <span className="self-center text-[12px] leading-[1.3] text-fg-muted">linked to speaker {speakerName(r.speaker) ?? `#${r.speaker}`} ✓</span>
            ) : (
              <Input aria-label="Authority link (Wikidata or other URI)" placeholder="https://www.wikidata.org/wiki/Q…" value={r.uri ?? ""} readOnly={readOnly} mono onChange={(e) => update(i, { uri: e.target.value || null })} className="h-8 text-[12px]" />
            )}
          </div>
          <RemoveButton label={`Remove ${r.name || "person"}`} disabled={readOnly} onClick={() => write(rows.filter((_, j) => j !== i))} />
        </div>
      ))}
      {errors.creators && <p className="text-[12.5px] text-red-dark">{errors.creators}</p>}
      {errors.contributors && <p className="text-[12.5px] text-red-dark">{errors.contributors}</p>}
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-2">
          <AddButton onClick={() => write([...rows, { name: "", role: null, kind: "creators" }])}>Add person</AddButton>
          {unlinked.length > 0 && (
            <Select
              aria-label="Add a speaker from this recording"
              size="sm"
              value=""
              onChange={(e) => {
                const s = speakers.find((x) => String(x.id) === e.target.value);
                if (s) write([...rows, { name: s.name, role: "speaker", speaker: s.id, kind: "contributors" }]);
              }}
              options={[{ value: "", label: "Add a speaker…" }, ...unlinked.map((s) => ({ value: String(s.id), label: s.name }))]}
              className="w-[180px]"
            />
          )}
        </div>
      )}
    </div>
  );
}

type Option = { key: string; label: string; detail: string; source: string; icon: ReactNode; subject: Subject };

/** Subjects: namespace entities first, then the profile's vocabulary, or your own words (MD1 subject picker). */
export function Subjects({ draft, set, errors, readOnly, namespace, vocabulary }: FieldProps & { namespace?: string | null; vocabulary?: string[] }) {
  const client = useApiClient();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const subjects = draft.subjects ?? [];

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 200);
    return () => clearTimeout(t);
  }, [q]);

  const entities = useQuery({
    queryKey: ["subject-picker", namespace, debounced],
    queryFn: () => data(Entities.listEntities({ client, query: { q: debounced, namespaces: namespace ?? undefined, limit: 6, sort: "mentions" } })),
    enabled: open && debounced.length > 1 && Boolean(namespace),
    staleTime: 30_000,
  });

  const options = useMemo<Option[]>(() => {
    const have = new Set(subjects.map((s) => s.label.toLowerCase()));
    const out: Option[] = [];
    const items = ((entities.data as { items?: { id: number; name: string; type_label?: string; mentions?: number }[] } | undefined)?.items ?? []).filter((e) => !have.has(e.name.toLowerCase()));
    for (const e of items)
      out.push({
        key: `e${e.id}`,
        label: e.name,
        detail: `${e.type_label ?? "Entity"} · ${e.mentions ?? 0} mentions in this namespace`,
        source: "entity",
        icon: <Building2 />,
        subject: { label: e.name, entity: e.id },
      });
    const term = debounced.toLowerCase();
    for (const v of vocabulary ?? [])
      if (term && v.toLowerCase().includes(term) && !have.has(v.toLowerCase()))
        out.push({ key: `v${v}`, label: v, detail: "This namespace’s vocabulary", source: "vocabulary", icon: <Tag />, subject: { label: v } });
    if (q.trim() && !have.has(q.trim().toLowerCase()) && !out.some((o) => o.label.toLowerCase() === q.trim().toLowerCase()))
      out.push({ key: "free", label: q.trim(), detail: "Add as your own subject", source: "", icon: <Plus />, subject: { label: q.trim() } });
    return out;
  }, [entities.data, vocabulary, debounced, q, subjects]);

  const pick = (o: Option) => {
    set("subjects", [...subjects, o.subject]);
    setQ("");
    setActive(0);
    inputRef.current?.focus();
  };

  return (
    <div className="flex flex-col gap-2">
      <SectionHead title="Subjects" maps={FIELD_MAPS.subjects} />
      <div className="relative">
        <div
          className={cn(
            "flex min-h-10 flex-wrap items-center gap-1.5 rounded-sm border px-2 py-1.5",
            open ? "border-blue shadow-[0_0_0_3px_var(--intent-surface)]" : "border-border",
            errors.subjects && "border-red",
          )}
        >
          {subjects.map((s, i) => (
            <span key={`${s.label}${i}`} className="inline-flex h-[26px] items-center gap-[5px] rounded-xs bg-surface-neutral px-[9px] text-[12px] font-semibold">
              {s.label}
              <span className="font-mono text-[10.5px] font-medium text-fg-muted">{s.entity ? "entity" : s.uri ? "URI" : vocabulary?.includes(s.label) ? "vocabulary" : ""}</span>
              {!readOnly && (
                <button type="button" aria-label={`Remove subject ${s.label}`} onClick={() => set("subjects", subjects.filter((_, j) => j !== i))} className="text-fg-muted hover:text-fg">
                  ×
                </button>
              )}
            </span>
          ))}
          {!readOnly && (
            <input
              ref={inputRef}
              role="combobox"
              aria-expanded={open && options.length > 0}
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
              aria-label="Add a subject"
              placeholder={subjects.length ? "" : "Search this namespace’s entities…"}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOpen(true);
                setActive(0);
              }}
              onFocus={() => setOpen(true)}
              onBlur={() => setTimeout(() => setOpen(false), 150)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(a + 1, options.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(a - 1, 0));
                } else if (e.key === "Enter" && options[active]) {
                  e.preventDefault();
                  pick(options[active]);
                } else if (e.key === "Escape") setOpen(false);
                else if (e.key === "Backspace" && !q && subjects.length) set("subjects", subjects.slice(0, -1));
              }}
              className="min-w-[120px] flex-1 bg-transparent text-[13px] outline-none placeholder:text-fg-muted"
            />
          )}
        </div>
        {open && options.length > 0 && (
          <ul id={listId} role="listbox" className="absolute left-0 top-[calc(100%+6px)] z-30 w-full max-w-[460px] rounded-md border border-border bg-background p-1.5 shadow-3">
            {options.map((o, i) => (
              <li
                key={o.key}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(o);
                }}
                onMouseEnter={() => setActive(i)}
                className={cn("grid cursor-pointer grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-sm px-2.5 py-2 [&_svg]:size-[15px] [&_svg]:text-fg-secondary", i === active && "bg-blue-surface")}
              >
                {o.icon}
                <span className="flex min-w-0 flex-col gap-[3px]">
                  <b className="truncate text-[13px] font-semibold leading-tight">{o.label}</b>
                  <span className="truncate text-[11.5px] leading-tight text-fg-muted">{o.detail}</span>
                </span>
                <code className="font-mono text-[11px] text-fg-muted">{o.source}</code>
              </li>
            ))}
          </ul>
        )}
      </div>
      <p className={cn("text-[12px] leading-[1.35]", errors.subjects ? "text-red-dark" : "text-fg-muted")}>
        {errors.subjects ?? "Searches this namespace’s entities first, then its vocabulary. Wikidata, GeoNames and LCSH lookups aren’t available yet."}
      </p>
    </div>
  );
}

/** Rights (a licence or statement URI) and the required attribution. */
export function RightsAttribution({ draft, set, errors, readOnly, lang, attributionRequired }: FieldProps & { lang: string; attributionRequired: boolean }) {
  const known = rightsFor(draft.rights);
  const [custom, setCustom] = useState(Boolean(draft.rights && !known));
  const attr = inLang(draft.attribution, lang) || inLang(draft.attribution, Object.keys(draft.attribution ?? {})[0] ?? lang);
  const attrLang = draft.attribution?.[lang] ? lang : Object.keys(draft.attribution ?? {})[0] ?? lang;
  return (
    <div className="grid gap-3.5 sm:grid-cols-2">
      <div className="flex flex-col gap-2">
        <SectionHead title="Rights" maps="rights" />
        {draft.rights && (
          <div className="flex min-w-0 items-center gap-2.5 rounded-[10px] border border-border px-3 py-2.5">
            <span className="h-7 shrink-0 rounded-xs bg-fg px-2 text-[11px] font-extrabold leading-7 tracking-[.04em] text-background">{known?.code ?? "URI"}</span>
            <span className="flex min-w-0 flex-col gap-[3px]">
              <span className="text-[13px] font-semibold leading-tight">{known?.name ?? "Custom statement"}</span>
              <code className="truncate font-mono text-[11px] leading-tight text-fg-muted">{draft.rights}</code>
            </span>
          </div>
        )}
        <Select
          aria-label="Rights statement"
          disabled={readOnly}
          value={custom ? "other" : (known?.uri ?? "")}
          onChange={(e) => {
            if (e.target.value === "other") setCustom(true);
            else {
              setCustom(false);
              set("rights", e.target.value || null);
            }
          }}
          options={[{ value: "", label: "None" }, ...RIGHTS.map((r) => ({ value: r.uri, label: `${r.code} — ${r.name}` })), { value: "other", label: "Other statement URI…" }]}
        />
        {custom && <Input aria-label="Rights statement URI" mono placeholder="http://rightsstatements.org/vocab/…" value={draft.rights ?? ""} readOnly={readOnly} onChange={(e) => set("rights", e.target.value ? canonicalRights(e.target.value) : null)} invalid={Boolean(errors.rights)} />}
        <p className={cn("text-[12px] leading-[1.35]", errors.rights ? "text-red-dark" : "text-fg-muted")}>{errors.rights ?? "Pick a Creative Commons licence or a RightsStatements.org statement"}</p>
      </div>
      <div className="flex flex-col gap-2">
        <SectionHead title="Required attribution" maps="requiredStatement" />
        <FormField error={errors.attribution ?? (attributionRequired && !attr ? "Required by this namespace’s profile" : null)} hint="Viewers show it next to the player">
          {(f) => (
            <Input
              id={f.id}
              aria-label="Required attribution"
              aria-describedby={f.describedBy}
              invalid={f.invalid}
              value={attr}
              readOnly={readOnly}
              onChange={(e) => set("attribution", withLang(draft.attribution, attrLang, e.target.value))}
            />
          )}
        </FormField>
      </div>
    </div>
  );
}

export function ProviderEditor({ draft, set, errors, readOnly }: FieldProps) {
  const p = draft.provider ?? { name: "" };
  const up = (patch: Partial<typeof p>) => {
    const next = { ...p, ...patch };
    set("provider", next.name || next.homepage || next.logo ? next : null);
  };
  return (
    <div className="flex flex-col gap-2">
      <SectionHead title="Provider" maps="provider" />
      <div className="grid gap-3 sm:grid-cols-3">
        <Input aria-label="Provider name" placeholder="Organisation name" value={p.name ?? ""} readOnly={readOnly} onChange={(e) => up({ name: e.target.value })} />
        <Input aria-label="Provider homepage" placeholder="https://…" mono value={p.homepage ?? ""} readOnly={readOnly} onChange={(e) => up({ homepage: e.target.value || null })} />
        <Input aria-label="Provider logo address" placeholder="https://…/logo.png" mono value={p.logo ?? ""} readOnly={readOnly} onChange={(e) => up({ logo: e.target.value || null })} />
      </div>
      <p className={cn("text-[12px]", errors.provider ? "text-red-dark" : "text-fg-muted")}>{errors.provider ?? "Who holds the recording. Viewers show the name and logo."}</p>
    </div>
  );
}

/** Related link (homepage), identifiers and other related links. */
export function IdentifiersLinks({ draft, set, errors, readOnly }: FieldProps) {
  const ids: Identifier[] = draft.identifiers ?? [];
  const related: Related[] = draft.related ?? [];
  return (
    <div className="flex flex-col gap-3.5">
      <div className="grid gap-3.5 sm:grid-cols-2">
        <FormField label="Related link (homepage)" error={errors.homepage}>
          {(f) => <Input id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} mono placeholder="https://…" value={draft.homepage ?? ""} readOnly={readOnly} onChange={(e) => set("homepage", e.target.value || null)} />}
        </FormField>
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold leading-tight text-fg-strong">Identifiers</span>
          {ids.map((id, i) => (
            <div key={i} className="grid grid-cols-[110px_minmax(0,1fr)_28px] items-center gap-2">
              <Input aria-label="Identifier type" placeholder="type" value={id.type ?? ""} readOnly={readOnly} onChange={(e) => set("identifiers", ids.map((x, j) => (j === i ? { ...x, type: e.target.value || null } : x)))} className="h-8 text-[12.5px]" />
              <Input aria-label="Identifier" mono value={id.value} readOnly={readOnly} onChange={(e) => set("identifiers", ids.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} className="h-8 text-[12.5px]" />
              <RemoveButton label="Remove identifier" disabled={readOnly} onClick={() => set("identifiers", ids.filter((_, j) => j !== i))} />
            </div>
          ))}
          {!readOnly && <AddButton onClick={() => set("identifiers", [...ids, { type: null, value: "" }])}>Add identifier</AddButton>}
          <p className={cn("text-[12px]", errors.identifiers ? "text-red-dark" : "text-fg-muted")}>{errors.identifiers ?? "dc:identifier · e.g. a catalogue number"}</p>
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[13px] font-bold leading-tight text-fg-strong">
          Other related links <span className="font-mono text-[11px] font-medium text-fg-muted">related</span>
        </span>
        {related.map((r, i) => (
          <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_28px] items-center gap-2">
            <Input aria-label="Link address" mono placeholder="https://…" value={r.id} readOnly={readOnly} onChange={(e) => set("related", related.map((x, j) => (j === i ? { ...x, id: e.target.value } : x)))} className="h-8 text-[12.5px]" />
            <Input aria-label="Link label" placeholder="label" value={r.label ?? ""} readOnly={readOnly} onChange={(e) => set("related", related.map((x, j) => (j === i ? { ...x, label: e.target.value || null } : x)))} className="h-8 text-[12.5px]" />
            <RemoveButton label="Remove link" disabled={readOnly} onClick={() => set("related", related.filter((_, j) => j !== i))} />
          </div>
        ))}
        {!readOnly && <AddButton onClick={() => set("related", [...related, { id: "", label: null }])}>Add link</AddButton>}
        {errors.related && <p className="text-[12px] text-red-dark">{errors.related}</p>}
      </div>
    </div>
  );
}

/** Label / value pairs shown in viewers, in this order. Edits the given language; other languages are kept. */
export function Pairs({ draft, set, errors, readOnly, lang }: FieldProps & { lang: string }) {
  const pairs: Pair[] = draft.metadata ?? [];
  const langOf = (m: Pair["label"]) => (m?.[lang] ? lang : Object.keys(m ?? {})[0] ?? lang);
  const edit = (i: number, part: "label" | "value", text: string) =>
    set(
      "metadata",
      pairs.map((p, j) => (j === i ? { ...p, [part]: withLang(p[part], langOf(p[part]), text) ?? {} } : p)),
    );
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= pairs.length) return;
    const next = [...pairs];
    [next[i], next[j]] = [next[j], next[i]];
    set("metadata", next);
  };
  return (
    <div className="flex flex-col gap-1.5">
      <SectionHead title="Label / value pairs" maps="metadata[] · shown in viewers, in this order" />
      {pairs.length === 0 && <p className="text-[13px] text-fg-muted">None yet. Viewers also list the date, duration, speakers and subjects on their own.</p>}
      {pairs.map((p, i) => (
        <div key={i} className="grid grid-cols-[22px_minmax(0,160px)_minmax(0,1fr)_28px] items-center gap-2">
          <span className="flex flex-col">
            <Tooltip content="Move up">
              <button type="button" aria-label="Move up" disabled={readOnly || i === 0} onClick={() => move(i, -1)} className="text-[10px] leading-none text-fg-muted hover:text-fg disabled:opacity-30">
                ▲
              </button>
            </Tooltip>
            <Tooltip content="Move down">
              <button type="button" aria-label="Move down" disabled={readOnly || i === pairs.length - 1} onClick={() => move(i, 1)} className="text-[10px] leading-none text-fg-muted hover:text-fg disabled:opacity-30">
                ▼
              </button>
            </Tooltip>
          </span>
          <Input aria-label="Label" value={inLang(p.label, langOf(p.label))} readOnly={readOnly} onChange={(e) => edit(i, "label", e.target.value)} className="h-[34px] text-[13px] font-semibold" />
          <Input aria-label="Value" value={inLang(p.value, langOf(p.value))} readOnly={readOnly} onChange={(e) => edit(i, "value", e.target.value)} className="h-[34px] text-[13px]" />
          <RemoveButton label="Remove pair" disabled={readOnly} onClick={() => set("metadata", pairs.filter((_, j) => j !== i))} />
        </div>
      ))}
      {!readOnly && <AddButton onClick={() => set("metadata", [...pairs, { label: {}, value: {} }])}>Add pair</AddButton>}
      {errors.metadata && <p className="text-[12px] text-red-dark">{errors.metadata}</p>}
    </div>
  );
}

