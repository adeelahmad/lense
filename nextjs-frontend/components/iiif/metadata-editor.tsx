"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, FileText, History as HistoryIcon, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Metadata } from "@/app/openapi-client";
import type { MetadataEdit } from "@/app/openapi-client/types.gen";
import { isUnreachable } from "@/components/errors/error-states";
import { DateLanguages, IdentifiersLinks, Pairs, People, ProviderEditor, RightsAttribution, SectionHead, Subjects, TitleSummary, type SetMeta } from "@/components/iiif/metadata-fields";
import {
  ACCESS,
  conflictingFields,
  describeEdit,
  dirtyFields,
  FIELD_LABEL,
  first,
  isEmpty,
  langGaps,
  langsOf,
  patchFor,
  profileProblems,
  PUBLISH_BADGE,
  publishState,
  same,
  validate,
  type Field,
  type LangMap,
  type Meta,
  type Problem,
} from "@/components/iiif/metadata-model";
import { keys, useMetaHistory, useNamespaceMeta, useRecordingBrief, useRecordingMeta, type RecordingMeta } from "@/components/iiif/queries";
import { rightsShort } from "@/components/iiif/rights";
import { ChoiceCards } from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog, Drawer } from "@/components/ui/dialog";
import { DateTime, EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Props = {
  recordingId: number;
  /** "panel": one column, for the Recording page's Metadata tab (MD2). "page": the full page with nav and preview (MD1). */
  variant?: "panel" | "page";
};

const SECTIONS: { id: string; label: string; fields: Field[] }[] = [
  { id: "md-title", label: "Title and summary", fields: ["label", "summary"] },
  {
    id: "md-date",
    label: "Date and languages",
    fields: ["navDate", "language"],
  },
  { id: "md-people", label: "Creators", fields: ["creators", "contributors"] },
  { id: "md-subjects", label: "Subjects", fields: ["subjects"] },
  {
    id: "md-rights",
    label: "Rights and attribution",
    fields: ["rights", "attribution"],
  },
  { id: "md-provider", label: "Provider", fields: ["provider"] },
  {
    id: "md-links",
    label: "Identifiers and links",
    fields: ["identifiers", "homepage", "related"],
  },
  { id: "md-pairs", label: "Label / value pairs", fields: ["metadata"] },
  { id: "md-access", label: "Access", fields: ["access"] },
];

export const FIELD_ANCHOR: Record<Field, string> = Object.fromEntries(SECTIONS.flatMap((s) => s.fields.map((f) => [f, s.id]))) as Record<Field, string>;

type Conflict = {
  fields: Field[];
  theirs: Meta;
  who: string;
  when: string | null;
};
type SaveArgs = {
  fields: Field[];
  values: Meta;
  resets: Set<Field>;
  force?: boolean;
};

/**
 * The descriptive metadata of one recording (IIIF Metadata MD1–MD2): language maps, people, subjects, rights, provider,
 * identifiers and pairs, with required fields, defaults and vocabularies from the namespace profile, history with
 * revert, and a conflict check when someone else saved while you were editing.
 */
export function MetadataEditor({ recordingId, variant = "panel" }: Props) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can, me } = useArchive();
  const meta = useRecordingMeta(recordingId);
  const history = useMetaHistory(recordingId);
  const rec = useRecordingBrief(recordingId);
  const ns = rec.data?.namespace ?? null;
  const nsMeta = useNamespaceMeta(ns);
  const profile = nsMeta.data?.profile ?? {};

  const [base, setBase] = useState<Meta | null>(null);
  const [draft, setDraft] = useState<Meta>({});
  const [baseEdit, setBaseEdit] = useState(0);
  const [resets, setResets] = useState<Set<Field>>(new Set());
  const [extraLangs, setExtraLangs] = useState<string[]>([]);
  const [lang, setLang] = useState<string>("");
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const loadedAt = useRef(0);
  const reload = useRef(false);

  const dirty = base ? [...new Set([...dirtyFields(base, draft), ...resets])] : [];

  // Take the server's copy when it first loads, and again when it changes while you have no unsaved edits
  // (or after a revert, which replaces what you had).
  useEffect(() => {
    if (!meta.data || meta.dataUpdatedAt === loadedAt.current) return;
    if (base && dirty.length && !reload.current) return;
    reload.current = false;
    loadedAt.current = meta.dataUpdatedAt;
    setBase(meta.data.meta);
    setDraft(meta.data.meta);
    setResets(new Set());
    setBaseEdit(history.data?.[0]?.id ?? 0);
  }, [meta.data, meta.dataUpdatedAt]);
  useEffect(() => {
    if (history.data && !baseEdit && !dirty.length) setBaseEdit(history.data[0]?.id ?? 0);
  }, [history.data]);

  const canEdit = can("editor", ns);
  const canPublish = can("owner", ns);
  const readOnly = !canEdit;
  const set: SetMeta = (field, value) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setResets((r) => {
      if (!r.has(field)) return r;
      const n = new Set(r);
      n.delete(field);
      return n;
    });
  };

  const errors = useMemo(() => validate(draft), [draft]);
  const required = (profile.required ?? []) as Field[];
  const problems: Problem[] = useMemo(
    () => [
      ...profileProblems(draft, profile, ns),
      ...Object.entries(errors).map(([f, m]) => ({
        field: f,
        message: m as string,
      })),
    ],
    [draft, profile, ns, errors],
  );
  const problemFields = new Set(problems.map((p) => p.field));
  const access = (draft.access ?? "private") as string;
  const savedState = publishState(base?.access ?? meta.data?.meta.access, (meta.data?.problems ?? []).length);
  const langs = langsOf(draft, extraLangs, Object.keys(draft.label ?? {})[0]);
  const currentLang = langs.includes(lang) ? lang : langs[0];
  const errorCount = Object.keys(errors).length;

  const fetchHistory = () =>
    qc.fetchQuery({
      queryKey: keys.history(recordingId),
      queryFn: () =>
        data(
          Metadata.listRecordingMetadataHistory({
            client,
            path: { rid: recordingId },
          }),
        ),
      staleTime: 0,
    });

  const save = useMutation({
    mutationFn: async ({ fields, values, resets: toReset, force }: SaveArgs) => {
      if (!force) {
        const newer = (await fetchHistory()).filter((e) => e.id > baseEdit);
        const clash = conflictingFields(fields, [...new Set(newer.flatMap((e) => e.changed))]);
        if (clash.length) {
          const fresh = (await data(
            Metadata.getRecordingMetadata({
              client,
              path: { rid: recordingId },
            }),
          )) as unknown as RecordingMeta;
          return {
            conflict: {
              fields: clash,
              theirs: fresh.meta,
              who: newer[0]?.by ?? "someone",
              when: newer[0]?.at ?? null,
            } satisfies Conflict,
          };
        }
      }
      const body = {
        set: patchFor(
          values,
          fields.filter((f) => !toReset.has(f)),
        ),
        reset: fields.filter((f) => toReset.has(f)),
      };
      return {
        saved: (await data(
          Metadata.updateRecordingMetadata({
            client,
            path: { rid: recordingId },
            body,
          }),
        )) as unknown as RecordingMeta,
      };
    },
    onSuccess: async (r) => {
      if ("conflict" in r && r.conflict) {
        setConflict(r.conflict);
        return;
      }
      if (!("saved" in r) || !r.saved) return;
      setSaveError(null);
      qc.setQueryData(keys.meta(recordingId), r.saved);
      const h = await fetchHistory();
      loadedAt.current = qc.getQueryState(keys.meta(recordingId))?.dataUpdatedAt ?? Date.now();
      setBase(r.saved.meta);
      setDraft(r.saved.meta);
      setResets(new Set());
      setBaseEdit(h[0]?.id ?? 0);
      void qc.invalidateQueries({ queryKey: keys.iiif(recordingId) });
      const left = r.saved.problems.length;
      toast({
        title: "Metadata saved",
        body: left ? `${left} problem${left === 1 ? "" : "s"} left` : "The Manifest and its records are updated.",
        tone: left ? "gate" : "green",
      });
    },
    onError: (e) => setSaveError(e.message),
  });
  const saveNow = (fields: Field[] = dirty, values: Meta = draft, force = false) => save.mutate({ fields, values, resets, force });

  const discard = () => {
    if (!base) return;
    setDraft(base);
    setResets(new Set());
    setSaveError(null);
  };

  if (meta.isError)
    return (
      <EmptyState
        tone="error"
        icon={<FileText />}
        title={isUnreachable(meta.error) ? "Can’t reach the server" : "Couldn’t load the metadata"}
        actions={<Button onClick={() => meta.refetch()}>Try again</Button>}
      >
        {meta.error.message}
      </EmptyState>
    );
  if (meta.isPending || !base || (rec.isPending && !rec.isError))
    return (
      <div className="flex flex-col gap-3 p-4" aria-busy="true" aria-label="Loading metadata">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-10 w-2/3" />
      </div>
    );

  const fieldProps = {
    draft,
    set,
    errors,
    readOnly,
    readOnlyReason: needRole("editor", ns),
  };
  const stored = meta.data.stored ?? {};
  const defaults = meta.data.defaults ?? {};
  const derivable = (f: Field) => !readOnly && stored[f] !== undefined && defaults[f] !== undefined && !same(stored[f], defaults[f]) && !resets.has(f);
  const applyDerived = (f: Field) => {
    setDraft((d) => ({ ...d, [f]: defaults[f] }));
    setResets((r) => new Set(r).add(f));
  };
  const derived = (...fs: Field[]) => {
    const list = fs.filter(derivable);
    return list.length ? (
      <div className="flex flex-wrap gap-3">
        {list.map((f) => (
          <Button key={f} variant="link" size="xs" className="text-[12px]" onClick={() => applyDerived(f)}>
            <RotateCcw className="!size-3" /> Use the derived {FIELD_LABEL[f].toLowerCase()}
          </Button>
        ))}
      </div>
    ) : null;
  };

  const sections: Record<string, ReactNode> = {
    "md-title": (
      <>
        <TitleSummary
          {...fieldProps}
          langs={langs}
          lang={currentLang}
          onLang={setLang}
          onAddLang={(l) => {
            setExtraLangs((x) => [...x, l]);
            setLang(l);
          }}
          onRemoveLang={(l) => {
            set("label", dropLang(draft.label, l));
            set("summary", dropLang(draft.summary, l));
            setExtraLangs((x) => x.filter((y) => y !== l));
            setLang(langs[0]);
          }}
          suggested={stored.summary === undefined && Boolean(defaults.summary) && same(draft.summary, defaults.summary)}
          required={{
            label: required.includes("label"),
            summary: required.includes("summary"),
          }}
        />
        {derived("label", "summary")}
      </>
    ),
    "md-date": (
      <>
        <DateLanguages {...fieldProps} fromTranscribe={stored.language === undefined && Boolean(defaults.language)} vocabulary={profile.vocabularies?.language} />
        {derived("navDate", "language")}
      </>
    ),
    "md-people": (
      <>
        <People {...fieldProps} speakers={rec.data?.speakers ?? []} />
        {derived("contributors")}
      </>
    ),
    "md-subjects": (
      <>
        <Subjects {...fieldProps} namespace={ns} vocabulary={profile.vocabularies?.subjects} />
        {derived("subjects")}
      </>
    ),
    "md-rights": <RightsAttribution {...fieldProps} lang={currentLang} attributionRequired={required.includes("attribution")} />,
    "md-provider": <ProviderEditor {...fieldProps} />,
    "md-links": <IdentifiersLinks {...fieldProps} />,
    "md-pairs": <Pairs {...fieldProps} lang={currentLang} />,
    "md-access": (
      <>
        <AccessEditor value={access} onChange={(v) => set("access", v as Meta["access"])} canPublish={canPublish} ns={ns} problems={problems.length} fromProfile={stored.access === undefined} />
        {derived("access")}
      </>
    ),
  };

  const sectionGlyph = (s: (typeof SECTIONS)[number]) => {
    if (s.fields.some((f) => problemFields.has(f))) return ["✕", "text-red-dark", "has problems"] as const;
    if (s.id === "md-title" && langGaps(draft).length) return ["◐", "text-gold-dark", "partly filled"] as const;
    if (s.fields.some((f) => !isEmpty(draft[f]))) return ["✓", "text-green-dark", "filled"] as const;
    return ["", "", "empty"] as const;
  };

  const lastEdit = history.data?.[0];
  const badge = PUBLISH_BADGE[savedState];
  const problemLine = problems.length ? (
    <span className="text-[12.5px] font-medium text-red-dark">
      ✕ {problems.length} problem{problems.length === 1 ? "" : "s"}
      {access === "private" ? " block publishing" : ""}
    </span>
  ) : (
    <span className="text-[12.5px] text-fg-muted">{required.length ? "all required ✓" : "no problems"}</span>
  );
  const saveDisabledReason = readOnly ? needRole("editor", ns) : !dirty.length ? "No changes to save" : errorCount ? "Fix the fields marked in red first" : undefined;

  const saveBar = (dirty.length > 0 || saveError) && (
    <div className={cn("sticky bottom-0 z-10 flex flex-wrap items-center gap-2.5 border-t border-border bg-background py-3", variant === "page" ? "px-6" : "px-4")}>
      <span aria-hidden className={cn("size-2 rounded-full", errorCount ? "bg-red" : "bg-blue")} />
      <span className="flex-1 text-[13px] font-medium" aria-live="polite">
        {dirty.length} change{dirty.length === 1 ? "" : "s"}
        {errorCount
          ? ` · fix ${errorCount === 1 ? `the ${FIELD_LABEL[Object.keys(errors)[0] as Field].toLowerCase()}` : `${errorCount} fields`} before saving`
          : problems.length
            ? " · drafts can be saved with problems"
            : ""}
      </span>
      {saveError && <span className="w-full text-[12.5px] text-red-dark sm:order-last">{saveError}</span>}
      <Button variant="ghost" size="sm" onClick={discard} disabled={save.isPending}>
        Discard
      </Button>
      <Button variant="primary" size="sm" disabled={Boolean(saveDisabledReason) || save.isPending} disabledReason={saveDisabledReason} onClick={() => saveNow()}>
        {save.isPending ? "Saving…" : "Save"}
      </Button>
    </div>
  );

  const historyList = (
    <HistoryList
      edits={history.data}
      loading={history.isPending}
      myEmail={me?.user.email}
      canRevert={canEdit}
      reason={needRole("editor", ns)}
      recordingId={recordingId}
      onReverted={() => {
        reload.current = true;
      }}
    />
  );
  const conflictDialog = (
    <ConflictDialog
      conflict={conflict}
      mine={draft}
      onClose={() => setConflict(null)}
      onDiscardMine={() => {
        reload.current = true;
        setConflict(null);
        void meta.refetch();
      }}
      onSave={(keepMine) => {
        if (!conflict) return;
        const theirs = conflict.fields.filter((f) => !keepMine.includes(f));
        const next: Meta = { ...draft };
        for (const f of theirs) (next as Record<string, unknown>)[f] = conflict.theirs[f];
        setDraft(next);
        setConflict(null);
        const fields = dirty.filter((f) => !theirs.includes(f));
        if (fields.length) saveNow(fields, next, true);
      }}
    />
  );

  if (variant === "panel")
    return (
      <div className="flex min-h-full flex-col">
        <div className="flex flex-col gap-5 px-4 py-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={badge.tone} dot>
              {badge.label}
            </Badge>
            <span className="min-w-0 flex-1 text-[12px] leading-tight text-fg-muted">
              {ns ? `Profile ${ns}` : "Profile"} ·{" "}
              {problems.length ? (
                <span className="text-red-dark">
                  {problems.length} problem{problems.length === 1 ? "" : "s"}
                </span>
              ) : required.length ? (
                "all required ✓"
              ) : (
                "no required fields"
              )}
            </span>
            <Link href={`/iiif/metadata/${recordingId}`} className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-fg-accent hover:underline">
              Full page <ExternalLink className="size-3" />
            </Link>
          </div>
          {readOnly && <Banner tone="info">You can read this metadata. Editors and owners of {ns ?? "this namespace"} can change it.</Banner>}
          {SECTIONS.map((s) => (
            <section key={s.id} aria-label={s.label} className="flex flex-col gap-2">
              {sections[s.id]}
            </section>
          ))}
          <section className="flex flex-col gap-1">
            <SectionHead title="History" />
            {historyList}
          </section>
        </div>
        {saveBar}
        {conflictDialog}
      </div>
    );

  return (
    <div className="flex flex-col rounded-md border border-border bg-background">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2.5 border-b border-border px-4 py-3.5 sm:px-6">
        <div className="flex min-w-0 flex-1 basis-full flex-col gap-1 sm:basis-auto">
          <span className="truncate text-[12px] font-medium text-fg-muted">
            <Link href={`/recordings/${recordingId}`} className="hover:underline">
              {rec.data?.title ?? `Recording ${recordingId}`}
            </Link>{" "}
            · Metadata
          </span>
          <span className="flex flex-wrap items-center gap-2">
            <h1 className="text-[20px] font-bold leading-[1.2] text-fg">Metadata</h1>
            <Badge tone={badge.tone} dot>
              {badge.label}
            </Badge>
            {problemLine}
          </span>
        </div>
        <span className="mr-auto text-[12px] leading-[1.3] text-fg-muted sm:mr-0 sm:text-right">
          Profile: <b className="text-fg-strong">{ns ?? "—"}</b>
          {required.length ? ` · ${required.length} required` : ""}
          <br />
          {lastEdit ? (
            <>
              Last saved by {lastEdit.by === me?.user.email ? "you" : (lastEdit.by ?? "someone")} · {relative(lastEdit.at)}
            </>
          ) : (
            "Not edited yet: values come from the recording"
          )}
        </span>
        <Button variant="ghost" size="sm" icon={<HistoryIcon />} onClick={() => setShowHistory(true)}>
          History
        </Button>
        <Button variant="primary" size="sm" disabled={Boolean(saveDisabledReason) || save.isPending} disabledReason={saveDisabledReason} onClick={() => saveNow()}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </header>
      <div className="grid min-h-0 lg:grid-cols-[200px_minmax(0,1fr)_300px]">
        <nav aria-label="Metadata sections" className="hidden border-r border-border bg-surface px-2.5 py-3.5 lg:block">
          <div className="flex flex-col gap-px lg:sticky lg:top-[78px]">
            {SECTIONS.map((s) => {
              const [g, c, what] = sectionGlyph(s);
              return (
                <a key={s.id} href={`#${s.id}`} className="flex h-8 items-center justify-between rounded-sm px-2.5 text-[13px] font-medium text-fg-strong hover:bg-surface-neutral">
                  {s.label}
                  {g && (
                    <span className={cn("text-[11px] font-bold", c)} aria-label={what}>
                      {g}
                    </span>
                  )}
                </a>
              );
            })}
          </div>
        </nav>
        <div className="flex min-w-0 flex-col gap-[18px] px-4 py-[18px] sm:px-6">
          {readOnly && <Banner tone="info">You can read this metadata. Editors and owners of {ns ?? "this namespace"} can change it.</Banner>}
          {SECTIONS.map((s) => (
            <section key={s.id} id={s.id} aria-label={s.label} className="flex scroll-mt-20 flex-col gap-2">
              {sections[s.id]}
            </section>
          ))}
        </div>
        <aside className="border-t border-border bg-surface px-[18px] py-4 lg:border-l lg:border-t-0">
          <div className="flex flex-col gap-3 lg:sticky lg:top-[78px]">
            <b className="text-[13px] font-bold">How a IIIF viewer shows it</b>
            <ViewerPreview draft={draft} attributionRequired={required.includes("attribution")} />
            <b className="mt-1 text-[13px] font-bold">Machine-readable record</b>
            <RecordLinks recordingId={recordingId} />
            <span className="text-[12px] leading-[1.4] text-fg-muted">Linked from the Manifest’s seeAlso and updated on save. EBUCore and PBCore aren’t produced yet.</span>
          </div>
        </aside>
      </div>
      {saveBar}
      <Drawer open={showHistory} onOpenChange={setShowHistory} title="History">
        <div className="px-4 py-2">{historyList}</div>
      </Drawer>
      {conflictDialog}
    </div>
  );
}

