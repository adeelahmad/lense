"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileAudio, FolderHeart, FolderOpen, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Collections, Metadata } from "@/app/openapi-client";
import { useSaveAccess } from "@/components/access/hooks";
import { ALL_PARTS, PARTS, partsText, togglePart, type AccessPart } from "@/components/access/model";
import { BulkEditDialog } from "@/components/iiif/bulk-edit";
import { PAGE, useCollectionItems, type CollectionItem } from "@/components/iiif/collection-data";
import { CopyButton, usePublicIiif } from "@/components/iiif/collections";
import { IiifPanel } from "@/components/iiif/iiif-panel";
import { first, PUBLISH_BADGE, withLang, type Meta } from "@/components/iiif/metadata-model";
import { keys, useNamespaceMeta } from "@/components/iiif/queries";
import { RIGHTS } from "@/components/iiif/rights";
import { SegmentedChoice } from "@/components/settings/controls";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog, Drawer } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { EmptyState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Pagination } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, shortDate } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

type IiifCollection = { id: string; items?: { id: string }[] };
type Activity = { id: string; totalItems: number };

/** A namespace's Collection (IP3): its URLs, validation across its Manifests, and every recording's publish state. */
export function CollectionPage({ ns }: { ns: string }) {
  const { namespaces, can } = useArchive();
  const client = useApiClient();
  const known = namespaces.find((n) => n.name === ns);
  const nsMeta = useNamespaceMeta(ns);
  const [offset, setOffset] = useState(0);
  const { list, total, items, loadingMeta } = useCollectionItems(ns, offset, nsMeta.data?.profile);
  const root = usePublicIiif<IiifCollection>("/iiif/collection");
  const collection = usePublicIiif<IiifCollection>(`/iiif/collection/${ns}`, Boolean(known));
  const feed = usePublicIiif<Activity>("/iiif/discovery/activity");
  const saved = useQuery({
    queryKey: ["collections"],
    queryFn: () => data(Collections.listCollections({ client })),
    staleTime: 60_000,
  });
  const [open, setOpen] = useState<CollectionItem | null>(null);
  const [publishing, setPublishing] = useState<CollectionItem | null>(null);
  const [bulk, setBulk] = useState<null | "edit" | "unpublish" | "publish">(null);
  const [editMeta, setEditMeta] = useState(false);

  if (namespaces.length && !known)
    return (
      <EmptyState
        icon={<FolderOpen />}
        title="This collection doesn’t exist"
        actions={
          <Button asChild>
            <Link href="/iiif">All collections</Link>
          </Button>
        }
      >
        The namespace may have been renamed, or you don’t have a role in it. Namespaces you don’t belong to are never
        shown here.
      </EmptyState>
    );

  const isPublic = (root.data?.items ?? []).some((c) => c.id.endsWith(`/collection/${ns}`));
  const collectionUrl = collection.data?.id ?? root.data?.id?.replace(/\/collection$/, `/collection/${ns}`) ?? "";
  const checked = items.filter((i) => i.state);
  const ok = checked.filter((i) => i.state === "published" || i.state === "unpublished").length;
  const attention = checked.filter((i) => i.state === "attention").length;
  const blocked = checked.filter((i) => i.state === "draft").length;
  const isOwner = can("owner", ns);
  const isEditor = can("editor", ns);
  const ownerReason = needRole("owner", ns);
  const mySaved = (saved.data ?? []).filter((c) => {
    const nss = (c.filter as { namespaces?: string[] } | null | undefined)?.namespaces;
    return !nss || nss.includes(ns);
  });

  return (
    <div className="px-4 py-5 sm:px-6">
      <section className="flex flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex min-w-0 flex-1 basis-full flex-col gap-1 sm:basis-auto">
            <Link href="/iiif" className="text-[12px] font-medium text-fg-muted hover:underline">
              Collections
            </Link>
            <span className="flex flex-wrap items-center gap-2">
              <h1 className="text-[22px] font-bold leading-[1.2] text-fg">{ns}</h1>
              <Badge tone={isPublic ? "green" : "neutral"} dot>
                {isPublic ? "Published" : "Not published"}
              </Badge>
              <span className="text-[12.5px] text-fg-muted">
                {isPublic ? "part of the public top-level Collection" : "nothing here is public yet"}
              </span>
            </span>
          </div>
          <Button size="sm" onClick={() => setEditMeta(true)} disabled={!isOwner} disabledReason={ownerReason}>
            Edit metadata
          </Button>
          <Button
            size="sm"
            onClick={() => setBulk(isPublic ? "unpublish" : "publish")}
            disabled={!isOwner}
            disabledReason={ownerReason}
          >
            {isPublic ? "Unpublish" : "Publish all"}
          </Button>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <div className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border px-3.5 py-3">
            <b className="text-[12px] font-bold text-fg-secondary">Collection</b>
            {collectionUrl ? (
              <>
                <code className="break-all font-mono text-[12px] leading-[1.4]">{collectionUrl}</code>
                <CopyButton text={collectionUrl} label="Collection URL" />
              </>
            ) : root.isError ? (
              <span className="text-[12.5px] text-red-dark">{root.error.message}</span>
            ) : (
              <Skeleton className="h-4 w-4/5" />
            )}
          </div>
          <div className="flex min-w-0 flex-col gap-1.5 rounded-md border border-border px-3.5 py-3">
            <b className="text-[12px] font-bold text-fg-secondary">Change feed for harvesters</b>
            {feed.data ? (
              <>
                <code className="break-all font-mono text-[12px] leading-[1.4]">{feed.data.id}</code>
                <span className="text-[12px] text-fg-muted">
                  IIIF Change Discovery · {count(feed.data.totalItems)} event
                  {feed.data.totalItems === 1 ? "" : "s"} (whole archive)
                </span>
              </>
            ) : feed.isError ? (
              <span className="text-[12.5px] text-red-dark">{feed.error.message}</span>
            ) : (
              <Skeleton className="h-4 w-4/5" />
            )}
          </div>
          <div
            className={cn(
              "flex min-w-0 flex-col gap-1.5 rounded-md border px-3.5 py-3",
              attention || blocked ? "border-gold-border bg-gold-surface" : "border-border",
            )}
          >
            <b className="text-[12px] font-bold text-fg-secondary">
              Validation across {count(checked.length)} Manifest
              {checked.length === 1 ? "" : "s"}
              {total > PAGE ? " on this page" : ""}
            </b>
            {list.isPending || (loadingMeta && !checked.length) ? (
              <>
                <Skeleton className="h-5 w-28" />
                <Skeleton className="h-3 w-3/5" />
              </>
            ) : (
              <>
                <span className="tabular flex gap-3.5 text-[16px] font-bold">
                  <span className="text-green-dark">✓ {ok}</span>
                  <span className="text-gold-dark">◆ {blocked}</span>
                  <span className="text-red-dark">✕ {attention}</span>
                </span>
                <span className="text-[12px] leading-[1.3] text-fg-secondary">
                  {attention ? `${attention} published with problems` : "No published Manifest has problems"}
                  {blocked ? ` · ${blocked} can’t be published until fixed` : ""}
                </span>
              </>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[12.5px] font-semibold text-fg-secondary">Order and group</span>
          <SegmentedChoice
            label="Order and group"
            value="date"
            onChange={() => undefined}
            options={[
              {
                value: "series",
                label: "By series",
                disabled: true,
                reason: "Not available yet: recordings have no series field",
              },
              { value: "date", label: "By date" },
              {
                value: "manual",
                label: "Manual",
                disabled: true,
                reason: "Not available yet: Collections are ordered by date",
              },
            ]}
          />
          <span className="flex-1" />
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setBulk("edit")}
            disabled={!isEditor}
            disabledReason={needRole("editor", ns)}
          >
            Bulk edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<Upload />}
            disabled
            disabledReason="CSV import isn’t available yet: the server can’t match CSV rows to recordings by identifier"
          >
            Import metadata CSV
          </Button>
          <Button asChild size="sm" variant="ghost">
            <Link href={`/iiif/collections/${ns}/profile`}>Metadata profile</Link>
          </Button>
        </div>

        <div className="overflow-hidden rounded-md border border-border">
          {list.isPending ? (
            <SkeletonRows rows={4} />
          ) : list.isError ? (
            <EmptyState
              tone="error"
              icon={<FolderOpen />}
              title="Couldn’t load the recordings"
              actions={<Button onClick={() => list.refetch()}>Try again</Button>}
            >
              {list.error.message}
            </EmptyState>
          ) : !total ? (
            <EmptyState icon={<FileAudio />} title="No recordings in this namespace yet">
              Import or connect a source; each recording becomes a Manifest in this Collection.
            </EmptyState>
          ) : (
            <ul aria-label={`Recordings in ${ns}`} aria-busy={loadingMeta || undefined}>
              {items.map((it) => (
                <ItemRow
                  key={it.id}
                  item={it}
                  canEdit={isEditor}
                  canPublish={isOwner}
                  ownerReason={ownerReason}
                  onOpen={() => setOpen(it)}
                  onPublish={() => setPublishing(it)}
                />
              ))}
              {mySaved.map((c) => (
                <li
                  key={`c${c.id}`}
                  className="grid grid-cols-[18px_minmax(0,1fr)_auto_auto] items-center gap-2.5 border-t border-border px-3.5 py-2.5 text-[13px] first:border-t-0 sm:grid-cols-[18px_minmax(0,1fr)_140px_90px]"
                >
                  <FolderHeart aria-hidden className="size-[15px] text-fg-secondary" />
                  <span className="flex min-w-0 flex-col items-start gap-0.5 sm:flex-row sm:items-center sm:gap-2">
                    <b className="max-w-full truncate font-bold">Saved collection · {c.name}</b>
                    <span className="text-[12px] text-fg-muted sm:whitespace-nowrap">
                      sub-collection · {count(c.count)}
                      {c.kind === "filter" ? " · updates with filters" : ""}
                    </span>
                  </span>
                  <Badge tone="neutral" dot>
                    Not published
                  </Badge>
                  <Button
                    variant="link"
                    size="xs"
                    disabled
                    disabledReason="Not available yet: saved collections aren’t published as IIIF Collections"
                    className="justify-self-end"
                  >
                    Publish
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {total > PAGE && (
            <Pagination
              offset={offset}
              limit={PAGE}
              total={total}
              onChange={setOffset}
              loading={loadingMeta}
              className="border-t border-border"
            />
          )}
        </div>
      </section>

      <Drawer open={Boolean(open)} onOpenChange={(o) => !o && setOpen(null)} title={open?.title ?? "IIIF"} width={488}>
        {open && (
          <>
            <div className="flex gap-3 border-b border-border px-[18px] py-2.5 text-[12.5px]">
              <Link href={`/resources/${open.id}`} className="font-semibold text-fg-accent hover:underline">
                Open recording
              </Link>
              <Link href={`/iiif/metadata/${open.id}`} className="font-semibold text-fg-accent hover:underline">
                Edit metadata
              </Link>
            </div>
            <IiifPanel recordingId={open.id} />
          </>
        )}
      </Drawer>
      <PublishDialog item={publishing} onClose={() => setPublishing(null)} />
      <BulkEditDialog ns={ns} open={bulk === "edit"} onClose={() => setBulk(null)} canPublish={isOwner} />
      <BulkEditDialog
        ns={ns}
        open={bulk === "unpublish" || bulk === "publish"}
        onClose={() => setBulk(null)}
        preset={
          bulk === "publish"
            ? {
                field: "access",
                value: "public",
                title: `Publish every recording in ${ns}?`,
              }
            : { field: "access", value: "private", title: `Unpublish ${ns}?` }
        }
      />
      {editMeta && <CollectionMetaDialog ns={ns} onClose={() => setEditMeta(false)} meta={nsMeta.data?.meta} />}
    </div>
  );
}

function ItemRow({
  item,
  canEdit,
  canPublish,
  ownerReason,
  onOpen,
  onPublish,
}: {
  item: CollectionItem;
  canEdit: boolean;
  canPublish: boolean;
  ownerReason: string;
  onOpen: () => void;
  onPublish: () => void;
}) {
  const badge = item.state ? PUBLISH_BADGE[item.state] : null;
  let action = null;
  if (item.state === "attention" || item.state === "draft")
    action = canEdit ? (
      <Button asChild variant="link" size="xs">
        <Link href={`/iiif/metadata/${item.id}`}>Fix</Link>
      </Button>
    ) : null;
  else if (item.state === "unpublished")
    action = (
      <Button variant="link" size="xs" onClick={onPublish} disabled={!canPublish} disabledReason={ownerReason}>
        Publish
      </Button>
    );
  else if (item.state)
    action = (
      <Button variant="link" size="xs" onClick={onOpen}>
        Open
      </Button>
    );
  return (
    <li
      className={cn(
        "grid grid-cols-[18px_minmax(0,1fr)_auto_auto] items-center gap-2.5 border-t border-border px-3.5 py-2.5 text-[13px] first:border-t-0 sm:grid-cols-[18px_minmax(0,1fr)_140px_90px]",
        item.state === "attention" && "bg-hl",
      )}
    >
      <FileAudio aria-hidden className="size-[15px] text-fg-secondary" />
      <span className="flex min-w-0 flex-col items-start gap-0.5 sm:flex-row sm:items-center sm:gap-2">
        <button
          type="button"
          onClick={onOpen}
          className="max-w-full truncate text-left font-medium text-fg hover:underline"
        >
          {item.title}
        </button>
        <span className="whitespace-nowrap text-[12px] text-fg-muted">{shortDate(item.recorded_at)}</span>
      </span>
      <span>
        {badge ? (
          <Badge tone={badge.tone} dot>
            {badge.label}
          </Badge>
        ) : (
          <span className="skeleton block h-[22px] w-24 rounded-pill" />
        )}
      </span>
      <span className="justify-self-end">{action}</span>
    </li>
  );
}

/** Publish one recording: make it public, and choose the parts anyone may use. */
function PublishDialog({ item, onClose }: { item: CollectionItem | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<AccessPart[]>(ALL_PARTS);
  const publish = useSaveAccess(item?.id ?? 0);
  useEffect(() => {
    if (item) setOpen(ALL_PARTS);
  }, [item]);
  return (
    <Dialog
      open={Boolean(item)}
      onOpenChange={(o) => !o && (publish.reset(), onClose())}
      title={`Publish “${item?.title ?? ""}”?`}
      description="It becomes public: anyone finds it, its Manifest goes live at its IIIF address, and harvesters see it in the change feed."
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={publish.isPending}
            onClick={() =>
              item &&
              publish.mutate(
                { access: "public", open },
                {
                  onSuccess: () => {
                    void qc.invalidateQueries({ queryKey: ["iiif-public"] });
                    onClose();
                  },
                },
              )
            }
          >
            {publish.isPending ? "Publishing…" : "Publish"}
          </Button>
        </>
      }
    >
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1.5 text-[13px] font-bold text-fg-strong">Open to everyone</legend>
        {PARTS.map((p) => (
          <Checkbox
            key={p.value}
            checked={open.includes(p.value)}
            onCheckedChange={(on) => setOpen((cur) => togglePart(cur, p.value, on))}
            label={`${p.label} (${p.hint})`}
          />
        ))}
      </fieldset>
      <p className="text-[12.5px] leading-[1.4] text-fg-secondary">
        {open.length
          ? `${partsText(open)} open to everyone; the rest asks people to sign in with access.`
          : "Its page and description only; everything else asks people to sign in with access."}
      </p>
      {publish.isError && <Banner tone="error">{publish.error.message}</Banner>}
    </Dialog>
  );
}

/** The Collection's own label, summary, rights, attribution and provider (owners). */
function CollectionMetaDialog({ ns, onClose, meta }: { ns: string; onClose: () => void; meta?: Meta }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const lang = Object.keys(meta?.label ?? meta?.summary ?? {})[0] ?? "none";
  const [label, setLabel] = useState(first(meta?.label));
  const [summary, setSummary] = useState(first(meta?.summary));
  const [rights, setRights] = useState(meta?.rights ?? "");
  const [attribution, setAttribution] = useState(first(meta?.attribution));
  const [provider, setProvider] = useState(meta?.provider?.name ?? "");
  const save = useMutation({
    mutationFn: () =>
      data(
        Metadata.updateNamespaceMetadata({
          client,
          path: { name: ns },
          body: {
            meta: {
              label: withLang(meta?.label, lang, label),
              summary: withLang(meta?.summary, lang, summary),
              rights: rights || null,
              attribution: withLang(meta?.attribution, lang, attribution),
              provider: provider ? { ...(meta?.provider ?? {}), name: provider } : null,
            },
          },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.namespace(ns) });
      void qc.invalidateQueries({ queryKey: ["iiif-public"] });
      toast({ title: "Collection metadata saved", tone: "green" });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${ns} · Collection metadata`}
      description="What IIIF viewers show for this Collection. Recording metadata is edited per recording."
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <Field label="Label" hint={`Defaults to the namespace name, “${ns}”`}>
        {(f) => (
          <Input id={f.id} aria-describedby={f.describedBy} value={label} onChange={(e) => setLabel(e.target.value)} />
        )}
      </Field>
      <Field label="Summary">
        {(f) => <Textarea id={f.id} rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} />}
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Rights">
          {(f) => (
            <Select
              id={f.id}
              value={rights}
              onChange={(e) => setRights(e.target.value)}
              options={[{ value: "", label: "None" }, ...RIGHTS.map((r) => ({ value: r.uri, label: r.code }))]}
            />
          )}
        </Field>
        <Field label="Required attribution">
          {(f) => <Input id={f.id} value={attribution} onChange={(e) => setAttribution(e.target.value)} />}
        </Field>
      </div>
      <Field label="Provider">
        {(f) => (
          <Input
            id={f.id}
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="Organisation name"
          />
        )}
      </Field>
      {save.isError && <Banner tone="error">{save.error.message}</Banner>}
    </Dialog>
  );
}
