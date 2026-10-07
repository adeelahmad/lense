"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AudioLines, FileText, Image as ImageIcon, Plus, Shapes, Video, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { ContentTypes } from "@/app/openapi-client";
import type { ContentType } from "@/app/openapi-client/types.gen";
import { rulesText, type Rules } from "@/components/content-types/model";
import { CatalogHeader, useContentTypes, usePipelineCatalog } from "@/components/pipelines/catalog-header";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { SectionTitle } from "@/components/ui/panel";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const BASE: Record<string, { label: string; icon: LucideIcon; hint: string }> = {
  video: { label: "Video", icon: Video, hint: "Anything with moving pictures" },
  audio: { label: "Audio", icon: AudioLines, hint: "Sound only" },
  image: { label: "Image", icon: ImageIcon, hint: "Pictures and scanned pages" },
  text: { label: "Text", icon: FileText, hint: "Transcripts, documents and web pages" },
};

type Draft = {
  key?: string;
  base: string;
  label: string;
  description: string;
  pipeline: string;
  extensions: string;
  pattern: string;
  min: string;
  max: string;
  forms?: Rules["forms"];
};

const blank = (base = "audio"): Draft => ({
  base,
  label: "",
  description: "",
  pipeline: "",
  extensions: "",
  pattern: "",
  min: "",
  max: "",
});

function toDraft(t: ContentType): Draft {
  const r = (t.rules ?? {}) as Rules;
  return {
    key: t.key,
    base: t.base,
    label: t.label,
    description: t.description ?? "",
    pipeline: t.pipeline == null ? "" : String(t.pipeline),
    extensions: (r.extensions ?? []).join(" "),
    pattern: r.pattern ?? "",
    min: r.min_minutes == null ? "" : String(r.min_minutes),
    max: r.max_minutes == null ? "" : String(r.max_minutes),
    forms: r.forms,
  };
}

function draftRules(d: Draft): Rules {
  const r: Rules = {};
  const exts = d.extensions.split(/[\s,]+/).filter(Boolean);
  if (exts.length) r.extensions = exts;
  if (d.pattern.trim()) r.pattern = d.pattern.trim();
  if (d.min.trim()) r.min_minutes = Number(d.min);
  if (d.max.trim()) r.max_minutes = Number(d.max);
  if (d.forms?.length) r.forms = d.forms;
  return r;
}