function dropLang(map: LangMap | null | undefined, lang: string): LangMap | null {
  const out = { ...(map ?? {}) };
  delete out[lang];
  return Object.keys(out).length ? out : null;
}

/** Access: public · transcript open · signed-in · private. Publishing is for owners, and needs no open problems. */
function AccessEditor({
  value,
  onChange,
  canPublish,
  ns,
  problems,
  fromProfile,
}: {
  value: string;
  onChange: (v: string) => void;
  canPublish: boolean;
  ns: string | null;
  problems: number;
  fromProfile: boolean;
}) {
  const current = ACCESS.find((a) => a.value === value) ?? ACCESS[3];
  return (
    <div className="flex flex-col gap-2">
      <SectionHead title="Access" maps="what IIIF publishes" />
      <ChoiceCards
        label="Access"
        size="sm"
        columns={4}
        className="max-sm:!grid-cols-2"
        value={value}
        onChange={onChange}
        disabled={!canPublish}
        disabledReason={needRole("owner", ns)}
        options={ACCESS.map((a) => ({
          value: a.value,
          label: a.label,
          hint: a.hint,
          disabled: a.value !== "private" && problems > 0 && value === "private",
          reason: `Fix ${problems} problem${problems === 1 ? "" : "s"} before publishing`,
        }))}
      />
      <p className="text-[12px] leading-[1.4] text-fg-secondary">
        {current.anon}
        {fromProfile ? " This comes from the namespace’s default access." : ""}
      </p>
    </div>
  );
}

