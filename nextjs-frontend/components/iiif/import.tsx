"use client";

import { useMutation } from "@tanstack/react-query";
import { Download, FolderOpen } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Iiif } from "@/app/openapi-client";
import { formatExt } from "@/components/iiif/iiif-model";
import { rightsShort } from "@/components/iiif/rights";
import { CollectionField } from "@/components/library/collections-ui";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select } from "@/components/ui/field";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState, PageHeader } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count, plural, tc } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type Canvas = {
  canvas: string;
  label?: string | null;
  duration?: number | null;
  audio?: {
    id?: string;
    format?: string;
    type?: string;
    duration?: number;
  } | null;
  captions: { id?: string; language?: string | string[] | null }[];
};
type ManifestPreview = {
  type: "Manifest";
  id: string;
  label?: string | null;
  rights?: string | null;
  navDate?: string | null;
  items: Canvas[];
};
type CollectionPreview = {
  type: "Collection";
  id: string;
  label?: string | null;
  /** Manifests found, in the Collection and the Collections inside it. */
  total: number;
  /** How many Collections inside it were read. */
  collections?: number;
  /** It stopped looking (too deep, too many Collections or Manifests). */
  more?: boolean;
  /** Its Manifests in order (the first 200); `path` names the Collections each is in below this one. */
  items: { id: string; type: string; label?: string | null; path?: string[] }[];
};
type Preview = ManifestPreview | CollectionPreview;

/** Above this many items, starting the import asks you to type the count. */
const TYPED_OVER = 100;

