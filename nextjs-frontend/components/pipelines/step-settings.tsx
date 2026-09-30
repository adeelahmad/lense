"use client";

import { useQuery } from "@tanstack/react-query";

import { Sources, Templates } from "@/app/openapi-client";
import type { TemplateSummary } from "@/app/openapi-client/types.gen";
import { stepLabel } from "@/components/activity/job-model";
import { DESCRIBE, PROVIDES, type StepSpec } from "@/components/pipelines/pipeline-model";
import { Checkbox, Field, Input, Select } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const num = (v: string) => (v.trim() === "" ? undefined : Number(v));

function VersionSelect({ template, value, onChange, disabled }: { template?: number; value?: number; onChange: (v?: number) => void; disabled?: boolean }) {
  const client = useApiClient();
  const t = useQuery({
    queryKey: ["template", template],
    queryFn: () => data(Templates.getTemplate({ client, path: { tid: template! } })),
    enabled: template != null,
    staleTime: 30_000,
  });
  return (
    <Field label="Version" hint="Pin a version, or follow the published one">
      {({ id, describedBy }) => (
        <Select
          id={id}
          aria-describedby={describedBy}
          disabled={disabled || template == null}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
          options={[
            { value: "", label: t.data ? `Published (v${t.data.current})` : "Published" },
            ...(t.data?.history ?? []).map((h) => ({ value: String(h.version), label: `v${h.version}${h.version === t.data?.current ? " · published" : ""}${h.notes ? ` · ${h.notes}` : ""}` })),
          ]}
        />
      )}
    </Field>
  );
}