/** Content types: the four base types and the vocabulary of subtypes under them, each with its pipeline. */
export function ContentTypesPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { admin } = useArchive();
  const types = useContentTypes();
  const pipelines = usePipelineCatalog();
  const list = pipelines.data?.pipelines ?? [];
  const [edit, setEdit] = useState<Draft | null>(null);

  useEffect(() => {
    if (admin && typeof window !== "undefined" && window.location.hash === "#new") setEdit(blank());
  }, [admin]);

  const done = (title: string) => {
    void qc.invalidateQueries({ queryKey: ["content-types"] });
    void qc.invalidateQueries({ queryKey: ["pipelines"] });
    toast({ tone: "green", title });
  };
  const fail = (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message });

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      const body = {
        label: d.label.trim(),
        description: d.description.trim() || null,
        pipeline: d.pipeline ? Number(d.pipeline) : null,
        rules: draftRules(d),
      };
      if (d.key) return data(ContentTypes.updateContentType({ client, path: { key: d.key }, body }));
      return data(
        ContentTypes.createContentType({
          client,
          body: { ...body, base: d.base as ContentType["base"] },
        }),
      );
    },
    onSuccess: (t) => {
      setEdit(null);
      done(`Saved ${t.label}`);
    },
    onError: fail,
  });
  const setPipeline = useMutation({
    mutationFn: ({ key, pipeline }: { key: string; pipeline: number | null }) =>
      data(ContentTypes.updateContentType({ client, path: { key }, body: { pipeline } })),
    onSuccess: (t) =>
      done(
        `${t.label} now runs ${t.pipeline == null ? "the namespace default" : (list.find((p) => p.id === t.pipeline)?.name ?? "that pipeline")}`,
      ),
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (key: string) => data(ContentTypes.deleteContentType({ client, path: { key } })),
    onSuccess: () => done("Content type removed"),
    onError: fail,
  });

  const all = types.data?.types ?? [];
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <CatalogHeader tab="content-types" />
      <p className="text-[13px] text-fg-secondary">
        Every resource is video, audio, image or text, read from its file. Under each are the content types you work
        with, e.g. a podcast or an interview. A resource gets the type someone chose for it, else the first whose rules
        match its file, else the general one, and runs that type’s pipeline (a namespace can override it on the
        Pipelines tab). Types without a pipeline run the namespace default.
      </p>
      {types.isLoading ? (
        <SkeletonRows rows={6} />
      ) : types.error ? (
        <EmptyState
          tone="error"
          icon={<Shapes />}
          title="Couldn’t load content types"
          actions={<Button onClick={() => types.refetch()}>Try again</Button>}
        >
          {(types.error as Error).message}
        </EmptyState>
      ) : (
        (types.data?.bases ?? Object.keys(BASE)).map((base) => {
          const info = BASE[base] ?? { label: base, icon: Shapes, hint: "" };
          const Icon = info.icon;
          const rows = all.filter((t) => t.base === base);
          return (
            <section key={base} aria-labelledby={`base-${base}`} className="flex flex-col gap-1">
              <SectionTitle>
                <span id={`base-${base}`} className="inline-flex items-center gap-2">
                  <Icon aria-hidden className="size-4 text-fg-secondary" /> {info.label}
                  <span className="text-[12.5px] font-normal text-fg-muted">{info.hint}</span>
                </span>
              </SectionTitle>
              <div className="overflow-x-auto rounded-md border border-border">
                <Table aria-label={`${info.label} content types`}>
                  <THead className="border-t-0">
                    <tr>
                      <Th>Name</Th>
                      <Th>Recognised by</Th>
                      <Th>Pipeline</Th>
                      <Th>
                        <span className="sr-only">Actions</span>
                      </Th>
                    </tr>
                  </THead>
                  <tbody>
                    {rows.map((t) => (
                      <Tr key={t.key} className="h-[54px]">
                        <Td>
                          <div className="flex min-w-0 flex-col gap-0.5">
                            <span className="flex items-center gap-1.5 font-semibold text-fg">
                              {t.label}
                              <code className="font-mono text-[11px] font-normal text-fg-muted">{t.key}</code>
                              {t.general && (
                                <span className="h-[18px] rounded-xs bg-surface-neutral px-1.5 text-[10.5px] font-semibold leading-[18px] text-fg-secondary">
                                  general
                                </span>
                              )}
                            </span>
                            {t.description && <span className="text-[12px] text-fg-muted">{t.description}</span>}
                          </div>
                        </Td>
                        <Td className="font-mono text-[12px] text-fg-secondary">
                          {t.general ? "anything else" : rulesText(t.rules as Rules) || "only when chosen"}
                        </Td>
                        <Td>
                          <Select
                            aria-label={`Pipeline for ${t.label}`}
                            size="sm"
                            className="w-[220px]"
                            value={t.pipeline == null ? "" : String(t.pipeline)}
                            disabled={!admin || setPipeline.isPending}
                            title={admin ? undefined : "Only admins can change content types"}
                            onChange={(e) =>
                              setPipeline.mutate({
                                key: t.key,
                                pipeline: e.target.value ? Number(e.target.value) : null,
                              })
                            }
                            options={[
                              { value: "", label: "Namespace default" },
                              ...list.map((p) => ({ value: String(p.id), label: p.name })),
                            ]}
                          />
                        </Td>
                        <Td className="whitespace-nowrap text-right">
                          {admin && (
                            <>
                              <Button size="xs" variant="ghost" onClick={() => setEdit(toDraft(t))}>
                                Edit
                              </Button>
                              {!t.general && (
                                <Button
                                  size="xs"
                                  variant="danger-ghost"
                                  disabled={remove.isPending}
                                  onClick={() => {
                                    if (
                                      window.confirm(`Remove ${t.label}? Resources that had it are recognised again.`)
                                    )
                                      remove.mutate(t.key);
                                  }}
                                >
                                  Remove
                                </Button>
                              )}
                            </>
                          )}
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              </div>
              {admin && (
                <Button
                  size="xs"
                  variant="link"
                  icon={<Plus />}
                  className="self-start"
                  onClick={() => setEdit(blank(base))}
                >
                  Add a {info.label.toLowerCase()} type
                </Button>
              )}
            </section>
          );
        })
      )}

      <Dialog
        open={edit != null}
        onOpenChange={(o) => !o && setEdit(null)}
        title={edit?.key ? `Edit ${edit.label}` : "New content type"}
        description="Rules recognise it when nobody chose it: every rule you fill in has to hold."
        actions={
          <>
            <Button variant="ghost" onClick={() => setEdit(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!edit?.label.trim() || save.isPending}
              onClick={() => edit && save.mutate(edit)}
            >
              {save.isPending ? "Saving…" : "Save"}
            </Button>
          </>
        }
      >
        {edit && (
          <div className="flex flex-col gap-3">
            <Field label="Base type" hint={edit.key ? "A content type keeps its base type" : undefined}>
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={edit.base}
                  disabled={Boolean(edit.key)}
                  onChange={(e) => setEdit({ ...edit, base: e.target.value })}
                  options={Object.entries(BASE).map(([value, b]) => ({ value, label: b.label }))}
                />
              )}
            </Field>
            <Field label="Name">
              {({ id }) => (
                <Input
                  id={id}
                  value={edit.label}
                  placeholder="e.g. Sermon"
                  onChange={(e) => setEdit({ ...edit, label: e.target.value })}
                />
              )}
            </Field>
            <Field label="Description" optional>
              {({ id }) => (
                <Input
                  id={id}
                  value={edit.description}
                  onChange={(e) => setEdit({ ...edit, description: e.target.value })}
                />
              )}
            </Field>
            <Field label="Pipeline" hint="What runs on it; the namespace default when none">
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={edit.pipeline}
                  onChange={(e) => setEdit({ ...edit, pipeline: e.target.value })}
                  options={[
                    { value: "", label: "Namespace default" },
                    ...list.map((p) => ({ value: String(p.id), label: p.name })),
                  ]}
                />
              )}
            </Field>
            <Field label="File extensions" optional hint="Space separated, e.g. .srt .vtt">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={edit.extensions}
                  onChange={(e) => setEdit({ ...edit, extensions: e.target.value })}
                />
              )}
            </Field>
            <Field label="Name pattern" optional hint="A regular expression found in the file name or title">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={edit.pattern}
                  placeholder="sermon|homily"
                  onChange={(e) => setEdit({ ...edit, pattern: e.target.value })}
                />
              )}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="At least (min)" optional>
                {({ id }) => (
                  <Input
                    id={id}
                    inputMode="decimal"
                    value={edit.min}
                    onChange={(e) => setEdit({ ...edit, min: e.target.value })}
                  />
                )}
              </Field>
              <Field label="At most (min)" optional>
                {({ id }) => (
                  <Input
                    id={id}
                    inputMode="decimal"
                    value={edit.max}
                    onChange={(e) => setEdit({ ...edit, max: e.target.value })}
                  />
                )}
              </Field>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
