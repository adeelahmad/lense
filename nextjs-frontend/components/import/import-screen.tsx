"use client";

import { FolderOpen, Upload } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { namespaceNameProblem, parseMapping, titleFromName } from "@/components/import/files";
import { ImportQueue } from "@/components/import/import-queue";
import { PasteTab } from "@/components/import/paste-tab";
import { chooseFiles, defaultImportNamespace, isFileDrag, takeFiles } from "@/components/import/pending";
import { SourceTab } from "@/components/import/source-tab";
import { FileDetail, FileList, ProblemCard, audioTwinOf } from "@/components/import/upload-tab";
import { fileBody, useImportFiles, useImportQueue, useNamespacePipeline, useNamespaceSpeakers } from "@/components/import/use-import";
import { LibraryTabs } from "@/components/library/library-tabs";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/states";
import { plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Tab = "upload" | "paste" | "source";
const NEW_NS = "\u0000new";

/** Where the import goes: a namespace you can edit, or (admins) a new one. */
function NamespaceField({ value, onChange, options, admin, label = "Namespace" }: { value: string; onChange: (v: string) => void; options: string[]; admin: boolean; label?: string }) {
  const [creating, setCreating] = useState(Boolean(value && !options.includes(value)));
  const problem = creating ? namespaceNameProblem(value) : null;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-bold text-fg-strong" id="import-ns-label">
        {label}
      </span>
      {creating ? (
        <div className="flex items-center gap-1.5">
          <Input
            aria-labelledby="import-ns-label"
            value={value}
            autoFocus
            onChange={(e) => onChange(e.target.value.toLowerCase())}
            placeholder="new-namespace"
            invalid={Boolean(problem)}
            mono
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setCreating(false);
              onChange(options[0] ?? "");
            }}
          >
            Cancel
          </Button>
        </div>
      ) : (
        <Select
          aria-labelledby="import-ns-label"
          value={value}
          onChange={(e) => {
            if (e.target.value === NEW_NS) {
              setCreating(true);
              onChange("");
            } else onChange(e.target.value);
          }}
          options={[...options, ...(admin ? [{ value: NEW_NS, label: "New namespace…" }] : [])]}
        />
      )}
      {creating && <span className={cn("text-[12px] leading-snug", problem ? "text-red-dark" : "text-fg-muted")}>{problem ?? "It’s created when the first import lands."}</span>}
    </div>
  );
}

