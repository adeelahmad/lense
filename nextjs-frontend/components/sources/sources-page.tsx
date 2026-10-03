"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ellipsis, FolderSearch, HardDriveDownload, Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { Pipelines, Sources } from "@/app/openapi-client";
import type { Source, Watch } from "@/app/openapi-client/types.gen";
import { SensorsTabs } from "@/components/sensors/parts";
import { ConnectionDialog } from "@/components/sources/connection-dialog";
import { DeleteConnectionDialog } from "@/components/sources/delete-connection";
import { FolderBrowser } from "@/components/sources/folder-browser";
import { TypeTile } from "@/components/sources/source-icons";
import { healthOf, sourceSubtitle, type BackendSpec } from "@/components/sources/source-model";
import { WatchCard } from "@/components/sources/watch-card";
import { WatchEditor } from "@/components/sources/watch-editor";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const GLYPH = {
  ok: ["✓", "text-green-dark"],
  bad: ["✕", "text-red-dark"],
  unknown: ["–", "text-fg-muted"],
} as const;

function HealthText({ source }: { source: Source }) {
  const h = healthOf(source.health);
  const [g, c] = GLYPH[h.tone];
  return (
    <span className={cn("flex items-start gap-[7px] text-[12.5px] font-medium leading-[1.35]", c)}>
      <span aria-hidden className="font-extrabold">
        {g}
      </span>
      <span className="min-w-0 break-words">{h.text}</span>
    </span>
  );
}

function watchedText(ws: Watch[]) {
  if (!ws.length) return "none";
  const ns = [...new Set(ws.map((w) => w.namespace).filter(Boolean))];
  return `${ws.length} · → ${ns.join(", ")}`;
}

type Editing = { source: Source | null; replaceToken?: boolean } | null;
type Watching = {
  source: { id: number; name: string };
  path: string;
  watch?: Watch | null;
} | null;