/** PL2 right panel: the selected step's settings, conditions and what it makes available. */
export function StepSettings({
  step,
  onChange,
  templates,
  readOnly,
  problem,
}: {
  step: StepSpec;
  onChange: (s: StepSpec) => void;
  templates: TemplateSummary[];
  readOnly?: boolean;
  problem?: string;
}) {
  const client = useApiClient();
  const { admin } = useArchive();
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => data(Sources.listSources({ client })), enabled: admin && step.type === "export", staleTime: 60_000 });
  const set = (patch: Partial<StepSpec>) => onChange({ ...step, ...patch });
  const setWhen = (patch: Partial<NonNullable<StepSpec["when"]>>) => set({ when: { ...(step.when ?? {}), ...patch } });
  const kind = { llm: "prompt", report: "report", export: "export" }[step.type];
  const choices = templates.filter((t) => t.kind === kind);
  const provides = step.type === "llm" ? `outputs.${step.key || "<key>"}` : PROVIDES[step.type];

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-[15px] font-bold text-fg">{stepLabel(step.type, step.name)} · settings</h2>
        <p className="mt-0.5 text-[12.5px] text-fg-secondary">{DESCRIBE[step.type]}</p>
      </div>
      {problem && (
        <p role="alert" className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[12.5px] text-red-dark">
          {problem}
        </p>
      )}
      <fieldset disabled={readOnly} className="flex flex-col gap-3">
        <Field label="Label" optional hint="Shown in Activity instead of the step’s name">
          {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} value={step.name ?? ""} placeholder={stepLabel(step.type)} onChange={(e) => set({ name: e.target.value })} />}
        </Field>

        {kind && (
          <>
            <Field label={kind === "prompt" ? "Prompt template" : kind === "report" ? "Report template" : "Export template"}>
              {({ id }) => (
                <Select
                  id={id}
                  value={step.template == null ? "" : String(step.template)}
                  onChange={(e) => set({ template: e.target.value ? Number(e.target.value) : undefined, version: undefined })}
                  options={[
                    { value: "", label: kind === "report" ? "Built-in recording report" : "Choose a template…" },
                    ...choices.map((t) => ({ value: String(t.id), label: `${t.name} · v${t.current}` })),
                  ]}
                />
              )}
            </Field>
            {step.template != null && <VersionSelect template={step.template} value={step.version} onChange={(v) => set({ version: v })} disabled={readOnly} />}
          </>
        )}
        {step.type === "llm" && (
          <>
            <Field label="Output name" hint="Saved on the recording as outputs.<name>; lowercase letters, digits and _">
              {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} mono value={step.key ?? ""} placeholder="meeting_notes" onChange={(e) => set({ key: e.target.value })} />}
            </Field>
            <Field label="Model" optional hint="Leave empty for the model in Settings → LLM">
              {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} mono value={step.model ?? ""} onChange={(e) => set({ model: e.target.value })} />}
            </Field>
          </>
        )}
        {step.type === "export" && (
          <>
            <Field label="File name" hint="A template too, e.g. {{ recording.title }}.md">
              {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} mono value={step.filename ?? ""} placeholder="{{ recording.title }}.md" onChange={(e) => set({ filename: e.target.value })} />}
            </Field>
            <div className="grid grid-cols-2 gap-2.5">
              <Field label="Copy to" optional>
                {({ id }) => (
                  <Select
                    id={id}
                    value={step.destination?.source == null ? "" : String(step.destination.source)}
                    onChange={(e) => set({ destination: e.target.value ? { source: Number(e.target.value), path: step.destination?.path ?? "" } : undefined })}
                    options={[{ value: "", label: "Keep on the server" }, ...(sources.data ?? []).map((s) => ({ value: String(s.id), label: s.name }))]}
                  />
                )}
              </Field>
              <Field label="Folder" optional>
                {({ id }) => (
                  <Input
                    id={id}
                    mono
                    disabled={step.destination?.source == null}
                    value={step.destination?.path ?? ""}
                    onChange={(e) => step.destination && set({ destination: { ...step.destination, path: e.target.value } })}
                  />
                )}
              </Field>
            </div>
          </>
        )}
        {step.type === "summarize" && (
          <p className="rounded-sm bg-surface px-3 py-2 text-[12.5px] leading-normal text-fg-secondary">
            Uses the model in Settings → LLM and the built-in summary prompt. To use your own prompt, add an LLM step with a prompt template.
          </p>
        )}
        {(step.type === "transcribe" || step.type === "diarize") && (
          <Checkbox
            label={step.type === "transcribe" ? "Transcribe again even if the file came with a transcript" : "Find speakers again even if the transcript labelled them"}
            checked={Boolean(step.force)}
            onCheckedChange={(v) => set({ force: v || undefined })}
            disabled={readOnly}
          />
        )}

        <div className="flex flex-col gap-2 rounded-md border border-border p-3">
          <span className="text-[13px] font-bold text-fg-strong">Run only if <span className="font-normal text-fg-muted">optional</span></span>
          <div className="grid grid-cols-2 gap-2.5">
            <Field label="Longer than (min)">
              {({ id }) => <Input id={id} inputMode="decimal" value={step.when?.min_minutes ?? ""} onChange={(e) => setWhen({ min_minutes: num(e.target.value) })} />}
            </Field>
            <Field label="Shorter than (min)">
              {({ id }) => <Input id={id} inputMode="decimal" value={step.when?.max_minutes ?? ""} onChange={(e) => setWhen({ max_minutes: num(e.target.value) })} />}
            </Field>
            <Field label="Source">
              {({ id }) => (
                <Select
                  id={id}
                  value={step.when?.source ?? ""}
                  onChange={(e) => setWhen({ source: e.target.value || undefined })}
                  options={[
                    { value: "", label: "Any" },
                    { value: "audio", label: "Audio or video" },
                    { value: "transcript", label: "Imported transcript" },
                  ]}
                />
              )}
            </Field>
            <Field label="Languages">
              {({ id }) => (
                <Input
                  id={id}
                  mono
                  placeholder="en, de"
                  value={(step.when?.languages ?? []).join(", ")}
                  onChange={(e) => setWhen({ languages: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
                />
              )}
            </Field>
          </div>
          <span className="text-[12px] text-fg-muted">Skipped runs show as “skipped”, not failed.</span>
        </div>
      </fieldset>

      <div className="flex flex-col gap-1.5">
        <span className="text-[13px] font-bold text-fg-strong">Makes available to later steps</span>
        <code className="rounded-sm border border-border bg-surface px-3 py-2.5 font-mono text-[12px] leading-relaxed text-fg">{provides}</code>
      </div>
    </div>
  );
}