/** Import (I1–I5): upload transcripts, paste text or watch a folder of a source. Nothing is saved before the preview. */
export function ImportScreen() {
  const { namespaces, namespace: topNs, can, admin, me } = useArchive();
  const params = useSearchParams();
  const router = useRouter();
  const initialTab = (params.get("tab") as Tab) || "upload";
  const [tab, setTab] = useState<Tab>(["upload", "paste", "source"].includes(initialTab) ? initialTab : "upload");
  const editable = useMemo(() => namespaces.filter((n) => can("editor", n.name)).map((n) => n.name), [namespaces, can]);
  const [ns, setNs] = useState("");
  const files = useImportFiles();
  const queue = useImportQueue();
  const directory = useNamespaceSpeakers(ns || null);
  const pipeline = useNamespacePipeline(ns || null);
  const [selected, setSelected] = useState<string | null>(null);
  const [over, setOver] = useState(false);

  // Default namespace (once): the top bar's, if you can import there, else your busiest one.
  const defaulted = useRef(false);
  useEffect(() => {
    if (defaulted.current || !editable.length) return;
    defaulted.current = true;
    setNs((cur) => cur || (defaultImportNamespace(namespaces, (n) => can("editor", n), topNs) ?? editable[0]));
  }, [editable, topNs, namespaces, can]);

  // Files dropped or picked elsewhere (Home, the Library) arrive here.
  const { add } = files;
  useEffect(() => {
    const p = takeFiles();
    if (p) {
      add(p.files);
      if (p.namespace) setNs(p.namespace);
      setTab("upload");
    }
  }, [add]);

  const items = files.items;
  useEffect(() => {
    if (!selected || !items.some((i) => i.id === selected)) setSelected(items[0]?.id ?? null);
  }, [items, selected]);

  const setTabUrl = (t: Tab) => {
    setTab(t);
    router.replace(t === "upload" ? "/import" : `/import?tab=${t}`, { scroll: false });
  };

  const nsProblem = namespaceNameProblem(ns);
  const nsReason = !ns ? "Choose a namespace" : editable.includes(ns) ? null : admin ? nsProblem : needRole("editor", ns);
  const ready = items.filter((i) => i.status === "ready");
  const reading = items.filter((i) => i.status === "reading").length;
  const mappingProblem = ready.find((i) => parseMapping(i.mapping, i.preview?.speakers ?? []).errors.length);
  const importReason = nsReason ?? (!ready.length ? (reading ? "Still reading the files…" : "No file is ready to import") : mappingProblem ? `Fix the speaker mapping of ${mappingProblem.file.name}` : null);
  const current = items.find((i) => i.id === selected) ?? null;

  const nsControl = <NamespaceField value={ns} onChange={setNs} options={editable} admin={admin} />;

  const importFiles = () => {
    const target = ns;
    void queue.send(
      ready.map((it) => ({
        key: it.id,
        name: it.file.name,
        title: it.title.trim() || titleFromName(it.file.name),
        namespace: target,
        kind: "file" as const,
        body: () => fileBody(it, target),
      })),
    );
    files.clear();
  };

  if (me && !can("editor")) {
    return (
      <div className="px-4 py-6 md:px-6">
        <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Import</h1>
        <EmptyState icon={<Upload />} title="Importing needs editor access">
          You can read, listen, search and chat in {namespaces.map((n) => n.name).join(", ") || "your namespaces"}, but importing needs the editor role. Ask an owner of the
          namespace to make you an editor.
        </EmptyState>
      </div>
    );
  }

  const attention = items.length - ready.length - reading;
  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <div className="flex items-center gap-2.5 px-4 pt-4 md:px-6 md:pt-[18px]">
        <h1 className="flex-1 text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Import</h1>
        {ns && (
          <span className="text-[13px] text-fg-muted">
            into <b className="font-bold text-fg-strong">{ns}</b>
          </span>
        )}
      </div>

      {queue.queue.length > 0 ? (
        <div className="px-4 py-5 md:px-6">
          <ImportQueue queue={queue.queue} onMore={queue.reset} />
        </div>
      ) : (
        <>
          <div className="mt-1.5 px-2 md:px-3">
            <LibraryTabs<Tab>
              value={tab}
              onChange={setTabUrl}
              items={[
                { value: "upload", label: "Upload", count: items.length || undefined },
                { value: "paste", label: "Paste" },
                admin
                  ? { value: "source", label: "From a source" }
                  : { value: "source", label: "From a source", disabledReason: "Only admins browse sources. Ask an admin to watch a folder for your namespace." },
              ]}
            />
          </div>

          {tab === "paste" && (
            <PasteTab
              namespace={ns || null}
              namespaceControl={nsControl}
              directory={directory.data}
              blockReason={nsReason}
              onImport={(b) =>
                void queue.send([
                  {
                    key: `paste-${Date.now()}`,
                    name: "Pasted text",
                    title: b.title || "Pasted transcript",
                    namespace: ns,
                    kind: "paste",
                    body: async () => ({ namespace: ns, text: b.text, title: b.title, speakers: b.speakers, format: "auto" as const }),
                  },
                ])
              }
            />
          )}

          {tab === "source" && admin && <SourceTab namespace={ns || null} namespaces={namespaces.map((n) => n.name)} />}

          {tab === "upload" &&
            (items.length === 0 ? (
              <div
                className="flex flex-1 p-4 md:p-6"
                onDragOver={(e) => {
                  if (isFileDrag(e)) {
                    e.preventDefault();
                    setOver(true);
                  }
                }}
                onDragLeave={() => setOver(false)}
                onDrop={(e) => {
                  if (!isFileDrag(e)) return;
                  e.preventDefault();
                  setOver(false);
                  files.add(Array.from(e.dataTransfer.files));
                }}
              >
                <div
                  className={cn(
                    "flex flex-1 flex-col items-center justify-center gap-4 rounded-lg border-[1.5px] border-dashed border-blue-border px-6 py-16 text-center",
                    over && "border-blue bg-blue-surface",
                  )}
                >
                  <span className="grid size-12 place-items-center rounded-full bg-blue-surface text-blue">
                    <FolderOpen className="size-6" aria-hidden />
                  </span>
                  <div className="flex flex-col gap-1.5">
                    <h2 className="text-[17px] font-bold text-fg">Drop transcripts here</h2>
                    <p className="max-w-md text-[14px] leading-normal text-fg-secondary">
                      txt, md, mdx, docx, doc, pdf, srt, vtt, json or jsonl, up to {files.maxMb} MB each. You’ll see how each one was read before anything is saved.
                    </p>
                  </div>
                  <div className="flex flex-wrap justify-center gap-2">
                    <Button variant="primary" icon={<Upload />} onClick={async () => files.add(await chooseFiles())}>
                      Choose files
                    </Button>
                    <Button variant="secondary" onClick={() => setTabUrl("paste")}>
                      Paste text instead
                    </Button>
                  </div>
                  <p className="text-[12.5px] text-fg-muted">Audio and video come in through watched folders (Sources), which have no size limit.</p>
                </div>
              </div>
            ) : (
              <>
                <div className="grid min-h-0 flex-1 border-t border-border md:grid-cols-[380px_minmax(0,1fr)]">
                  <div className="flex flex-col gap-2 border-b border-border bg-surface p-4 md:border-b-0 md:border-r">
                    <FileList items={items} selected={selected} onSelect={setSelected} onAdd={files.add} />
                  </div>
                  <div className="min-w-0 px-4 py-4 md:px-6 md:py-[18px]">
                    {current?.status === "ready" ? (
                      <FileDetail
                        it={current}
                        onPatch={(p) => files.patch(current.id, p)}
                        namespace={ns || null}
                        namespaceControl={nsControl}
                        pipeline={pipeline.name}
                        directory={directory.data}
                        audioTwin={audioTwinOf(current, items)}
                      />
                    ) : current?.problem ? (
                      <ProblemCard
                        it={current}
                        className="max-w-[520px]"
                        onRemove={() => files.remove(current.id)}
                        onReplace={(fs) => {
                          if (!fs.length) return;
                          files.remove(current.id);
                          files.add(fs);
                        }}
                      />
                    ) : current ? (
                      <p className="text-[13.5px] text-fg-muted" aria-live="polite">
                        Reading {current.file.name}…
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-2.5 border-t border-border bg-background px-4 py-3.5 md:px-6">
                  <span className="min-w-0 flex-1 text-[13px] leading-snug text-fg-secondary" aria-live="polite">
                    <b className="font-bold text-fg">
                      {ready.length} of {plural(items.length, "file")} ready.
                    </b>{" "}
                    {reading ? `Reading ${reading}… ` : ""}
                    {attention > 0 ? `${attention} ${attention === 1 ? "needs" : "need"} attention — ${attention === 1 ? "it’ll" : "they’ll"} be skipped unless fixed.` : ""}
                  </span>
                  <Button variant="ghost" onClick={files.clear}>
                    Cancel
                  </Button>
                  <Button variant="primary" disabled={Boolean(importReason)} disabledReason={importReason ?? undefined} onClick={importFiles}>
                    Import {plural(ready.length, "file")}
                  </Button>
                </div>
              </>
            ))}
        </>
      )}
    </div>
  );
}
