"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Batches, Collections, Entities, Pipelines, Templates } from "@/app/openapi-client";
import type { BatchEstimate, BatchRun, BatchSelection, Estimate } from "@/app/openapi-client/types.gen";
import { planFromParams, rememberCombine } from "@/components/batches/plan";
import {
  approxCount,
  approxDuration,
  confirmMatches,
  confirmNumber,
  describeSelection,
  MEDIA_STEPS,
  money,
  nothingRunnable,
} from "@/components/batches/format";
import { useSelectionRecordings } from "@/components/batches/selection";
import { useSpeakerDirectory } from "@/components/search/data";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, SearchInput } from "@/components/ui/field";
import { STEP_LABEL } from "@/components/ui/loop";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count, plural, tc } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Kind = "template" | "pipeline" | "steps";
const STEP_ORDER = [
  "transcribe",
  "diarize",
  "shots",
  "ocr",
  "faces",
  "objects",
  "describe",
  "analyze",
  "summarize",
  "report",
];

/** "Run on…": pick the work — a pipeline, one template, or reprocess steps. */
function WorkPicker({
  run,
  onChange,
  onNext,
  busy,
}: {
  run: BatchRun;
  onChange: (r: BatchRun) => void;
  onNext: () => void;
  busy: boolean;
}) {
  const client = useApiClient();
  const [kind, setKind] = useState<Kind>(run.pipeline ? "pipeline" : run.steps?.length ? "steps" : "template");
  const templates = useQuery({
    queryKey: ["templates"],
    queryFn: () => data(Templates.listTemplates({ client })),
    staleTime: 60_000,
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 60_000,
  });
  const stepTypes = (pipelines.data?.step_types ?? STEP_ORDER)
    .filter((s) => STEP_ORDER.includes(s))
    .sort((a, b) => STEP_ORDER.indexOf(a) - STEP_ORDER.indexOf(b));
  const steps = (run.steps ?? []).map(String);
  const chosen =
    kind === "template" ? run.template != null : kind === "pipeline" ? run.pipeline != null : steps.length > 0;
  const radio = "flex w-full items-start gap-3 rounded-md border px-3.5 py-3 text-left transition-colors duration-fast";
  return (
    <div className="flex flex-col gap-4">
      <Segmented
        value={kind}
        onChange={(v) => {
          setKind(v as Kind);
          onChange({});
        }}
        items={[
          { value: "template", label: "A template" },
          { value: "pipeline", label: "A pipeline" },
          { value: "steps", label: "Reprocess steps" },
        ]}
        className="self-start"
      />
      {kind === "template" && (
        <div role="radiogroup" aria-label="Template" className="flex flex-col gap-2">
          {templates.isLoading && <Skeleton className="h-14 w-full rounded-md" />}
          {templates.isError && <Banner tone="error">{templates.error.message}</Banner>}
          {templates.data?.length === 0 && (
            <p className="m-0 text-[13.5px] text-fg-secondary">
              No templates yet. Admins add them under Pipelines → Templates.
            </p>
          )}
          {(templates.data ?? []).map((t) => (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={run.template === t.id}
              onClick={() => onChange({ template: t.id })}
              className={cn(
                radio,
                run.template === t.id ? "border-blue bg-blue-surface" : "border-border hover:bg-surface",
              )}
            >
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="text-[14px] font-bold text-fg">{t.name}</span>
                {t.description && <span className="text-[12.5px] text-fg-secondary">{t.description}</span>}
              </span>
              <span className="rounded-pill bg-surface-neutral px-2 py-0.5 text-[11px] font-bold uppercase tracking-[.04em] text-fg-secondary">
                {t.kind}
              </span>
            </button>
          ))}
          {run.template != null && templates.data?.find((t) => t.id === run.template)?.kind === "prompt" && (
            <Field
              label="Output name"
              optional
              hint="Results are saved on each recording under this name; the same name replaces earlier results."
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  value={run.key ?? ""}
                  onChange={(e) => onChange({ ...run, key: e.target.value || undefined })}
                  placeholder="From the template name"
                  mono
                  className="max-w-[320px]"
                />
              )}
            </Field>
          )}
        </div>
      )}
      {kind === "pipeline" && (
        <div role="radiogroup" aria-label="Pipeline" className="flex flex-col gap-2">
          {pipelines.isLoading && <Skeleton className="h-14 w-full rounded-md" />}
          {pipelines.data?.pipelines.length === 0 && (
            <p className="m-0 text-[13.5px] text-fg-secondary">No saved pipelines. Pick reprocess steps instead.</p>
          )}
          {(pipelines.data?.pipelines ?? []).map((p) => (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={run.pipeline === p.id}
              onClick={() => onChange({ pipeline: p.id })}
              className={cn(
                radio,
                run.pipeline === p.id ? "border-blue bg-blue-surface" : "border-border hover:bg-surface",
              )}
            >
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="text-[14px] font-bold text-fg">{p.name}</span>
                <span className="text-[12.5px] text-fg-secondary">
                  {((p.steps ?? []) as { type?: string }[]).map((s) => STEP_LABEL[s.type ?? ""] ?? s.type).join(" → ")}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
      {kind === "steps" && (
        <fieldset className="m-0 flex flex-col gap-2.5 border-0 p-0">
          <legend className="mb-2 text-[13px] font-bold text-fg-strong">Steps to run again</legend>
          {stepTypes.map((s) => (
            <Checkbox
              key={s}
              checked={steps.includes(s)}
              onCheckedChange={(on) =>
                onChange({
                  steps: STEP_ORDER.filter((x) => (x === s ? on : steps.includes(x))),
                })
              }
              label={
                <span>
                  {STEP_LABEL[s] ?? s}
                  {MEDIA_STEPS.has(s) && (
                    <span className="ml-1.5 text-[12.5px] text-fg-muted">needs audio or video</span>
                  )}
                </span>
              }
            />
          ))}
        </fieldset>
      )}
      <div className="flex justify-end">
        <Button
          variant="primary"
          disabled={!chosen || busy}
          disabledReason={chosen ? undefined : "Pick what to run first"}
          onClick={onNext}
        >
          {busy ? "Estimating…" : "Double-check"}
        </Button>
      </div>
    </div>
  );
}

function Tile({ v, k }: { v: string; k: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-md bg-surface px-3 py-3">
      <b className="tabular text-[18px] font-bold leading-none text-fg">{v}</b>
      <span className="text-[12px] text-fg-secondary">{k}</span>
    </div>
  );
}

/** The names behind a selection, for the "Started from" line. */
function useSelectionNames(sel: BatchSelection, hint: string | null | undefined) {
  const client = useApiClient();
  const dir = useSpeakerDirectory(Boolean(sel.speaker));
  const col = useQuery({
    queryKey: ["collection", sel.collection],
    queryFn: () =>
      data(
        Collections.getCollection({
          client,
          path: { cid: sel.collection as number },
        }),
      ),
    enabled: Boolean(sel.collection),
  });
  const ent = useQuery({
    queryKey: ["entity", sel.entity],
    queryFn: () => data(Entities.getEntity({ client, path: { eid: sel.entity as number } })),
    enabled: Boolean(sel.entity) && !hint,
  });
  return {
    collection: col.data?.name,
    entity: hint ?? ent.data?.name,
    speaker: dir.speakers.find((s) => s.id === sel.speaker)?.display,
  };
}

/** BA2: what a run will do before it starts — size, time, tokens, cost, what's replaced or skipped, and the list. */
function DoubleCheck({
  plan,
  est,
  onBack,
  onStarted,
}: {
  plan: { selection: BatchSelection; run: BatchRun; label?: string | null };
  est: BatchEstimate;
  onBack: () => void;
  onStarted: (id: number) => void;
}) {
  const client = useApiClient();
  const { roleIn } = useArchive();
  const names = useSelectionNames(plan.selection, plan.label);
  const list = useSelectionRecordings(plan.selection);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState("");
  const [typed, setTyped] = useState("");
  const [needs, setNeeds] = useState<Estimate | null>(est.needs_confirmation ? est : null);
  const editable = (id: number) => {
    const ns = list.byId.get(id)?.namespace;
    const r = ns ? roleIn(ns) : undefined;
    return r === "editor" || r === "owner";
  };
  const ids = list.ids ?? [];
  const runnable = ids.filter((id) => editable(id) && !excluded.has(id));
  const exact = list.ids != null && list.complete;
  const n = excluded.size && exact ? runnable.length : est.recordings;
  const keys = est.steps.filter((s) => s.type === "llm").map((s) => String(s.key ?? ""));
  const media = est.steps.map((s) => String(s.type ?? "")).filter((t) => MEDIA_STEPS.has(t));
  const transcripts = est.by_kind?.transcript ?? 0;
  const tokens = (est.llm?.input_tokens ?? 0) + (est.llm?.output_tokens ?? 0);
  const selection: BatchSelection = excluded.size && exact ? { recordings: runnable } : plan.selection;
  const nsNames = [...new Set(ids.map((id) => list.byId.get(id)?.namespace).filter(Boolean))] as string[];

  const create = useMutation({
    mutationFn: (opts: { sample?: number; confirm?: string }) =>
      data(
        Batches.createBatch({
          client,
          body: { selection, run: plan.run, ...opts },
        }),
      ),
    onSuccess: (r) => onStarted(r.id),
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        const body = e.body as { estimate?: Estimate } | undefined;
        setNeeds(body?.estimate ?? est);
      }
    },
  });

  if (!est.recordings || nothingRunnable(est, est.steps)) {
    const total = est.recordings + (est.skipped ?? 0);
    const allTranscripts = transcripts > 0 && transcripts === est.recordings && media.length === est.steps.length;
    return (
      <div className="flex flex-col gap-4">
        <span className="label-caps">Nothing to run</span>
        <h1 className="text-[22px] font-bold leading-tight text-fg">
          None of {total === 1 ? "this recording" : `these ${count(total)} recordings`} can run {est.label}
        </h1>
        <p className="m-0 text-[14px] leading-normal text-fg-secondary">
          {allTranscripts
            ? `${total === 1 ? "It is" : `All ${count(total)} are`} transcript-only, so there’s no audio to work on. Attach audio first, or pick steps that work on text (Analyze, Summarize, Report).`
            : est.skipped
              ? `You can only view ${est.skipped === 1 ? "it" : "them"}: running needs editor access to the namespace.`
              : "No recordings match this selection any more."}
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => history.back()}>
            Close
          </Button>
          <Button variant="secondary" onClick={onBack}>
            Choose other steps
          </Button>
        </div>
      </div>
    );
  }

  const shown = ids.filter(
    (id) => !filter.trim() || (list.byId.get(id)?.title ?? "").toLowerCase().includes(filter.trim().toLowerCase()),
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        {needs && <span className="label-caps">Over the limit · {plural(n, "recording")}</span>}
        <h1 className="text-[22px] font-bold leading-tight text-fg">
          Run “{est.label}” on {plural(n, "recording")}?
        </h1>
        <p className="m-0 text-[13.5px] text-fg-secondary">
          Started from: <b className="font-semibold text-fg">{describeSelection(plan.selection, names)}</b>
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <Tile v={count(n)} k="will run" />
        <Tile v={approxDuration(est.seconds)} k="estimated time" />
        <Tile
          v={tokens ? approxCount(tokens) : "—"}
          k={tokens ? `tokens${est.llm?.model ? ` · ${est.llm.model}` : ""}` : "no model calls"}
        />
        <Tile
          v={est.llm?.cost != null ? `~${money(est.llm.cost)}` : "—"}
          k={est.llm?.cost != null ? "at Settings prices" : tokens ? "set prices in Settings → AI" : "no model cost"}
        />
      </div>

      {(est.would_replace > 0 || (est.skipped ?? 0) > 0 || transcripts > 0) && (
        <section aria-labelledby="replaced" className="flex flex-col gap-2">
          <h2 id="replaced" className="text-[14px] font-bold text-fg">
            What gets replaced or skipped
          </h2>
          {est.would_replace > 0 && (
            <div className="flex flex-wrap items-center gap-3 text-[13.5px] text-fg">
              <span className="flex-1">
                {plural(est.would_replace, "recording")} already {est.would_replace === 1 ? "has" : "have"} an output
                named <code className="font-mono text-[12.5px]">{keys.join(", ")}</code>
              </span>
              <span className="flex items-center gap-1.5">
                <Button
                  size="xs"
                  variant="ghost"
                  className="border-border"
                  disabled
                  disabledReason="Not available yet: a run replaces existing outputs"
                >
                  Keep theirs
                </Button>
                <span className="inline-flex h-7 items-center rounded-pill bg-surface-neutral px-3 text-[12.5px] font-bold text-fg">
                  Overwrite
                </span>
              </span>
            </div>
          )}
          <p className="m-0 text-[13px] leading-snug text-fg-secondary">
            {(est.skipped ?? 0) > 0 && (
              <>
                <span aria-hidden className="text-gold-dark">
                  ◆{" "}
                </span>
                {plural(est.skipped ?? 0, "recording")} you can only view {(est.skipped ?? 0) === 1 ? "is" : "are"}{" "}
                skipped
              </>
            )}
            {(est.skipped ?? 0) > 0 && transcripts > 0 && " · "}
            {transcripts > 0 &&
              (media.length
                ? `${plural(transcripts, "transcript-only recording")} can’t run ${media.map((m) => STEP_LABEL[m] ?? m).join(", ")} (no audio)`
                : "transcript-only recordings run (this doesn’t need audio)")}
          </p>
        </section>
      )}

      {list.isLoading && <Skeleton className="h-32 w-full rounded-md" />}
      {list.ids && list.ids.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface px-3 py-2">
            <span className="flex-1 text-[12px] font-semibold text-fg-secondary">
              {plural(ids.length, "recording")}
              {nsNames.length ? ` in ${nsNames.join(", ")}` : ""} · {count(n)} will run
              {!list.complete ? " · list shows the first ones" : ""}
            </span>
            <SearchInput
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search list"
              aria-label="Search the list"
              className="w-44 [&_input]:h-8"
            />
          </div>
          <ul className="m-0 max-h-[320px] list-none overflow-y-auto p-0">
            {shown.map((id) => {
              const r = list.byId.get(id);
              const can = editable(id);
              const out = excluded.has(id);
              return (
                <li
                  key={id}
                  className="grid grid-cols-[auto_minmax(0,1fr)_64px_minmax(0,150px)] items-center gap-3 border-b border-border px-3 py-2 last:border-b-0"
                >
                  <Checkbox
                    aria-label={`${out || !can ? "Include" : "Exclude"} ${r?.title ?? `recording ${id}`}`}
                    checked={can && !out}
                    disabled={!can || !exact}
                    onCheckedChange={(on) =>
                      setExcluded((s) => {
                        const x = new Set(s);
                        if (on) x.delete(id);
                        else x.add(id);
                        return x;
                      })
                    }
                  />
                  <span
                    className={cn("truncate text-[13.5px] font-semibold", can && !out ? "text-fg" : "text-fg-muted")}
                  >
                    {r?.title ?? `Recording ${id}`}
                    {!can && r?.namespace && <span className="font-normal"> (you’re a viewer)</span>}
                  </span>
                  <span className="tabular text-[13px] text-fg-secondary">
                    {r?.duration_ms ? tc(r.duration_ms) : ""}
                  </span>
                  <span className="truncate text-[12.5px] text-fg-muted">
                    {!can ? "skipped · no edit rights" : out ? "excluded by you" : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {needs && (
        <div className="flex flex-col gap-3">
          <Banner tone="warning">
            This is over the limits in Settings → AI
            {needs.llm?.cost != null ? ` (about ${money(needs.llm.cost)})` : ""}. Type the confirmation to run it all,
            or try it on 3 first.
          </Banner>
          <Field label={`Type ${needs.confirm_text ?? `RUN ${confirmNumber(needs.confirm_text)}`} to confirm`}>
            {({ id }) => (
              <Input
                id={id}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                className="max-w-[320px]"
              />
            )}
          </Field>
        </div>
      )}
      {create.isError && !(create.error instanceof ApiError && create.error.status === 409) && (
        <Banner tone="error">{create.error.message}</Banner>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
        <Button variant="ghost" icon={<ArrowLeft />} onClick={onBack}>
          Back
        </Button>
        <Button
          variant="secondary"
          disabled={create.isPending || n <= 3}
          disabledReason={n <= 3 ? "There are 3 or fewer to run" : undefined}
          onClick={() => create.mutate({ sample: 3 })}
        >
          Try on 3 first
        </Button>
        <Button
          variant="primary"
          disabled={create.isPending || (needs != null && !confirmMatches(typed, needs.confirm_text))}
          disabledReason={
            needs != null && !confirmMatches(typed, needs.confirm_text) ? `Type ${needs.confirm_text} first` : undefined
          }
          onClick={() => create.mutate(needs ? { confirm: needs.confirm_text ?? undefined } : {})}
        >
          {create.isPending ? "Starting…" : `Run all ${count(n)}`}
        </Button>
      </div>
    </div>
  );
}

/** BA1–BA2: "Run on…" — choose the work, double-check, then start (optionally on a sample of 3). */
export function RunOnPage() {
  const params = useSearchParams();
  const router = useRouter();
  const client = useApiClient();
  const { can, namespace: topNs } = useArchive();
  const initial = useMemo(() => planFromParams(new URLSearchParams(params.toString())), [params]);
  const [run, setRun] = useState<BatchRun>(initial.run);
  const [est, setEst] = useState<BatchEstimate | null>(null);
  const combine = params.get("combine");
  const plan = { selection: initial.selection, run, label: initial.label };
  const empty = !Object.keys(initial.selection).length;
  const selNs =
    initial.selection.namespace ??
    (initial.selection.filter as { namespaces?: string[] } | null)?.namespaces?.[0] ??
    null;

  const estimate = useMutation({
    mutationFn: () =>
      data(
        Batches.estimateBatch({
          client,
          body: { selection: plan.selection, run },
        }),
      ),
    onSuccess: setEst,
  });
  useEffect(() => {
    if ((initial.run.template || initial.run.pipeline || initial.run.steps?.length) && !empty) estimate.mutate();
  }, []); // once, when a link already names the work

  if (empty)
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState
          title="Choose recordings first"
          actions={
            <Button
              variant="secondary"
              onClick={() => router.push(topNs ? `/batches/new?ns=${encodeURIComponent(topNs)}` : "/library")}
            >
              {topNs ? `Everything in ${topNs}` : "Go to the Library"}
            </Button>
          }
        >
          “Run on…” starts from a Library selection, a namespace, a speaker, an entity in the graph, search results or a
          saved collection.
        </EmptyState>
      </div>
    );
  if (!can("editor", selNs))
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState title="You can’t run things here">
          {needRole("editor", selNs)}. Viewers can read, listen, search and chat.
        </EmptyState>
      </div>
    );

  return (
    <div className="flex justify-center px-4 py-6 md:px-6">
      <div className="flex w-full max-w-[808px] flex-col gap-5 rounded-xl border border-border bg-background p-5 shadow-2 md:p-6">
        {!est ? (
          <>
            <div className="flex flex-col gap-1">
              <h1 className="text-[22px] font-bold leading-tight text-fg">Run on…</h1>
              <p className="m-0 text-[13.5px] text-fg-secondary">
                Pick the work. Next you’ll see how many recordings it runs on, how long it may take and what it costs,
                before anything starts.
              </p>
            </div>
            {estimate.isError && <Banner tone="error">{estimate.error.message}</Banner>}
            <WorkPicker run={run} onChange={setRun} onNext={() => estimate.mutate()} busy={estimate.isPending} />
          </>
        ) : (
          <DoubleCheck
            plan={plan}
            est={est}
            onBack={() => setEst(null)}
            onStarted={(id) => {
              if (combine != null) rememberCombine(id, combine);
              router.push(`/batches/${id}${combine != null ? "?report=1" : ""}`);
            }}
          />
        )}
      </div>
    </div>
  );
}