/** What a IIIF viewer would show for the draft (MD1's right column). */
export function ViewerPreview({ draft, attributionRequired }: { draft: Meta; attributionRequired: boolean }) {
  const pairs = draft.metadata ?? [];
  const attribution = first(draft.attribution);
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-border bg-background p-3.5 text-[12.5px] leading-[1.45]">
      <b className="text-[14px] font-bold leading-[1.3]">{first(draft.label) || "Untitled"}</b>
      {first(draft.summary) && <span className="line-clamp-3 text-fg-secondary">{first(draft.summary)}</span>}
      {pairs.map((p, i) => (
        <span key={i}>
          <b className="font-semibold text-fg-secondary">{first(p.label)}</b>
          <br />
          {first(p.value)}
        </span>
      ))}
      {draft.navDate && (
        <span>
          <b className="font-semibold text-fg-secondary">Date</b>
          <br />
          {draft.navDate.slice(0, 10)}
        </span>
      )}
      <span>
        <b className="font-semibold text-fg-secondary">Rights</b>
        <br />
        {draft.rights ? rightsShort(draft.rights) : "None"}
      </span>
      {attribution ? (
        <span>
          <b className="font-semibold text-fg-secondary">Attribution</b>
          <br />
          {attribution}
        </span>
      ) : attributionRequired ? (
        <span className="rounded-sm bg-red-surface p-2 text-red-dark">Attribution: missing</span>
      ) : null}
      {draft.provider?.name && (
        <span className="flex items-center gap-2 border-t border-border pt-2">
          {draft.provider.logo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={draft.provider.logo} alt="" className="size-7 rounded-xs object-contain" />
          ) : (
            <span aria-hidden className="grid size-7 place-items-center rounded-xs border border-dashed border-border text-[9px] font-semibold text-fg-muted">
              logo
            </span>
          )}
          {draft.provider.name}
        </span>
      )}
    </div>
  );
}

