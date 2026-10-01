"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Folder, HardDriveDownload } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Imports, Sources } from "@/app/openapi-client";
import type { BrowseEntry } from "@/app/openapi-client/types.gen";
import { importable, kindOf, pipelineOptions, sourceImportSummary } from "@/components/import/files";
import { fileIcon } from "@/components/import/upload-tab";
import { useNamespacePipeline } from "@/components/import/use-import";
import { CollectionField } from "@/components/library/collections-ui";
import { sourceTypeLabel } from "@/components/library/source-labels";
import { pickupText } from "@/components/sources/source-model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Select } from "@/components/ui/field";
import { Segmented } from "@/components/ui/tabs";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { bytes, plural, relative } from "@/lib/format";

const KIND_NOTE: Record<string, string> = {
  transcript: "Transcript",
  audio: "Audio",
  video: "Video",
  document: "Document",
  image: "Image",
  unsupported: "",
};

function crumbs(path: string): { label: string; path: string }[] {
  const parts = path.split("/").filter(Boolean);
  return parts.map((p, i) => ({
    label: p,
    path: (path.startsWith("/") ? "/" : "") + parts.slice(0, i + 1).join("/"),
  }));
}

/**
 * Import I3 (from a source), admins only. Browse a connected source, then import the files you choose now, or watch the
 * folder so new files come in on their own. Files that are recordings already say where.
 */
