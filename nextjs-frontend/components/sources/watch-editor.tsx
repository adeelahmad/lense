"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { Pipelines, Sources } from "@/app/openapi-client";
import type { Watch } from "@/app/openapi-client/types.gen";
import { splitPatterns } from "@/components/sources/source-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Switch } from "@/components/ui/field";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

const WATCH_STEPS = ["transcribe", "diarize", "shots", "ocr", "faces", "analyze", "summarize", "report"];
const PICK = [
  { value: "audio", label: "Audio" },
  { value: "transcripts", label: "Transcripts" },
  { value: "both", label: "Both" },
];

type Run = "default" | "pipeline" | "steps";
type Form = {
  namespace: string;
  kinds: "audio" | "transcripts" | "both";
  poll: string;
  stable: string;
  include: string;
  exclude: string;
  run: Run;
  pipeline: string;
  steps: string[];
  backfill: boolean;
  enabled: boolean;
};

function initial(watch: Watch | null | undefined, ns: string): Form {
  const w = watch as (Watch & { pipeline?: number | null }) | null | undefined;
  return {
    namespace: w?.namespace ?? ns,
    kinds: (w?.kinds as Form["kinds"]) ?? "both",
    poll: String(w?.poll_minutes ?? 5),
    stable: String(w?.stable_seconds ?? 30),
    include: (w?.include ?? []).join(", "),
    exclude: (w?.exclude ?? []).join(", "),
    run: w?.steps?.length ? "steps" : w?.pipeline ? "pipeline" : "default",
    pipeline: w?.pipeline ? String(w.pipeline) : "",
    steps: w?.steps?.length ? w.steps : ["transcribe", "diarize", "analyze", "summarize"],
    backfill: Boolean(w?.backfill),
    enabled: w?.enabled ?? true,
  };
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** SO3: watch a folder (or change a watched folder): where files go, what to pick up, how often, what to run. */
export function WatchEditor({
  open,
  onOpenChange,
  source,
  path,
  watch,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: { id: number; name: string };
  path: string;
  watch?: Watch | null;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { namespaces, namespace } = useArchive();
  const editing = Boolean(watch);
  const [f, setF] = useState<Form>(() => initial(watch, namespace ?? namespaces[0]?.name ?? ""));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirmStop, setConfirmStop] = useState(false);
  useEffect(() => {
    if (!open) return;
    setF(initial(watch, namespace ?? namespaces[0]?.name ?? ""));
    setErrors({});
    setConfirmStop(false);
  }, [open, watch?.id]);

  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    enabled: open,
    staleTime: 60_000,
  });
  const filter = useDebounced(
    useMemo(
      () => ({
        kinds: f.kinds,
        include: splitPatterns(f.include),
        exclude: splitPatterns(f.exclude),
      }),
      [f.kinds, f.include, f.exclude],
    ),
    600,
  );
  const preview = useQuery({
    queryKey: ["watch-preview", source.id, path, filter],
    queryFn: () =>
      data(
        Sources.previewWatch({
          client,
          body: { source: source.id, path, ...filter },
        }),
      ),
    enabled: open && !editing,
    staleTime: 60_000,
    retry: false,
  });

  const save = useMutation({
    mutationFn: async () => {
      const opts = {
        kinds: f.kinds,
        poll_minutes: Number(f.poll),
        stable_seconds: Number(f.stable),
        include: splitPatterns(f.include),
        exclude: splitPatterns(f.exclude),
        steps: f.run === "steps" ? f.steps : null,
        pipeline: f.run === "pipeline" ? Number(f.pipeline) : null,
        enabled: f.enabled,
      };
      if (watch) return data(Sources.updateWatch({ client, path: { wid: watch.id }, body: opts }));
      return data(
        Sources.createWatch({
          client,
          body: {
            source: source.id,
            path,
            namespace: f.namespace,
            backfill: f.backfill,
            ...opts,
          },
        }),
      );
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["watches"] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
      toast({
        tone: "green",
        title: editing ? "Watched folder saved" : "Watching the folder",
        body: `${source.name}:${path || "/"} → ${f.namespace}`,
      });
      onOpenChange(false);
    },
    onError: (e: Error) => setErrors({ _: e.message }),
  });
  const stop = useMutation({
    mutationFn: () => data(Sources.deleteWatch({ client, path: { wid: watch!.id } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["watches"] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
      toast({
        title: "Stopped watching",
        body: `${source.name}:${path || "/"}`,
      });
      onOpenChange(false);
    },
    onError: (e: Error) => setErrors({ _: e.message }),
  });

  const submit = () => {
    const e: Record<string, string> = {};
    if (!editing && !f.namespace) e.namespace = "Choose a namespace.";
    if (!/^\d+$/.test(f.poll.trim()) || Number(f.poll) < 1) e.poll = "Whole minutes, at least 1.";
    if (!/^\d+$/.test(f.stable.trim())) e.stable = "Whole seconds, 0 or more.";
    if (f.run === "pipeline" && !f.pipeline) e.pipeline = "Choose a pipeline.";
    if (f.run === "steps" && !f.steps.length) e.steps = "Pick at least one step.";
    setErrors(e);
    if (!Object.keys(e).length) save.mutate();
  };

  const p = preview.data;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Watched folder ${watch?.id}` : `Watch ${source.name}:${path || "/"}`}
      description={editing ? `${source.name}:${path || "/"} → ${watch?.namespace}` : undefined}
      className="max-w-[580px]"
      actions={
        confirmStop ? null : (
          <>
            {editing && (
              <Button variant="danger-ghost" className="mr-auto" onClick={() => setConfirmStop(true)}>
                Stop watching…
              </Button>
            )}
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={submit} disabled={save.isPending}>
              {save.isPending ? "Saving…" : editing ? "Save" : "Start watching"}
            </Button>
          </>
        )
      }
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field
          label="Namespace"
          error={errors.namespace}
          hint={editing ? "Files keep going to the same namespace" : undefined}
        >
          {({ id, describedBy }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              value={f.namespace}
              disabled={editing}
              onChange={(e) => set("namespace", e.target.value)}
              options={[
                ...(f.namespace ? [] : [{ value: "", label: "Choose…" }]),
                ...namespaces.map((n) => ({ value: n.name, label: n.name })),
              ]}
            />
          )}
        </Field>
        <Field label="Pick up">
          {({ id }) => (
            <Select
              id={id}
              value={f.kinds}
              onChange={(e) => set("kinds", e.target.value as Form["kinds"])}
              options={PICK}
            />
          )}
        </Field>
        <Field label="Check every (minutes)" error={errors.poll}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              inputMode="numeric"
              value={f.poll}
              onChange={(e) => set("poll", e.target.value)}
            />
          )}
        </Field>
        <Field label="Wait until unchanged (seconds)" error={errors.stable}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              inputMode="numeric"
              value={f.stable}
              onChange={(e) => set("stable", e.target.value)}
            />
          )}
        </Field>
        <Field label="Include" hint="Patterns, comma-separated" optional>
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              mono
              placeholder="*.m4a, *.srt"
              value={f.include}
              onChange={(e) => set("include", e.target.value)}
            />
          )}
        </Field>
        <Field label="Exclude" optional>
          {({ id }) => (
            <Input
              id={id}
              mono
              placeholder="drafts/*"
              value={f.exclude}
              onChange={(e) => set("exclude", e.target.value)}
            />
          )}
        </Field>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[13px] font-bold text-fg-strong" id="watch-run">
          Run on new files
        </span>
        <Segmented
          className="self-start"
          value={f.run}
          onChange={(v) => set("run", v as Run)}
          items={[
            { value: "default", label: "Namespace pipeline" },
            { value: "pipeline", label: "A pipeline" },
            { value: "steps", label: "These steps" },
          ]}
        />
        {f.run === "default" && (
          <p className="text-[12.5px] text-fg-muted">
            Whatever {f.namespace || "the namespace"} runs by default (Pipelines → namespace defaults).
          </p>
        )}
        {f.run === "pipeline" && (
          <Field error={errors.pipeline}>
            {({ id }) => (
              <Select
                id={id}
                aria-label="Pipeline"
                value={f.pipeline}
                onChange={(e) => set("pipeline", e.target.value)}
                options={[
                  {
                    value: "",
                    label: pipelines.isLoading ? "Loading…" : "Choose a pipeline",
                  },
                  ...(pipelines.data?.pipelines ?? []).map((x) => ({
                    value: String(x.id),
                    label: `${x.name} · v${x.current}`,
                  })),
                ]}
              />
            )}
          </Field>
        )}
        {f.run === "steps" && (
          <div role="group" aria-labelledby="watch-run" className="flex flex-col gap-1">
            <div className="flex flex-wrap gap-x-3 gap-y-2">
              {WATCH_STEPS.map((s) => (
                <Checkbox
                  key={s}
                  label={s}
                  checked={f.steps.includes(s)}
                  onCheckedChange={(on) =>
                    set(
                      "steps",
                      on ? WATCH_STEPS.filter((x) => x === s || f.steps.includes(x)) : f.steps.filter((x) => x !== s),
                    )
                  }
                />
              ))}
            </div>
            {errors.steps && (
              <p role="alert" className="text-[12.5px] text-red-dark">
                {errors.steps}
              </p>
            )}
          </div>
        )}
      </div>

      {!editing && (
        <div className="flex flex-wrap items-center gap-2.5 rounded-md border border-blue-border bg-blue-surface p-3">
          <Checkbox
            label="Import files already there (backfill)"
            checked={f.backfill}
            onCheckedChange={(v) => set("backfill", v)}
          />
          <span className="flex-1" />
          <span className="tabular text-[12.5px] font-semibold text-fg-accent" aria-live="polite">
            {preview.isFetching
              ? "Counting…"
              : p
                ? `${count(p.files)} ${p.files === 1 ? "file" : "files"} · ${count(p.audio)} audio · ${count(p.transcripts)} transcripts`
                : preview.error
                  ? "Couldn’t count the files"
                  : ""}
          </span>
        </div>
      )}
      {!editing && !f.backfill && p && p.files > 0 && (
        <p className="-mt-2 text-[12.5px] text-fg-muted">
          Without backfill, the {count(p.files)} files there now are marked skipped; only new ones are imported.
        </p>
      )}

      <Switch label="Enabled" checked={f.enabled} onCheckedChange={(v) => set("enabled", v)} />

      {errors._ && (
        <p
          role="alert"
          className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-red-dark"
        >
          {errors._}
        </p>
      )}
      {confirmStop && (
        <div
          role="alert"
          className="flex flex-col gap-2.5 rounded-md border border-red-border bg-red-surface p-3.5 text-[13.5px]"
        >
          <span>
            <b>
              Stop watching {source.name}:{path || "/"}?
            </b>{" "}
            Recordings already imported stay in {watch?.namespace}. Files on the storage aren’t touched.
          </span>
          <span className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setConfirmStop(false)}>
              Keep watching
            </Button>
            <Button size="sm" variant="danger" onClick={() => stop.mutate()} disabled={stop.isPending}>
              Stop watching
            </Button>
          </span>
        </div>
      )}
    </Dialog>
  );
}