const RECORDS = [
  { path: "dc.xml", label: "Dublin Core XML" },
  { path: "record.json", label: "schema.org JSON-LD" },
  { path: "manifest", label: "IIIF Manifest" },
];

/** The records linked from the Manifest's seeAlso, fetched with your session (private recordings need it). */
function RecordLinks({ recordingId }: { recordingId: number }) {
  const client = useApiClient();
  const [open, setOpen] = useState<(typeof RECORDS)[number] | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const show = async (r: (typeof RECORDS)[number]) => {
    setOpen(r);
    setText(null);
    setErr(null);
    try {
      const auth = (client.getConfig().headers as Headers | Record<string, string> | undefined) ?? {};
      const h = auth instanceof Headers ? auth.get("Authorization") : (auth as Record<string, string>).Authorization;
      const res = await fetch(`/iiif/${recordingId}/${r.path}`, {
        headers: h ? { Authorization: h } : {},
      });
      if (!res.ok) throw new Error(`The server answered ${res.status}`);
      const body = await res.text();
      setText(r.path.endsWith("xml") ? body : JSON.stringify(JSON.parse(body), null, 2));
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <>
      <div className="flex flex-wrap gap-1.5">
        {RECORDS.map((r) => (
          <button key={r.path} type="button" onClick={() => void show(r)} className="h-6 rounded-xs border border-border bg-background px-2 font-mono text-[11.5px] hover:bg-surface-neutral">
            {r.label}
          </button>
        ))}
      </div>
      <Dialog open={Boolean(open)} onOpenChange={(o) => !o && setOpen(null)} title={open?.label ?? ""} wide>
        {err ? (
          <Banner tone="error">{err}</Banner>
        ) : text == null ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <pre className="max-h-[60vh] overflow-auto rounded-sm border border-border bg-surface p-3 font-mono text-[11.5px] leading-[1.55] text-fg-strong">{text}</pre>
        )}
      </Dialog>
    </>
  );
}