export function SourceTab({ namespace, namespaces }: { namespace: string | null; namespaces: string[] }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => data(Sources.listSources({ client })),
    staleTime: 30_000,
  });
  const [sid, setSid] = useState<number | null>(null);
  const [path, setPath] = useState("");
  const [watchOpen, setWatchOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const open = (p: string) => {
    setPath(p);
    setChosen(new Set());
  };

  useEffect(() => {
    if (sid == null && sources.data?.length) setSid(sources.data[0].id);
  }, [sid, sources.data]);

  const listing = useQuery({
    queryKey: ["browse", sid, path],
    queryFn: () =>
      data(
        Sources.browseSource({
          client,
          path: { sid: sid as number },
          query: { path },
        }),
      ),
    enabled: sid != null,
    retry: false,
  });
  const counts = useQuery({
    queryKey: ["watch-preview", sid, path],
    queryFn: () => data(Sources.previewWatch({ client, body: { source: sid as number, path } })),
    enabled: sid != null && path !== "",
    retry: false,
    staleTime: 60_000,
  });

  if (sources.isLoading) return <SkeletonRows rows={6} className="p-6" />;
  if (sources.isError)
    return (
      <EmptyState
        tone="error"
        title="Couldn’t load sources"
        actions={<Button onClick={() => sources.refetch()}>Try again</Button>}
      >
        {(sources.error as Error).message}
      </EmptyState>
    );
  if (!sources.data?.length)
    return (
      <EmptyState
        icon={<HardDriveDownload />}
        title="No sources connected yet"
        actions={
          <Button asChild variant="primary">
            <Link href="/sources">Connect a source</Link>
          </Button>
        }
      >
        Connect S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV or a local folder, then watch folders from here.
      </EmptyState>
    );

  const src = sources.data.find((s) => s.id === sid);
  const entries: BrowseEntry[] = [...(listing.data ?? [])].sort(
    (a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name),
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-border px-4 py-3 md:px-5">
        <Select
          aria-label="Source"
          className="h-[34px] w-auto min-w-[220px] text-[13px] font-semibold"
          value={String(sid ?? "")}
          onChange={(e) => {
            setSid(Number(e.target.value));
            open("");
          }}
          options={sources.data.map((s) => ({
            value: String(s.id),
            label: `${sourceTypeLabel(s.type, s.label)} · ${s.name}`,
          }))}
        />
        <nav
          aria-label="Folder"
          className="flex min-w-0 flex-wrap items-center gap-1 text-[13px] font-medium text-fg-secondary"
        >
          <button type="button" className="hover:text-fg hover:underline" onClick={() => open("")}>
            {src?.name ?? "Top"}
          </button>
          {crumbs(path).map((c) => (
            <span key={c.path} className="flex items-center gap-1">
              <ChevronRight className="size-3.5" aria-hidden />
              <button type="button" className="hover:text-fg hover:underline" onClick={() => open(c.path)}>
                {c.label}
              </button>
            </span>
          ))}
        </nav>
        {src?.health && !src.health.ok && (
          <span className="text-[12.5px] text-red-dark">
            ✕ {src.health.error || "The last connection test failed."}
          </span>
        )}
      </div>
      <div className="min-h-[240px] flex-1 overflow-y-auto">
        {listing.isLoading ? (
          <SkeletonRows rows={6} />
        ) : listing.isError ? (
          <EmptyState
            tone="error"
            title="Couldn’t open this folder"
            actions={<Button onClick={() => listing.refetch()}>Try again</Button>}
          >
            {(listing.error as Error).message}
          </EmptyState>
        ) : !entries.length ? (
          <EmptyState title="This folder is empty" />
        ) : (
          <ul aria-label={`Contents of ${path || src?.name || "the source"}`}>
            {entries.map((e) => {
              const kind = e.dir ? null : kindOf(e.name);
              const Icon = e.dir
                ? Folder
                : fileIcon({
                    file: new File([], e.name),
                    status: "ready",
                    problem: undefined,
                  });
              const where = (e.imported ?? []).map((x) => x.namespace);
              const row = (
                <>
                  <Icon className="size-4 shrink-0 text-fg-secondary" aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-fg">{e.name}</span>
                  {where.length > 0 && (
                    <Badge tone="green" className="hidden shrink-0 sm:inline-flex">
                      In {where.join(", ")}
                    </Badge>
                  )}
                  <span className="tabular w-20 shrink-0 text-right text-[12px] text-fg-muted">
                    {e.dir ? "" : bytes(e.size)}
                  </span>
                  <span className="hidden w-28 shrink-0 text-[12px] text-fg-muted sm:block">
                    {e.modified ? relative(e.modified) : ""}
                  </span>
                  <span className="w-24 shrink-0 truncate text-[12px] font-medium text-fg-muted">
                    {where.length > 0 && <span className="text-green-dark sm:hidden">In {where.join(", ")}</span>}
                    <span className={where.length > 0 ? "hidden sm:inline" : undefined}>
                      {kind ? KIND_NOTE[kind] : "Folder"}
                    </span>
                  </span>
                </>
              );
              return (
                <li key={e.path} className="border-b border-border">
                  {e.dir ? (
                    <button
                      type="button"
                      onClick={() => open(e.path)}
                      className="flex h-[42px] w-full items-center gap-2.5 px-4 text-left hover:bg-surface md:px-5"
                    >
                      {row}
                    </button>
                  ) : importable(e) ? (
                    <div className="flex h-[42px] items-center gap-2.5 px-4 md:px-5">
                      <Checkbox
                        checked={chosen.has(e.path)}
                        aria-label={`Choose ${e.name}${where.length ? ` (in ${where.join(", ")} already)` : ""}`}
                        onCheckedChange={(on) =>
                          setChosen((c) => {
                            const next = new Set(c);
                            if (on) next.add(e.path);
                            else next.delete(e.path);
                            return next;
                          })
                        }
                      />
                      {row}
                    </div>
                  ) : (
                    <div className="flex h-[42px] items-center gap-2.5 px-4 pl-[46px] md:px-5 md:pl-[48px]">{row}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2.5 border-t border-border px-4 py-3.5 md:px-5">
        <span className="min-w-0 flex-1 text-[13px] leading-snug text-fg-secondary" aria-live="polite">
          {chosen.size
            ? `${plural(chosen.size, "file")} chosen.`
            : path === ""
              ? "Open a folder to import files from it or to watch it."
              : counts.data
                ? `To pick up here and below: ${pickupText(counts.data)}. Choose files to import them now, or watch the folder.`
                : counts.isError
                  ? (counts.error as Error).message
                  : "Counting files…"}
        </span>
        <Button
          size="sm"
          disabled={path === ""}
          disabledReason="Open a folder first"
          onClick={() => setWatchOpen(true)}
        >
          Watch this folder…
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!chosen.size}
          disabledReason="Choose files first"
          onClick={() => setImportOpen(true)}
        >
          {chosen.size ? `Import ${plural(chosen.size, "file")}…` : "Import files…"}
        </Button>
      </div>
      {sid != null && (
        <ImportChosenDialog
          open={importOpen}
          onOpenChange={setImportOpen}
          source={sid}
          paths={[...chosen]}
          defaultNs={namespace ?? namespaces[0] ?? ""}
          namespaces={namespaces}
          onDone={(summary) => {
            setChosen(new Set());
            void qc.invalidateQueries({ queryKey: ["browse", sid, path] });
            void qc.invalidateQueries({ queryKey: ["recordings"] });
            void qc.invalidateQueries({ queryKey: ["jobs"] });
            toast({ title: "Imported from the source", body: summary, tone: "green" });
          }}
        />
      )}
      {sid != null && (
        <WatchDialog
          open={watchOpen}
          onOpenChange={setWatchOpen}
          source={sid}
          path={path}
          files={counts.data?.files}
          defaultNs={namespace ?? namespaces[0] ?? ""}
          namespaces={namespaces}
          onDone={() => {
            void qc.invalidateQueries({ queryKey: ["watches"] });
            toast({
              title: "Watching this folder",
              body: "New files will be imported on their own.",
              tone: "green",
            });
          }}
        />
      )}
    </div>
  );
}

/** Import the chosen files now: into which namespace, and what runs after. */
function ImportChosenDialog({
  open,
  onOpenChange,
  source,
  paths,
  defaultNs,
  namespaces,
  onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  source: number;
  paths: string[];
  defaultNs: string;
  namespaces: string[];
  onDone: (summary: string) => void;
}) {
  const client = useApiClient();
  const [ns, setNs] = useState(defaultNs);
  const [pipelineId, setPipelineId] = useState<number | null>(null);
  const [collectionId, setCollectionId] = useState<number | null>(null);
  const [pdfAs, setPdfAs] = useState<"document" | "transcript">("document");
  const pdfs = paths.filter((p) => kindOf(p) === "document").length;
  const pipeline = useNamespacePipeline(ns || null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setNs(defaultNs);
      setPdfAs("document");
      setError(null);
    }
  }, [open, defaultNs]);
  useEffect(() => setCollectionId(null), [ns]);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Import ${plural(paths.length, "file")}?`}
      description="Audio, video, documents and images stay on the source: they’re copied for processing and shown from there. Transcripts are imported. Files a namespace has from this source already are left as they are."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy || !ns}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const r = await data(
                  Imports.importFromSource({
                    client,
                    body: {
                      source,
                      paths,
                      namespace: ns,
                      pipeline: pipelineId,
                      collection: collectionId,
                      pdf_as: pdfAs,
                    },
                  }),
                );
                onOpenChange(false);
                onDone(sourceImportSummary(r.results));
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Importing…" : `Import ${plural(paths.length, "file")}`}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Into namespace</span>
          <Select value={ns} onChange={(e) => setNs(e.target.value)} options={namespaces} />
        </label>
        <CollectionField
          ns={ns || null}
          value={collectionId}
          onChange={setCollectionId}
          id="source-import-collection"
        />
        {pdfs > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[13px] font-bold text-fg-strong">
              Import {pdfs === 1 ? "the PDF" : `the ${pdfs} PDFs`} as
            </span>
            <Segmented
              label={`Import ${pdfs === 1 ? "the PDF" : "the PDFs"} as`}
              items={[
                { value: "document", label: "Documents" },
                { value: "transcript", label: "Transcripts" },
              ]}
              value={pdfAs}
              onChange={(v) => setPdfAs(v as "document" | "transcript")}
            />
            <span className="text-[12.5px] text-fg-muted">
              {pdfAs === "document"
                ? "Their pages, to look at and search; scans are read by OCR."
                : "Only their text, as transcripts without media."}
            </span>
          </div>
        )}
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Then run</span>
          <Select
            value={pipelineId == null ? "" : String(pipelineId)}
            onChange={(e) => setPipelineId(e.target.value ? Number(e.target.value) : null)}
            options={pipelineOptions(pipeline.name, pipeline.pipelines)}
          />
        </label>
        {error && (
          <p className="text-[13px] text-red-dark" role="alert">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function WatchDialog({
  open,
  onOpenChange,
  source,
  path,
  files,
  defaultNs,
  namespaces,
  onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  source: number;
  path: string;
  files?: number;
  defaultNs: string;
  namespaces: string[];
  onDone: () => void;
}) {
  const client = useApiClient();
  const [ns, setNs] = useState(defaultNs);
  const [backfill, setBackfill] = useState(true);
  const [poll, setPoll] = useState("15");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setNs(defaultNs);
      setError(null);
    }
  }, [open, defaultNs]);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Watch this folder?"
      description={
        <>
          New files in <b className="font-mono text-[13px] text-fg">{path}</b> are imported on their own and run the
          namespace’s pipeline.
        </>
      }
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy || !ns}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await data(
                  Sources.createWatch({
                    client,
                    body: {
                      source,
                      path,
                      namespace: ns,
                      backfill,
                      poll_minutes: Number(poll),
                    },
                  }),
                );
                onOpenChange(false);
                onDone();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Saving…" : "Watch folder"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Into namespace</span>
          <Select value={ns} onChange={(e) => setNs(e.target.value)} options={namespaces} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-bold text-fg-strong">Look for new files</span>
          <Select
            value={poll}
            onChange={(e) => setPoll(e.target.value)}
            options={[
              { value: "5", label: "Every 5 minutes" },
              { value: "15", label: "Every 15 minutes" },
              { value: "60", label: "Every hour" },
              { value: "1440", label: "Once a day" },
            ]}
          />
        </label>
        <Checkbox
          checked={backfill}
          onCheckedChange={setBackfill}
          label={
            files != null
              ? `Also import the ${plural(files, "file")} already there`
              : "Also import what’s already there"
          }
        />
        {error && (
          <p className="text-[13px] text-red-dark" role="alert">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