/** The design's error cards, from the backend's refusal text. */
export function importProblem(message: string): {
  tag: string;
  title: string;
  body: string;
} {
  if (/version 2|presentation\/2/i.test(message))
    return {
      tag: "Version 2.x",
      title: "This is a Presentation 2 Manifest",
      body: "Version 2 Manifests carry images only, so there’s nothing to listen to. Look for a Presentation 3 version of it on the source site.",
    };
  if (/\b(401|403)\b|unauthori[sz]ed|forbidden/i.test(message))
    return {
      tag: "Access denied",
      title: "The source asks for sign-in",
      body: "It protects this resource with IIIF auth, which importing can’t pass yet. Ask the source for an open Manifest.",
    };
  if (/isn['’]t a IIIF|not a IIIF|JSON/i.test(message))
    return {
      tag: "Not IIIF",
      title: "That address isn’t a IIIF Manifest or Collection",
      body: "It returned something else, such as an HTML page. Look for a “IIIF” link or logo on the source site and paste that address instead.",
    };
  if (/http\(s\)|use an http/i.test(message))
    return {
      tag: "Address",
      title: "Use an http(s) address",
      body: "Paste the full address of a IIIF Manifest or Collection, starting with https://.",
    };
  return {
    tag: "Can’t read it",
    title: "The source couldn’t be read",
    body: message,
  };
}

const langs = (l: Canvas["captions"][number]["language"]) => (Array.isArray(l) ? l.join(", ") : l || "");

/** Import from another IIIF archive (IP4): preview a Manifest or Collection, choose options, import in the background. */
export function IiifImport() {
  const client = useApiClient();
  const { admin, namespaces } = useArchive();
  const [url, setUrl] = useState("");
  const [ns, setNs] = useState("");
  const [collectionId, setCollectionId] = useState<number | null>(null);
  useEffect(() => setCollectionId(null), [ns]);
  const [keep, setKeep] = useState(true);
  const [limit, setLimit] = useState("50");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");

  const preview = useMutation({
    mutationFn: (u: string) =>
      data(Iiif.previewIiifImport({ client, body: { url: u } })) as Promise<unknown> as Promise<Preview>,
  });
  const start = useMutation({
    mutationFn: () =>
      data(
        Iiif.importIiif({
          client,
          body: {
            url: url.trim(),
            namespace: ns.trim(),
            collection: collectionId,
            keep_transcripts: keep,
            limit: Number(limit) || 50,
            wait: false,
          },
        }),
      ),
    onSuccess: () => setConfirming(false),
  });

  if (!admin)
    return (
      <div className="px-4 py-5 sm:px-6">
        <PageHeader title="Import from IIIF" />
        <EmptyState
          icon={<Download />}
          title="Admins import from other archives"
          actions={
            <Button asChild>
              <Link href="/iiif">Back to Collections</Link>
            </Button>
          }
        >
          Importing from IIIF is a source, so only platform admins can start one. Ask an admin if you need recordings
          from another archive.
        </EmptyState>
      </div>
    );

  const p = preview.data;
  const canvases = p?.type === "Manifest" ? p.items : [];
  const usable = canvases.filter((c) => c.audio?.id || c.captions.length);
  const collectionItems = p?.type === "Collection" ? p.items : [];
  const manifests = collectionItems.filter((i) => i.type === "Manifest");
  const lim = Number(limit);
  const limitError = !Number.isInteger(lim) || lim < 1 || lim > 1000 ? "Use a number from 1 to 1000" : null;
  const itemCount = p?.type === "Collection" ? Math.min(manifests.length, lim || 0) : usable.length;
  const nsError =
    ns.trim() && !/^[a-z0-9][a-z0-9_-]{0,40}$/.test(ns.trim()) ? "Lowercase letters, digits, - and _" : null;
  const noAudio = p?.type === "Manifest" && usable.length === 0;
  const problem = preview.error
    ? importProblem(preview.error instanceof ApiError ? preview.error.message : String(preview.error))
    : null;
  const ready = Boolean(p) && !noAudio && itemCount > 0 && ns.trim() && !nsError && !limitError;
  const confirmText = `IMPORT ${itemCount}`;

  return (
    <div className="px-4 py-5 sm:px-6">
      <PageHeader title="Import from IIIF" meta="Audio, captions and metadata from another archive" />
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,900px)_380px]">
        <section className="flex flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-[22px]">
          <form
            className="flex flex-wrap items-end gap-2.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (url.trim()) {
                start.reset();
                preview.mutate(url.trim());
              }
            }}
          >
            <Field label="IIIF Manifest or Collection URL" className="min-w-[240px] flex-1">
              {(f) => (
                <Input
                  id={f.id}
                  mono
                  type="url"
                  placeholder="https://…/manifest.json"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              )}
            </Field>
            <Button type="submit" disabled={!url.trim() || preview.isPending}>
              {preview.isPending ? "Reading…" : "Preview"}
            </Button>
          </form>

          {p && (
            <div className="flex flex-wrap items-center gap-3.5 rounded-md border border-border bg-surface px-3.5 py-3 text-[13px]">
              <span className="flex min-w-0 flex-1 flex-col gap-1">
                <b className="text-[15px] font-bold leading-tight">{p.label || "Untitled"}</b>
                <span className="text-fg-secondary">
                  {p.type} · Presentation 3.0
                  {p.type === "Manifest" && p.rights ? ` · ${rightsShort(p.rights)}` : ""}
                </span>
              </span>
              <span className="tabular flex gap-4 text-[15px] font-bold">
                {p.type === "Collection" ? (
                  <span>
                    {count(p.total)} <span className="text-[11.5px] font-normal text-fg-muted">items</span>
                  </span>
                ) : (
                  <>
                    <span>
                      {count(canvases.length)} <span className="text-[11.5px] font-normal text-fg-muted">items</span>
                    </span>
                    <span>{tc(canvases.reduce((a, c) => a + (c.duration ?? c.audio?.duration ?? 0), 0) * 1000)}</span>
                    <span>
                      {count(canvases.filter((c) => c.captions.length).length)}{" "}
                      <span className="text-[11.5px] font-normal text-fg-muted">with captions</span>
                    </span>
                  </>
                )}
              </span>
            </div>
          )}

          {p && (
            <div className="overflow-hidden rounded-md border border-border">
              <Table aria-label="What would be imported" className="text-[12.5px]">
                <THead className="border-t-0">
                  <tr>
                    <Th className="w-8">
                      <span className="sr-only">Imported</span>
                    </Th>
                    <Th>Item</Th>
                    {p.type === "Manifest" ? (
                      <>
                        <Th>Duration</Th>
                        <Th>Audio</Th>
                        <Th>Transcript</Th>
                      </>
                    ) : (
                      <Th>Kind</Th>
                    )}
                  </tr>
                </THead>
                <tbody>
                  {p.type === "Manifest"
                    ? canvases.map((c, i) => {
                        const ok = Boolean(c.audio?.id || c.captions.length);
                        return (
                          <Tr key={c.canvas ?? i} className={cn("h-[38px]", !ok && "text-fg-muted")}>
                            <Td>
                              <Checkbox checked={ok} disabled aria-label={ok ? "Imported" : "Not imported"} />
                            </Td>
                            <Td className="max-w-[320px] truncate font-semibold">
                              {c.label || p.label || `Item ${i + 1}`}
                            </Td>
                            <Td className="tabular">
                              {c.duration || c.audio?.duration
                                ? tc((c.duration ?? c.audio?.duration ?? 0) * 1000)
                                : "—"}
                            </Td>
                            <Td>
                              <code className="font-mono text-[11.5px]">
                                {c.audio?.id ? `${formatExt(c.audio.format)} · copied here` : "no audio"}
                              </code>
                            </Td>
                            <Td>
                              {c.captions.length
                                ? `WebVTT${langs(c.captions[0].language) ? ` · ${langs(c.captions[0].language)}` : ""}`
                                : "none"}
                            </Td>
                          </Tr>
                        );
                      })
                    : collectionItems.slice(0, 200).map((it, i) => {
                        const ok = it.type === "Manifest" && manifests.indexOf(it) < (lim || 0);
                        return (
                          <Tr key={`${it.id}-${i}`} className={cn("h-[38px]", !ok && "text-fg-muted")}>
                            <Td>
                              <Checkbox checked={ok} disabled aria-label={ok ? "Imported" : "Not imported"} />
                            </Td>
                            <Td className="max-w-[420px] truncate font-semibold">
                              {it.path?.length ? (
                                <span className="font-normal text-fg-secondary">{it.path.join(" › ")} › </span>
                              ) : null}
                              {it.label ?? it.id}
                            </Td>
                            <Td className="text-fg-secondary">{ok ? "Manifest" : "Manifest · over the limit"}</Td>
                          </Tr>
                        );
                      })}
                </tbody>
              </Table>
              {p.type === "Collection" && (p.total > 200 || Boolean(p.collections) || p.more) && (
                <p className="border-t border-border px-3 py-2 text-[12px] text-fg-secondary">
                  {[
                    p.total > 200 ? `Showing the first 200 of ${count(p.total)} Manifests.` : null,
                    p.collections
                      ? `Found in it and ${plural(p.collections, "Collection")} inside it; they all go into the collection you choose.`
                      : null,
                    p.more
                      ? "It holds more than Lens reads at once: import the Collections inside it one by one."
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" ")}
                </p>
              )}
            </div>
          )}

          {noAudio && (
            <Banner tone="warning" title="Nothing here to listen to.">
              None of its {canvases.length} items has audio or captions. Lens imports audio (and audio with
              transcripts) only.
            </Banner>
          )}

          {p && !noAudio && (
            <>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field
                  label="Namespace"
                  hint={
                    nsError
                      ? undefined
                      : namespaces.some((n) => n.name === ns.trim()) || !ns.trim()
                        ? undefined
                        : "A new namespace is created"
                  }
                  error={nsError}
                >
                  {(f) => (
                    <>
                      <Input
                        id={f.id}
                        aria-describedby={f.describedBy}
                        invalid={f.invalid}
                        list="iiif-ns"
                        value={ns}
                        onChange={(e) => setNs(e.target.value)}
                        placeholder="research-interviews"
                      />
                      <datalist id="iiif-ns">
                        {namespaces.map((n) => (
                          <option key={n.name} value={n.name} />
                        ))}
                      </datalist>
                    </>
                  )}
                </Field>
                <CollectionField
                  ns={ns.trim() && !nsError ? ns.trim() : null}
                  value={collectionId}
                  onChange={setCollectionId}
                  id="iiif-collection"
                />
                <Field label="Audio" hint="Playing from the origin isn’t available yet">
                  {(f) => (
                    <Select
                      id={f.id}
                      value="copy"
                      onChange={() => undefined}
                      options={[
                        { value: "copy", label: "Copy audio here" },
                        {
                          value: "origin",
                          label: "Play from origin (not available)",
                          disabled: true,
                        },
                      ]}
                    />
                  )}
                </Field>
                <Field label="Transcripts">
                  {(f) => (
                    <Select
                      id={f.id}
                      value={keep ? "keep" : "redo"}
                      onChange={(e) => setKeep(e.target.value === "keep")}
                      options={[
                        {
                          value: "keep",
                          label: "Keep captions, transcribe the rest",
                        },
                        { value: "redo", label: "Transcribe everything" },
                      ]}
                    />
                  )}
                </Field>
              </div>
              <div className="flex flex-wrap items-end gap-3">
                {p.type === "Collection" && (
                  <Field label="Import the first" hint="Manifests, in the Collection’s order" error={limitError}>
                    {(f) => (
                      <Input
                        id={f.id}
                        aria-describedby={f.describedBy}
                        invalid={f.invalid}
                        inputMode="numeric"
                        value={limit}
                        onChange={(e) => setLimit(e.target.value)}
                        className="w-[120px]"
                      />
                    )}
                  </Field>
                )}
                <span className="flex-1" />
                <Checkbox
                  checked={false}
                  disabled
                  label="Re-harvest when the source’s change feed updates (not available yet)"
                />
                <Button
                  variant="primary"
                  disabled={!ready || start.isPending}
                  disabledReason={!ready ? (ns.trim() ? "Fix the options first" : "Choose a namespace") : undefined}
                  onClick={() => setConfirming(true)}
                >
                  Double-check {count(itemCount)} item
                  {itemCount === 1 ? "" : "s"}
                </Button>
              </div>
            </>
          )}

          {start.isSuccess && (
            <Banner tone="success" title="Importing in the background.">
              New recordings appear in{" "}
              <Link className="font-semibold underline" href="/library">
                the Library
              </Link>{" "}
              as they arrive, and {ns.trim()}’s pipeline runs on each (skipping transcription where captions were kept).
              Follow it in{" "}
              <Link className="font-semibold underline" href="/activity">
                Activity
              </Link>
              .
            </Banner>
          )}
          {start.isError && <Banner tone="error">{start.error.message}</Banner>}
          {!p && !problem && !preview.isPending && (
            <EmptyState icon={<FolderOpen />} title="Paste a IIIF address to see what it holds">
              A Presentation 3 Manifest or Collection with audio. Captions (WebVTT) become the transcript, and the
              metadata is mapped.
            </EmptyState>
          )}
        </section>

        <aside className="flex flex-col gap-2.5" aria-live="polite">
          {problem && (
            <div
              className={cn(
                "flex flex-col gap-1.5 rounded-md border bg-background p-3.5",
                problem.tag === "Version 2.x" ? "border-gold-border" : "border-red-border",
              )}
              role="alert"
            >
              <span className="font-mono text-[11px] font-semibold text-fg-muted">{problem.tag}</span>
              <b className="text-[14px] font-bold leading-[1.3]">{problem.title}</b>
              <span className="text-[12.5px] leading-[1.45] text-fg-secondary">{problem.body}</span>
            </div>
          )}
          <div className="flex flex-col gap-1.5 rounded-md border border-border bg-background p-3.5 text-[12.5px] leading-[1.45] text-fg-secondary">
            <b className="text-[13px] font-bold text-fg">What happens</b>
            <span>
              Audio is copied into the archive. WebVTT captions become the transcript, with their speakers. Label,
              summary, rights, attribution, date and metadata pairs are kept, and a link back to the source is added.
            </span>
            <span>
              Items already imported into the namespace are skipped, so running it again only adds what’s new.
            </span>
          </div>
        </aside>
      </div>

      <Dialog
        open={confirming}
        onOpenChange={(o) => !o && (setConfirming(false), setTyped(""))}
        title={`Import ${count(itemCount)} item${itemCount === 1 ? "" : "s"} into ${ns.trim()}?`}
        description={`From “${p?.label ?? url}”. ${keep ? "Captions are kept as transcripts; the rest are transcribed." : "Everything is transcribed here."} The namespace’s pipeline runs on each.`}
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Back
            </Button>
            <Button
              variant="primary"
              disabled={start.isPending || (itemCount > TYPED_OVER && typed.trim() !== confirmText)}
              disabledReason={
                itemCount > TYPED_OVER && typed.trim() !== confirmText ? `Type ${confirmText} to confirm` : undefined
              }
              onClick={() => start.mutate()}
            >
              {start.isPending ? "Starting…" : "Start import"}
            </Button>
          </>
        }
      >
        {itemCount > TYPED_OVER && (
          <Field label={`Type ${confirmText} to confirm`}>
            {(f) => (
              <Input id={f.id} mono value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
            )}
          </Field>
        )}
      </Dialog>
    </div>
  );
}
