"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Play } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { Recordings, Templates } from "@/app/openapi-client";
import type { Template, TemplatePreview } from "@/app/openapi-client/types.gen";
import { CodeEditor, type CodeEditorHandle } from "@/components/templates/code-editor";
import {
  VARIABLES,
  checkAgainstSchema,
  checkTemplate,
  parseSchema,
  schemaSummary,
} from "@/components/templates/template-model";
import { useTemplateUsage } from "@/components/templates/use-template-usage";
import { VersionsPanel } from "@/components/templates/versions-panel";
import { Button } from "@/components/ui/button";
import { Field, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Tab = "body" | "schema" | "versions";
const json = (v: unknown) => (v == null ? "" : JSON.stringify(v, null, 2));

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function ago(t: number | null, now: number) {
  if (!t) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`;
}

/** TP1 / TP2: edit a template with its variables at hand, preview it against a recording, check the output, compare versions. */
export function TemplateEditor({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { admin } = useArchive();
  const editor = useRef<CodeEditorHandle>(null);
  const q = useQuery({
    queryKey: ["template", id],
    queryFn: () => data(Templates.getTemplate({ client, path: { tid: id } })),
  });
  const t = q.data as Template | undefined;
  const [tab, setTab] = useState<Tab>("body");
  const [body, setBody] = useState("");
  const [schemaText, setSchemaText] = useState("");
  const [system, setSystem] = useState("");
  const [loadedVersion, setLoadedVersion] = useState<number | null>(null);
  const [rid, setRid] = useState("");
  const [result, setResult] = useState<{ value: unknown; at: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const { usage } = useTemplateUsage();

  useEffect(() => {
    if (t && t.version !== loadedVersion) {
      setBody(t.body);
      setSchemaText(json(t.schema));
      setSystem(t.system ?? "");
      setLoadedVersion(t.version);
    }
  }, [t, loadedVersion]);
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(i);
  }, []);

  const recs = useQuery({
    queryKey: ["recordings", "picker"],
    queryFn: () => data(Recordings.listRecordings({ client, query: { limit: 500 } })),
    staleTime: 60_000,
  });
  useEffect(() => {
    if (!rid && recs.data?.[0]) setRid(String(recs.data[0].id));
  }, [recs.data, rid]);

  const prompt = t?.kind === "prompt";
  const problems = useMemo(() => checkTemplate(body), [body]);
  const schema = parseSchema(schemaText);
  const dirty =
    Boolean(t) && (body !== t!.body || (prompt && schemaText !== json(t!.schema)) || system !== (t!.system ?? ""));
  const latest = t ? Math.max(t.current, ...(t.history ?? []).map((h) => h.version)) : 0;
  const readOnly = !admin;

  // The preview re-renders about 2 s after typing stops; it never writes to the recording.
  const params = useDebounced(
    useMemo(() => ({ body, schemaText, system, rid }), [body, schemaText, system, rid]),
    2000,
  );
  const preview = useQuery({
    queryKey: ["template-preview", id, params],
    queryFn: async () => {
      const r = await data(
        Templates.previewTemplate({
          client,
          body: {
            recording: Number(params.rid),
            kind: t!.kind,
            body: params.body,
            system: params.system || null,
            schema: prompt && schema.ok ? schema.value : null,
          },
        }),
      );
      return { ...r, at: Date.now() } as TemplatePreview & { at: number };
    },
    enabled: Boolean(t && params.rid),
    staleTime: Infinity,
    retry: false,
  });
  useEffect(() => setResult(null), [params]);

  const run = useMutation({
    mutationFn: () =>
      data(
        Templates.previewTemplate({
          client,
          body: {
            recording: Number(rid),
            kind: "prompt",
            body,
            system: system || null,
            schema: schema.ok ? schema.value : null,
            run: true,
          },
        }),
      ),
    onSuccess: (r) => {
      if (r.ok) setResult({ value: r.result, at: Date.now() });
      else
        toast({
          tone: "red",
          title: "The model didn’t answer",
          body: r.error ?? undefined,
        });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t run the model", body: e.message }),
  });

  const save = useMutation({
    mutationFn: () =>
      data(
        Templates.createTemplateVersion({
          client,
          path: { tid: id },
          body: {
            body,
            schema: prompt && schema.ok ? schema.value : null,
            system: system || null,
            publish: true,
          },
        }),
      ),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["template", id] });
      void qc.invalidateQueries({ queryKey: ["templates"] });
      toast({
        tone: "green",
        title: `Saved v${r.version}`,
        body: "Pipelines that follow the published version use it from their next run.",
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message }),
  });

  const restore = async (v: number) => {
    try {
      const old = await data(
        Templates.getTemplate({
          client,
          path: { tid: id },
          query: { version: v },
        }),
      );
      setBody(old.body);
      setSchemaText(json(old.schema));
      setSystem(old.system ?? "");
      setTab("body");
      toast({
        title: `v${v} restored as a draft`,
        body: `Save to publish it as v${latest + 1}.`,
      });
    } catch (e) {
      toast({
        tone: "red",
        title: "Couldn’t load that version",
        body: (e as Error).message,
      });
    }
  };

  if (q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading template">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[480px] w-full rounded-md" />
      </div>
    );
  if (q.error || !t) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<FileText />}
        title={e?.status === 404 ? "This template doesn’t exist" : "Couldn’t load the template"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/templates">All templates</Link>
          </Button>
        }
      >
        {e?.message}
      </EmptyState>
    );
  }

  const schemaError = prompt && !schema.ok ? 1 : 0;
  const saveReason = readOnly
    ? "Only admins can change templates"
    : schemaError
      ? "Fix 1 schema error first"
      : !dirty
        ? "No changes to save"
        : undefined;
  const checks = result && prompt && schema.ok ? checkAgainstSchema(result.value, schema.value as never) : [];
  const recTitle = recs.data?.find((r) => String(r.id) === rid)?.title ?? "the recording";
  const renderedWords = preview.data?.rendered ? preview.data.rendered.split(/\s+/).filter(Boolean).length : 0;
  const errorBar = problems[0]?.message ?? (preview.data && !preview.data.ok ? preview.data.error : null);

  return (
    <div className="flex h-[calc(100vh-64px)] min-h-[560px] flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2 md:px-5">
        <div className="flex min-w-0 flex-col py-1">
          <nav aria-label="Breadcrumb" className="text-[12px] font-medium text-fg-muted">
            <Link href="/templates" className="hover:text-fg hover:underline">
              Templates
            </Link>{" "}
            › {t.name}
          </nav>
          <div className="flex items-baseline gap-3">
            <h1 className="truncate text-[17px] font-bold text-fg">{t.name}</h1>
            <code className="font-mono text-[12px] text-fg-muted">
              {t.kind} · v{dirty ? latest + 1 : t.version}{" "}
              {dirty ? "draft" : t.version === t.current ? "published" : ""}
            </code>
          </div>
        </div>
        <span className="flex-1" />
        <Tabs
          aria-label="Template"
          className="border-b-0"
          size="sm"
          value={tab}
          onChange={(v) => setTab(v as Tab)}
          items={[
            { value: "body", label: prompt ? "Prompt" : "Template" },
            ...(prompt
              ? [
                  {
                    value: "schema",
                    label: schemaError ? "Output schema ✕" : "Output schema",
                  },
                ]
              : []),
            {
              value: "versions",
              label: "Versions",
              count: t.history?.length ?? 1,
            },
          ]}
        />
        <Button
          size="sm"
          variant="primary"
          disabled={Boolean(saveReason) || save.isPending}
          disabledReason={saveReason}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "Saving…" : `Save v${latest + 1}`}
        </Button>
      </header>

      {tab === "versions" ? (
        <VersionsPanel
          template={t}
          draftBody={dirty ? body : null}
          usedBy={usage.get(id) ?? []}
          onRestore={restore}
          readOnly={readOnly}
        />
      ) : (
        <div className="grid min-h-0 flex-1 lg:grid-cols-[200px_minmax(0,1fr)_minmax(0,1fr)]">
          <aside
            aria-label="Variables"
            className="hidden flex-col gap-1.5 overflow-y-auto border-r border-border bg-surface p-3.5 lg:flex"
          >
            <span className="label-caps pb-1">Variables</span>
            {VARIABLES.map((v) => (
              <button
                key={v.key}
                type="button"
                disabled={readOnly || tab !== "body"}
                onClick={() => editor.current?.insert(`{{ ${v.key} }}`)}
                title={readOnly ? undefined : "Insert at the cursor"}
                className="flex flex-col gap-0.5 rounded-[6px] px-2 py-1.5 text-left enabled:hover:bg-surface-neutral disabled:cursor-default"
              >
                <code className="font-mono text-[11.5px] font-medium leading-snug text-fg-accent [overflow-wrap:anywhere]">{`{{ ${v.key} }}`}</code>
                <span className="text-[11px] leading-snug text-fg-muted">{v.type}</span>
              </button>
            ))}
            <p className="mt-2 text-[11.5px] leading-snug text-fg-muted">
              Jinja: loops with <code className="font-mono">{"{% for s in speakers %}"}</code>, filters like{" "}
              <code className="font-mono">| tc</code> and <code className="font-mono">| json</code>.
            </p>
          </aside>

          <div className="flex min-h-[320px] min-w-0 flex-col">
            {tab === "body" ? (
              <>
                <CodeEditor
                  ref={editor}
                  label={`${prompt ? "Prompt" : "Template"} text`}
                  value={body}
                  onChange={setBody}
                  problems={problems}
                  readOnly={readOnly}
                />
                {errorBar && (
                  <div
                    role="alert"
                    className="flex gap-2 bg-red-surface px-3.5 py-2.5 text-[12.5px] font-medium leading-snug text-fg"
                  >
                    <span aria-hidden className="font-extrabold text-red">
                      ✕
                    </span>
                    <span className="min-w-0 break-words">
                      {errorBar}
                      {problems.length > 1 ? ` (+${problems.length - 1} more)` : ""}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col gap-3 overflow-y-auto p-4">
                <Field
                  label="System message"
                  optional
                  hint="Leave empty for the default: accurate, specific, JSON only"
                >
                  {({ id: fid, describedBy }) => (
                    <Textarea
                      id={fid}
                      aria-describedby={describedBy}
                      rows={3}
                      value={system}
                      readOnly={readOnly}
                      onChange={(e) => setSystem(e.target.value)}
                    />
                  )}
                </Field>
                <Field
                  label="Output schema (JSON Schema)"
                  error={!schema.ok ? schema.error : undefined}
                  hint={schema.ok ? `Checks: ${schemaSummary(schema.value as never) || "any object"}` : undefined}
                >
                  {({ id: fid, describedBy, invalid }) => (
                    <Textarea
                      id={fid}
                      aria-describedby={describedBy}
                      invalid={invalid}
                      mono
                      rows={18}
                      spellCheck={false}
                      readOnly={readOnly}
                      value={schemaText}
                      onChange={(e) => setSchemaText(e.target.value)}
                      className="text-[12.5px] leading-relaxed"
                    />
                  )}
                </Field>
              </div>
            )}
          </div>

          <section
            aria-label="Preview"
            className="flex min-h-0 min-w-0 flex-col gap-2.5 overflow-y-auto border-t border-border p-4 lg:border-l lg:border-t-0"
          >
            <div className="flex items-center gap-2">
              <h2 className="flex-1 text-[13px] font-bold text-fg">
                {prompt ? (result ? "Preview · structured result" : "Preview · rendered prompt") : "Preview"}
              </h2>
              <Select
                aria-label="Recording to preview against"
                className="h-8 w-[220px] text-[12.5px]"
                value={rid}
                onChange={(e) => setRid(e.target.value)}
                options={
                  recs.isLoading
                    ? [{ value: "", label: "Loading…" }]
                    : (recs.data ?? []).map((r) => ({
                        value: String(r.id),
                        label: r.title ?? `Recording ${r.id}`,
                      }))
                }
              />
            </div>
            <span className="text-[12px] text-fg-muted" aria-live="polite">
              {preview.isFetching
                ? "Rendering…"
                : result
                  ? `Model answered ${ago(result.at, now)} against ${recTitle}`
                  : preview.data?.ok
                    ? `Rendered ${ago(preview.data.at, now)} against ${recTitle} · ${count(renderedWords)} words · nothing is saved`
                    : ""}
            </span>
            {!recs.isLoading && !(recs.data ?? []).length ? (
              <p className="text-[13px] text-fg-secondary">Import a recording to preview templates against it.</p>
            ) : preview.error ? (
              <p
                role="alert"
                className="rounded-sm border border-red-border bg-red-surface px-3 py-2 text-[13px] text-red-dark"
              >
                {(preview.error as Error).message}
              </p>
            ) : preview.data && !preview.data.ok ? (
              <div
                role="alert"
                className="flex gap-2 rounded-md border border-red-border bg-red-surface px-3 py-2.5 text-[12.5px] leading-snug"
              >
                <span aria-hidden className="font-extrabold text-red">
                  ✕
                </span>
                <span className="min-w-0 break-words">
                  It doesn’t render: <code className="font-mono">{preview.data.error}</code>
                </span>
              </div>
            ) : result ? (
              <pre className="m-0 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface p-3 font-mono text-[12px] leading-[1.65] text-fg">
                {JSON.stringify(result.value, null, 2)}
              </pre>
            ) : t.kind === "report" && preview.data?.rendered ? (
              <iframe
                title="Report preview"
                sandbox=""
                srcDoc={preview.data.rendered}
                className="min-h-[420px] w-full flex-1 rounded-md border border-border bg-white"
              />
            ) : preview.data?.rendered != null ? (
              <pre className="m-0 max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface p-3 font-mono text-[12px] leading-[1.65] text-fg">
                {preview.data.rendered}
              </pre>
            ) : (
              <Skeleton className="h-40 rounded-md" />
            )}
            {result &&
              (checks.length ? (
                <div
                  role="alert"
                  className="flex gap-2 rounded-md border border-red-border bg-red-surface px-3 py-2.5 text-[12.5px] font-medium leading-snug"
                >
                  <span aria-hidden className="font-extrabold text-red">
                    ✕
                  </span>
                  <span>
                    Doesn’t match the output schema: {checks.slice(0, 4).join("; ")}
                    {checks.length > 4 ? "…" : ""}.
                  </span>
                </div>
              ) : (
                <div className="flex gap-2 rounded-md border border-green-border bg-green-surface px-3 py-2.5 text-[12.5px] font-medium leading-snug">
                  <span aria-hidden className="font-extrabold text-green-dark">
                    ✓
                  </span>
                  <span>
                    Matches the output schema
                    {schema.ok && schemaSummary(schema.value as never)
                      ? `: ${schemaSummary(schema.value as never)}`
                      : ""}
                    .
                  </span>
                </div>
              ))}
            {prompt && (
              <div className={cn("flex flex-wrap items-center gap-2", !preview.data?.ok && "opacity-60")}>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Play />}
                  disabled={!rid || run.isPending || !schema.ok}
                  disabledReason={!schema.ok ? "Fix the output schema first" : undefined}
                  onClick={() => run.mutate()}
                >
                  {run.isPending ? "Asking the model…" : result ? "Run the model again" : "Run the model"}
                </Button>
                {result && (
                  <Button size="sm" variant="ghost" onClick={() => setResult(null)}>
                    Show the prompt
                  </Button>
                )}
                <span className="text-[12px] text-fg-muted">
                  Uses Settings → LLM; nothing is saved to the recording.
                </span>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