/** SO1–SO3: connections with their health, and the watched folders that import from them. */
export function SourcesPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { admin, can } = useArchive();
  const [selected, setSelected] = useState<number | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [browsing, setBrowsing] = useState<Source | null>(null);
  const [watching, setWatching] = useState<Watching>(null);
  const [deleting, setDeleting] = useState<Source | null>(null);

  const backends = useQuery({
    queryKey: ["source-backends"],
    queryFn: () => data(Sources.listBackends({ client })),
    staleTime: Infinity,
    enabled: admin,
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => data(Sources.listSources({ client })),
    enabled: admin,
    refetchInterval: 60_000,
  });
  const watches = useQuery({
    queryKey: ["watches"],
    queryFn: () => data(Sources.listWatches({ client })),
    refetchInterval: 30_000,
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 60_000,
  });
  const pipelineName = useMemo(() => {
    const m = new Map((pipelines.data?.pipelines ?? []).map((p) => [p.id, p.name]));
    return (w: Watch) => {
      const id = (w as Watch & { pipeline?: number | null }).pipeline;
      return id ? (m.get(id) ?? `pipeline #${id}`) : null;
    };
  }, [pipelines.data]);

  const test = useMutation({
    mutationFn: (s: Source) => data(Sources.testSource({ client, path: { sid: s.id } })),
    onSuccess: (h, s) => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      toast(
        h.ok
          ? { tone: "green", title: "Connection OK", body: s.name }
          : { tone: "red", title: "Still failing", body: h.error ?? s.name },
      );
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t test", body: e.message }),
  });

  const all = (sources.data ?? []) as Source[];
  const ws = (watches.data ?? []) as Watch[];
  const bySource = (id: number) => ws.filter((w) => w.source === id);
  const current = all.find((s) => s.id === selected) ?? all.find((s) => bySource(s.id).length) ?? all[0];
  const specs = (backends.data ?? {}) as Record<string, BackendSpec>;

  const dialogs = (
    <>
      {editing && backends.data && (
        <ConnectionDialog
          open
          onOpenChange={(o) => !o && setEditing(null)}
          backends={specs}
          source={editing.source}
          replaceToken={editing.replaceToken}
          onSaved={(id) => setSelected(id)}
        />
      )}
      {browsing && (
        <FolderBrowser
          open
          source={browsing}
          onOpenChange={(o) => !o && setBrowsing(null)}
          canWatch={admin}
          onWatch={(path) => {
            setWatching({ source: browsing, path });
            setBrowsing(null);
          }}
        />
      )}
      {watching && (
        <WatchEditor
          open
          onOpenChange={(o) => !o && setWatching(null)}
          source={watching.source}
          path={watching.path}
          watch={watching.watch}
        />
      )}
      {deleting && (
        <DeleteConnectionDialog
          open
          source={deleting}
          watches={bySource(deleting.id)}
          onOpenChange={(o) => !o && setDeleting(null)}
          onDeleted={() => setSelected(null)}
        />
      )}
    </>
  );

  // Namespace owners: the watched folders feeding their namespaces, read-only.
  if (!admin) {
    const owner = can("owner");
    return (
      <div className="px-4 pb-10 pt-[18px] md:px-6">
        <PageHeader
          title="Sources"
          meta={
            owner && watches.isSuccess ? `${plural(ws.length, "watched folder")} feeding your namespaces` : undefined
          }
        />
        {watches.isLoading ? (
          <SkeletonRows rows={4} />
        ) : watches.error ? (
          <EmptyState
            tone="error"
            icon={<HardDriveDownload />}
            title="Couldn’t load watched folders"
            actions={<Button onClick={() => watches.refetch()}>Try again</Button>}
          >
            {(watches.error as Error).message}
          </EmptyState>
        ) : !ws.length ? (
          <EmptyState icon={<HardDriveDownload />} title="Sources are managed by admins">
            {owner
              ? "No watched folders feed the namespaces you own. Ask an admin to watch a folder for you."
              : "Storage connections and watched folders are set up by admins. Owners of a namespace see the folders that feed it here."}
          </EmptyState>
        ) : (
          <div className="flex flex-col gap-3">
            <Banner>
              Admins manage connections. You see the watched folders that feed namespaces you own; they’re read-only
              here.
            </Banner>
            {ws.map((w) => (
              <WatchCard key={w.id} watch={w} manage={false} showSource pipelineName={pipelineName(w)} />
            ))}
          </div>
        )}
      </div>
    );
  }

  const loading = sources.isLoading || watches.isLoading;
  const error = (sources.error ?? watches.error) as Error | null;

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <div className="flex flex-wrap items-center gap-3.5 px-4 pb-3.5 pt-[18px] md:px-6">
        <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Sensors</h1>
        {sources.isSuccess && (
          <span className="text-[13px] text-fg-muted">
            {plural(all.length, "connection")} · {plural(ws.length, "watched folder")}
          </span>
        )}
        <span className="flex-1" />
        <Button
          size="sm"
          variant="primary"
          icon={<Plus />}
          onClick={() => setEditing({ source: null })}
          disabled={!backends.data}
          disabledReason="Loading the kinds of storage…"
        >
          Add connection
        </Button>
      </div>
      <div className="px-4 pb-3.5 md:px-6">
        <SensorsTabs tab="sources" />
      </div>

      {loading ? (
        <SkeletonRows rows={5} className="px-6" />
      ) : error ? (
        <EmptyState
          tone="error"
          icon={<HardDriveDownload />}
          title="Couldn’t load sources"
          actions={<Button onClick={() => (sources.refetch(), watches.refetch())}>Try again</Button>}
        >
          {error.message}
        </EmptyState>
      ) : !all.length ? (
        <EmptyState
          icon={<HardDriveDownload />}
          title="No connections yet"
          actions={
            <Button
              variant="primary"
              icon={<Plus />}
              onClick={() => setEditing({ source: null })}
              disabled={!backends.data}
            >
              Add connection
            </Button>
          }
        >
          Connect S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV or a folder on this machine, then watch a
          folder to import new files as they arrive.
        </EmptyState>
      ) : (
        <>
          <Table aria-label="Connections" className="table-fixed border-t border-border">
            <colgroup>
              <col style={{ width: 70 }} />
              <col style={{ width: "30%" }} />
              <col />
              <col style={{ width: 210 }} />
              <col style={{ width: 200 }} />
            </colgroup>
            <THead>
              <tr>
                <Th className="first:pl-6">
                  <span className="sr-only">Type</span>
                </Th>
                <Th>Connection</Th>
                <Th>Health</Th>
                <Th>Watched folders</Th>
                <Th className="last:pr-6">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {all.map((s) => {
                const on = current?.id === s.id;
                const bad = s.health && !s.health.ok;
                const oauth = Boolean(s.oauth);
                return (
                  <Tr key={s.id} selected={on} className="h-14 cursor-pointer" onClick={() => setSelected(s.id)}>
                    <Td className="first:pl-6">
                      <TypeTile type={s.type} />
                    </Td>
                    <Td>
                      <button
                        type="button"
                        onClick={() => setSelected(s.id)}
                        aria-pressed={on}
                        className="flex min-w-0 max-w-full flex-col gap-1 text-left"
                      >
                        <span className="truncate text-[13.5px] font-semibold leading-tight text-fg">{s.name}</span>
                        <span className="truncate text-[12px] text-fg-muted">{sourceSubtitle(s)}</span>
                      </button>
                    </Td>
                    <Td>
                      <HealthText source={s} />
                    </Td>
                    <Td className="text-[13px] font-medium text-fg-strong">{watchedText(bySource(s.id))}</Td>
                    <Td className="last:pr-6">
                      <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                        {bad &&
                        oauth &&
                        /token|auth|expired|unauthori[sz]ed|401|invalid_grant/i.test(s.health?.error ?? "") ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => setEditing({ source: s, replaceToken: true })}
                          >
                            Paste new token
                          </Button>
                        ) : bad || !s.health ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => test.mutate(s)}
                            disabled={test.isPending && test.variables?.id === s.id}
                          >
                            {test.isPending && test.variables?.id === s.id ? "Testing…" : bad ? "Test again" : "Test"}
                          </Button>
                        ) : null}
                        <Menu>
                          <MenuTrigger asChild>
                            <button
                              type="button"
                              aria-label={`More for ${s.name}`}
                              className="grid size-8 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                            >
                              <Ellipsis aria-hidden className="size-[18px]" />
                            </button>
                          </MenuTrigger>
                          <MenuContent align="end">
                            <MenuItem onSelect={() => setBrowsing(s)}>Browse folders</MenuItem>
                            <MenuItem onSelect={() => test.mutate(s)}>Test now</MenuItem>
                            <MenuItem onSelect={() => setEditing({ source: s })}>Edit connection</MenuItem>
                            {oauth && (
                              <MenuItem onSelect={() => setEditing({ source: s, replaceToken: true })}>
                                Paste new token
                              </MenuItem>
                            )}
                            <MenuSeparator />
                            <MenuItem danger onSelect={() => setDeleting(s)}>
                              Delete…
                            </MenuItem>
                          </MenuContent>
                        </Menu>
                      </div>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>

          {current && (
            <section
              aria-label={`${current.name}: watched folders`}
              className="flex flex-1 flex-col gap-3 bg-surface px-4 pb-8 pt-[18px] md:px-6"
            >
              <div className="flex items-center gap-2.5">
                <TypeTile type={current.type} size={22} />
                <h2 className="min-w-0 truncate text-[16px] font-bold text-fg">{current.name}</h2>
                <span className="flex-1" />
                <Button size="sm" variant="secondary" icon={<FolderSearch />} onClick={() => setBrowsing(current)}>
                  Browse folders
                </Button>
              </div>
              {bySource(current.id).length ? (
                bySource(current.id).map((w) => (
                  <WatchCard
                    key={w.id}
                    watch={w}
                    manage
                    pipelineName={pipelineName(w)}
                    onEdit={() =>
                      setWatching({
                        source: { id: current.id, name: current.name },
                        path: w.path,
                        watch: w,
                      })
                    }
                  />
                ))
              ) : (
                <p className="rounded-md border border-dashed border-border bg-background px-4 py-5 text-[13.5px] text-fg-secondary">
                  No watched folders on this connection. Browse its folders and pick one to watch: new files there are
                  imported into a namespace and run through its pipeline.
                </p>
              )}
              <p className="mt-1 text-[12.5px] leading-normal text-fg-muted">
                Scan counts: seen = files matched this scan · new = queued · waiting = still changing · skipped =
                already there or excluded · errors = couldn’t read. Changes are found by polling; there are no webhooks.
              </p>
            </section>
          )}
        </>
      )}
      {dialogs}
    </div>
  );
}
