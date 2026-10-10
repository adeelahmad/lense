"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Link2, RefreshCw, Store, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import { Extensions } from "@/app/openapi-client";
import type { ExtensionImportPreview, Marketplace, MarketplacePlugin } from "@/app/openapi-client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, SearchInput } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

const ITEM_LABEL: Record<string, string> = { skill: "Skill", tool: "Tool", hook: "Hook" };

/** A file, as base64 for the API. */
function base64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

type Pending =
  | { kind: "plugin"; market: number; name: string }
  | { kind: "link"; url: string }
  | { kind: "file"; filename: string; data: string };

/** What installing would add, and what Lens leaves out; installs on yes. */
function PreviewDialog({ pending, onClose }: { pending: Pending | null; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const preview = useQuery({
    queryKey: ["import-preview", pending],
    enabled: pending !== null,
    retry: false,
    queryFn: (): Promise<ExtensionImportPreview> => {
      const p = pending!;
      if (p.kind === "plugin")
        return data(Extensions.previewMarketplacePlugin({ client, path: { mid: p.market, name: p.name } }));
      const req =
        p.kind === "link"
          ? Extensions.importLink({ client, body: { url: p.url, preview: true } })
          : Extensions.importArchive({ client, body: { filename: p.filename, data: p.data, preview: true } });
      return data(req) as Promise<ExtensionImportPreview>;
    },
  });
  const install = useMutation({
    mutationFn: () => {
      const p = pending!;
      if (p.kind === "plugin")
        return data(Extensions.installMarketplacePlugin({ client, path: { mid: p.market, name: p.name } }));
      return data(
        p.kind === "link"
          ? Extensions.importLink({ client, body: { url: p.url } })
          : Extensions.importArchive({ client, body: { filename: p.filename, data: p.data } }),
      );
    },
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: ["extensions"] });
      void qc.invalidateQueries({ queryKey: ["marketplace-plugins"] });
      const v = (out as { version?: number }).version ?? 1;
      toast({
        tone: "green",
        title: v > 1 ? `Updated to version ${v}` : "Installed",
        body: "It’s yours and private until you share it, and switched on in your conversations.",
      });
      onClose();
    },
    onError: (e) => toast({ tone: "red", title: "Couldn’t install it", body: (e as Error).message }),
  });
  const m = preview.data?.manifest as
    | {
        name?: string;
        title?: string;
        description?: string;
        items?: { kind: string; name: string; description?: string }[];
      }
    | undefined;
  return (
    <Dialog
      open={pending !== null}
      onOpenChange={(o) => !o && onClose()}
      wide
      title={m?.title ?? (pending?.kind === "plugin" ? pending.name : "Install")}
      description={preview.data ? <span className="break-all">From {preview.data.source}</span> : undefined}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!preview.data || install.isPending} onClick={() => install.mutate()}>
            {install.isPending ? "Installing…" : "Install"}
          </Button>
        </>
      }
    >
      {preview.isLoading ? (
        <SkeletonRows rows={3} />
      ) : preview.error ? (
        <p role="alert" className="text-[14px] text-red-dark">
          {(preview.error as Error).message}
        </p>
      ) : m ? (
        <div className="flex flex-col gap-3">
          {m.description && <p className="text-[14px] text-fg-secondary">{m.description}</p>}
          <div>
            <h3 className="mb-1.5 text-[13px] font-bold text-fg-strong">What it adds to the assistant</h3>
            <ul className="flex flex-col gap-1.5">
              {(m.items ?? []).map((it) => (
                <li key={it.name} className="flex items-start gap-2 text-[13.5px]">
                  <Badge tone="intent">{ITEM_LABEL[it.kind] ?? it.kind}</Badge>
                  <span className="min-w-0">
                    <code className="font-mono text-[12.5px] font-semibold text-fg">{it.name}</code>
                    {it.description && <span className="line-clamp-2 text-fg-muted">{it.description}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {preview.data?.skipped?.length ? (
            <div>
              <h3 className="mb-1.5 text-[13px] font-bold text-fg-strong">Left out</h3>
              <ul className="flex flex-col gap-1 text-[13px] text-fg-secondary">
                {preview.data.skipped.map((s, i) => (
                  <li key={i}>
                    <span className="font-semibold text-fg">{s.name}</span>: {s.why}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

/** A marketplace's plugins, searchable, each installable (or saying why not). */
function Plugins({ market, onInstall }: { market: Marketplace; onInstall: (name: string) => void }) {
  const client = useApiClient();
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["marketplace-plugins", market.id],
    queryFn: () => data(Extensions.listMarketplacePlugins({ client, path: { mid: market.id } })),
  });
  const shown = useMemo(() => {
    const words = q.trim().toLowerCase();
    return (list.data ?? []).filter(
      (p: MarketplacePlugin) =>
        !words ||
        `${p.name} ${p.description ?? ""} ${p.category ?? ""} ${p.author ?? ""}`.toLowerCase().includes(words),
    );
  }, [list.data, q]);
  if (list.isLoading) return <SkeletonRows rows={4} />;
  if (list.error)
    return (
      <p role="alert" className="text-[14px] text-red-dark">
        {(list.error as Error).message}
      </p>
    );
  return (
    <div className="flex flex-col gap-3">
      <SearchInput
        aria-label={`Search ${market.title ?? market.name}`}
        placeholder={`Search ${list.data?.length ?? 0} plugins`}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="overflow-x-auto rounded-md border border-border">
        <Table aria-label={`Plugins in ${market.title ?? market.name}`}>
          <THead className="border-t-0">
            <tr>
              <Th>Plugin</Th>
              <Th>Category</Th>
              <Th />
            </tr>
          </THead>
          <tbody>
            {shown.map((p) => (
              <Tr key={p.name}>
                <Td>
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="flex items-center gap-1.5">
                      <span className="font-mono text-[13px] font-semibold text-fg">{p.name}</span>
                      {p.version && <span className="text-[12px] text-fg-muted">v{p.version}</span>}
                      {p.homepage && (
                        <a
                          href={p.homepage}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={`${p.name} homepage`}
                          className="text-fg-muted hover:text-fg-accent"
                        >
                          <ExternalLink className="size-3.5" />
                        </a>
                      )}
                    </span>
                    {p.description && <span className="line-clamp-2 text-[12px] text-fg-muted">{p.description}</span>}
                    {p.author && <span className="text-[12px] text-fg-muted">by {p.author}</span>}
                  </div>
                </Td>
                <Td className="text-fg-secondary">{p.category}</Td>
                <Td className="text-right">
                  {p.installable ? (
                    <span className="flex items-center justify-end gap-2">
                      {p.installed ? (
                        <Link
                          href={`/extensions/${p.installed}`}
                          className="text-[12.5px] text-fg-accent hover:underline"
                        >
                          Installed
                        </Link>
                      ) : null}
                      <Button size="xs" variant={p.installed ? "ghost" : "primary"} onClick={() => onInstall(p.name)}>
                        {p.installed ? "Update" : "Install"}
                      </Button>
                    </span>
                  ) : (
                    <span className="text-[12px] text-fg-muted" title={p.why_not ?? undefined}>
                      Not supported yet
                    </span>
                  )}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  );
}

/** Marketplaces (Anthropic's, or any in its format) and imports from a link or a file. */
export function MarketplacePage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { admin } = useArchive();
  const [source, setSource] = useState("");
  const [link, setLink] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const markets = useQuery({
    queryKey: ["marketplaces"],
    queryFn: () => data(Extensions.listMarketplaces({ client })),
  });
  const suggested = useQuery({
    queryKey: ["marketplaces", "suggested"],
    queryFn: () => data(Extensions.suggestedMarketplaces({ client })),
  });
  const done = () => void qc.invalidateQueries({ queryKey: ["marketplaces"] });
  const add = useMutation({
    mutationFn: (v: { source: string; title?: string }) => data(Extensions.addMarketplace({ client, body: v })),
    onSuccess: (out) => {
      setSource("");
      setOpen(out.id);
      done();
    },
    onError: (e) => toast({ tone: "red", title: "Couldn’t add it", body: (e as Error).message }),
  });
  const refresh = useMutation({
    mutationFn: (mid: number) => data(Extensions.refreshMarketplace({ client, path: { mid } })),
    onSuccess: () => {
      done();
      void qc.invalidateQueries({ queryKey: ["marketplace-plugins"] });
    },
    onError: (e) => toast({ tone: "red", title: "Couldn’t refresh it", body: (e as Error).message }),
  });
  const remove = useMutation({
    mutationFn: (mid: number) => data(Extensions.deleteMarketplace({ client, path: { mid } })),
    onSuccess: done,
    onError: (e) => toast({ tone: "red", title: "Couldn’t remove it", body: (e as Error).message }),
  });
  const pickFile = async (f: File | undefined) => {
    if (!f) return;
    setPending({ kind: "file", filename: f.name, data: await base64(f) });
    if (fileRef.current) fileRef.current.value = "";
  };
  const current = (markets.data ?? []).find((m) => m.id === open) ?? markets.data?.[0];

  return (
    <div className="flex flex-col gap-6 px-4 pb-10 pt-[18px] md:px-6">
      <PageHeader
        title="Marketplace"
        meta="Skills and plugins in Anthropic’s format, from a marketplace, a link or a file"
        actions={
          <Button asChild variant="ghost">
            <Link href="/extensions">Back to extensions</Link>
          </Button>
        }
      />

      <section aria-labelledby="import-title" className="flex flex-col gap-3 rounded-md border border-border p-4">
        <h2 id="import-title" className="text-[15px] font-bold text-fg">
          Import a plugin or a skill
        </h2>
        <div className="flex flex-wrap items-end gap-2">
          <Field
            className="min-w-[260px] flex-1"
            label="From a link"
            hint="A GitHub or GitLab repository or folder, or a .zip link"
          >
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                placeholder="https://github.com/anthropics/skills/tree/main/skills/pdf"
                value={link}
                onChange={(e) => setLink(e.target.value)}
              />
            )}
          </Field>
          <Button disabled={!link.trim()} onClick={() => setPending({ kind: "link", url: link.trim() })}>
            <Link2 aria-hidden />
            Preview
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".zip,.skill,.tar.gz,.tgz"
            className="hidden"
            aria-label="Plugin or skill file"
            onChange={(e) => void pickFile(e.target.files?.[0])}
          />
          <Button variant="ghost" onClick={() => fileRef.current?.click()}>
            <Upload aria-hidden />
            From a file
          </Button>
        </div>
      </section>

      <section aria-labelledby="markets-title" className="flex flex-col gap-3">
        <h2 id="markets-title" className="text-[15px] font-bold text-fg">
          Marketplaces
        </h2>
        {admin ? (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-end gap-2">
              <Field
                className="min-w-[260px] flex-1"
                label="Add a marketplace"
                hint="owner/repo on GitHub, a GitHub or GitLab link, or a link to a marketplace.json"
              >
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    aria-describedby={describedBy}
                    mono
                    placeholder="your-team/claude-plugins"
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                  />
                )}
              </Field>
              <Button
                variant="primary"
                disabled={!source.trim() || add.isPending}
                onClick={() => add.mutate({ source: source.trim() })}
              >
                Add
              </Button>
            </div>
            {suggested.data?.length ? (
              <div className="flex flex-wrap items-center gap-2 text-[13px] text-fg-secondary">
                Suggested:
                {suggested.data.map((s) => (
                  <Button
                    key={s.source}
                    size="xs"
                    variant="secondary"
                    disabled={add.isPending}
                    onClick={() => add.mutate({ source: s.source, title: s.title })}
                  >
                    Add {s.title}
                  </Button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {markets.isLoading ? (
          <SkeletonRows rows={3} />
        ) : markets.data?.length ? (
          <>
            <div role="tablist" aria-label="Marketplaces" className="flex flex-wrap gap-2">
              {markets.data.map((m) => (
                <span key={m.id} className="flex items-center gap-1">
                  <Button
                    role="tab"
                    aria-selected={current?.id === m.id}
                    size="sm"
                    variant={current?.id === m.id ? "primary" : "secondary"}
                    onClick={() => setOpen(m.id)}
                  >
                    {m.title ?? m.name}
                    <span className="font-normal opacity-80">{m.plugins}</span>
                  </Button>
                  {admin && current?.id === m.id ? (
                    <>
                      <IconButton
                        label={`Refresh ${m.title ?? m.name}`}
                        disabled={refresh.isPending}
                        onClick={() => refresh.mutate(m.id)}
                      >
                        <RefreshCw />
                      </IconButton>
                      <IconButton label={`Remove ${m.title ?? m.name}`} onClick={() => remove.mutate(m.id)}>
                        <Trash2 />
                      </IconButton>
                    </>
                  ) : null}
                </span>
              ))}
            </div>
            {current && (
              <>
                <p className="text-[13px] text-fg-muted">
                  {current.description ? `${current.description}. ` : ""}
                  <a href={current.source} target="_blank" rel="noreferrer" className="break-all hover:underline">
                    {current.source}
                  </a>
                </p>
                <Plugins
                  market={current}
                  onInstall={(name) => setPending({ kind: "plugin", market: current.id, name })}
                />
              </>
            )}
          </>
        ) : (
          <EmptyState icon={<Store />} title="No marketplaces yet">
            {admin
              ? "Add Anthropic’s, or your team’s own: any repository with a .claude-plugin/marketplace.json."
              : "An admin adds marketplaces. Meanwhile, import a plugin or a skill from a link or a file above."}
          </EmptyState>
        )}
      </section>

      <PreviewDialog pending={pending} onClose={() => setPending(null)} />
    </div>
  );
}