/** The record's history, newest first; each change can be reverted (MD2). */
function HistoryList({
  edits,
  loading,
  myEmail,
  canRevert,
  reason,
  recordingId,
  onReverted,
}: {
  edits: MetadataEdit[] | undefined;
  loading: boolean;
  myEmail?: string;
  canRevert: boolean;
  reason: string;
  recordingId: number;
  onReverted: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const revert = useMutation({
    mutationFn: (e: MetadataEdit) => data(Metadata.revertMetadataEdit({ client, path: { eid: e.id } })),
    onSuccess: () => {
      onReverted();
      void qc.invalidateQueries({ queryKey: keys.meta(recordingId) });
      void qc.invalidateQueries({ queryKey: keys.history(recordingId) });
      void qc.invalidateQueries({ queryKey: keys.iiif(recordingId) });
      toast({
        title: "Change reverted",
        body: "The revert is in the history too, so it can be undone.",
        tone: "green",
      });
    },
    onError: (e) => toast({ title: "Couldn’t revert", body: e.message, tone: "red" }),
  });
  if (loading) return <Skeleton className="h-16 w-full" />;
  if (!edits?.length) return <p className="py-2 text-[13px] text-fg-muted">No edits yet. Every save is kept here and can be reverted.</p>;
  return (
    <ul className="flex flex-col">
      {edits.map((e) => (
        <li key={e.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2.5 gap-y-1 border-t border-border py-2.5 first:border-t-0">
          <span className="text-[13px] font-semibold leading-[1.35]">{describeEdit(e as never)}</span>
          <Button variant="link" size="xs" className="text-[12.5px]" disabled={!canRevert || revert.isPending} disabledReason={!canRevert ? reason : undefined} onClick={() => revert.mutate(e)}>
            Revert
          </Button>
          <span className="text-[12px] leading-[1.3] text-fg-muted">
            {e.by === myEmail ? "You" : (e.by ?? "Someone")} · <DateTime iso={e.at} />
          </span>
        </li>
      ))}
    </ul>
  );
}

function summarize(field: Field, v: unknown): string {
  if (isEmpty(v)) return "(empty)";
  if (field === "rights") return rightsShort(v as string);
  if (field === "label" || field === "summary" || field === "attribution") return first(v as LangMap);
  if (Array.isArray(v)) return v.map((x) => (typeof x === "string" ? x : (x.name ?? x.label ?? x.value ?? first(x.label)))).join(", ");
  if (typeof v === "object") return (v as { name?: string }).name ?? JSON.stringify(v);
  return String(v);
}

/** "Lena changed this record": choose, per field, whose value to keep (MD2). */
function ConflictDialog({
  conflict,
  mine,
  onClose,
  onDiscardMine,
  onSave,
}: {
  conflict: Conflict | null;
  mine: Meta;
  onClose: () => void;
  onDiscardMine: () => void;
  onSave: (keepMine: Field[]) => void;
}) {
  const [keep, setKeep] = useState<Record<string, "mine" | "theirs">>({});
  useEffect(() => setKeep({}), [conflict]);
  if (!conflict) return null;
  const local = conflict.who.split("@")[0];
  const who = local.charAt(0).toUpperCase() + local.slice(1);
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${who} changed this record`}
      description={`While you were editing, ${conflict.who} saved changes to ${conflict.fields.length} field${conflict.fields.length === 1 ? "" : "s"}${conflict.when ? ` (${relative(conflict.when)})` : ""}. Choose which to keep for each field.`}
      actions={
        <>
          <Button variant="ghost" onClick={onDiscardMine}>
            Discard mine
          </Button>
          <Button variant="primary" onClick={() => onSave(conflict.fields.filter((f) => (keep[f] ?? "mine") === "mine"))}>
            Save my choices
          </Button>
        </>
      }
    >
      {conflict.fields.map((f) => {
        const choice = keep[f] ?? "mine";
        return (
          <div key={f} className="flex flex-col gap-1.5 rounded-md border border-border p-3">
            <b className="text-[13px] font-bold">{FIELD_LABEL[f]}</b>
            <ChoiceCards
              label={`Keep which ${FIELD_LABEL[f].toLowerCase()}`}
              size="sm"
              value={choice}
              onChange={(v) => setKeep((k) => ({ ...k, [f]: v as "mine" | "theirs" }))}
              options={[
                { value: "mine", label: "YOURS", hint: summarize(f, mine[f]) },
                {
                  value: "theirs",
                  label: `${who.toUpperCase()}’S`,
                  hint: summarize(f, conflict.theirs[f]),
                },
              ]}
            />
          </div>
        );
      })}
    </Dialog>
  );
}
