"use client";

import { useQuery } from "@tanstack/react-query";
import { Captions, ChevronRight, File, FileAudio, Folder, FolderOpen } from "lucide-react";
import { useEffect, useState } from "react";

import { Sources } from "@/app/openapi-client";
import type { BrowseEntry, Source } from "@/app/openapi-client/types.gen";
import { TYPE_ICON } from "@/components/sources/source-icons";
import { fileKind } from "@/components/sources/source-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { bytes, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE = 100;

/** Breadcrumbs for a path: remote paths are relative to the connection's top; local ones are absolute. */
function crumbs(path: string, local: boolean): { label: string; path: string }[] {
  const parts = path.split("/").filter(Boolean);
  const out: { label: string; path: string }[] = [];
  parts.forEach((p, i) =>
    out.push({
      label: p,
      path: (local ? "/" : "") + parts.slice(0, i + 1).join("/"),
    }),
  );
  return out;
}

/** SO3: browse a connection's folders, then watch one. */
export function FolderBrowser({
  source,
  open,
  onOpenChange,
  onWatch,
  canWatch,
}: {
  source: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onWatch: (path: string) => void;
  canWatch: boolean;
}) {
  const client = useApiClient();
  const [path, setPath] = useState("");
  const [shown, setShown] = useState(PAGE);
  const local = source.type === "local";
  useEffect(() => {
    if (open) setPath("");
  }, [open, source.id]);
  useEffect(() => setShown(PAGE), [path]);

  const q = useQuery({
    queryKey: ["browse", source.id, path],
    queryFn: () =>
      data(
        Sources.browseSource({
          client,
          path: { sid: source.id },
          query: { path },
        }),
      ),
    enabled: open,
    staleTime: 30_000,
    retry: false,
  });
  const entries: BrowseEntry[] = q.data ?? [];
  const files = entries.filter((e) => !e.dir);
  const audio = files.filter((f) => fileKind(f.name) === "audio").length;
  const transcripts = files.filter((f) => fileKind(f.name) === "transcript").length;
  const Root = TYPE_ICON[source.type];
  const atRoots = local && path === "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={`Browse ${source.name}`} className="max-w-[640px] gap-3">
      <nav
        aria-label="Folder"
        className="-mx-6 flex flex-wrap items-center gap-1 border-y border-border px-4 py-2.5 text-[13px] font-medium text-fg-secondary"
      >
        <button
          type="button"
          onClick={() => setPath("")}
          className={cn("flex items-center gap-1.5 rounded-xs px-1 hover:text-fg", path === "" && "font-bold text-fg")}
        >
          <Root aria-hidden className="size-[15px]" />
          {local ? "Allowed folders" : source.name}
        </button>
        {crumbs(path, local).map((c, i, all) => (
          <span key={c.path} className="flex items-center gap-1">
            <ChevronRight aria-hidden className="size-3.5" />
            <button
              type="button"
              aria-current={i === all.length - 1 ? "location" : undefined}
              onClick={() => setPath(c.path)}
              className={cn("rounded-xs px-1 hover:text-fg", i === all.length - 1 && "font-bold text-fg")}
            >
              {c.label}
            </button>
          </span>
        ))}
      </nav>
      <div className="-mx-6 min-h-[300px]">
        {q.isLoading ? (
          <div className="flex flex-col gap-3 px-4 py-3" aria-busy="true" aria-label="Loading folder">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-4" style={{ width: `${70 - i * 6}%` }} />
            ))}
          </div>
        ) : q.error ? (
          <EmptyState
            tone="error"
            icon={<FolderOpen />}
            title="Couldn’t open this folder"
            actions={<Button onClick={() => q.refetch()}>Try again</Button>}
          >
            <code className="font-mono text-[12.5px]">{(q.error as Error).message}</code>
          </EmptyState>
        ) : !entries.length ? (
          <EmptyState icon={<FolderOpen />} title={atRoots ? "No folders are allowed yet" : "This folder is empty"}>
            {atRoots
              ? "Admins list the folders this machine may read in sources.local_roots (set at startup)."
              : "Files that land here later are picked up once you watch it."}
          </EmptyState>
        ) : (
          <table className="w-full table-fixed border-collapse text-[13px]">
            <thead className="bg-surface text-left">
              <tr>
                <th scope="col" className="w-9 py-2 pl-4" aria-label="Kind" />
                <th scope="col" className="py-2 text-[12px] font-bold text-fg-secondary">
                  Name
                </th>
                <th scope="col" className="w-[90px] py-2 text-right text-[12px] font-bold text-fg-secondary">
                  Size
                </th>
                <th scope="col" className="w-[120px] py-2 pr-4 text-right text-[12px] font-bold text-fg-secondary">
                  Modified
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.slice(0, shown).map((e) => {
                const kind = e.dir ? "dir" : fileKind(e.name);
                const Icon = e.dir ? Folder : kind === "audio" ? FileAudio : kind === "transcript" ? Captions : File;
                return (
                  <tr
                    key={e.path}
                    className={cn("h-[38px] border-t border-border", kind === "other" && "text-fg-muted")}
                  >
                    <td className="pl-4">
                      <Icon aria-hidden className="size-[15px] text-fg-secondary" />
                    </td>
                    <td className="truncate pr-2">
                      {e.dir ? (
                        <button
                          type="button"
                          onClick={() => setPath(e.path)}
                          className="max-w-full truncate text-left font-medium text-fg hover:text-fg-accent hover:underline"
                        >
                          {e.name}
                        </button>
                      ) : (
                        <span title={e.name}>{e.name}</span>
                      )}
                    </td>
                    <td className="tabular text-right">{e.dir ? "—" : bytes(e.size)}</td>
                    <td className="tabular pr-4 text-right">{e.modified ? shortDate(e.modified) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {entries.length > shown && (
          <div className="flex items-center gap-2 border-t border-border px-4 py-2 text-[12.5px] text-fg-muted">
            {shown} of {entries.length}
            <Button variant="link" size="xs" onClick={() => setShown((n) => n + PAGE)}>
              Show more
            </Button>
          </div>
        )}
      </div>
      <div className="-mx-6 -mb-6 flex flex-wrap items-center gap-2.5 border-t border-border px-4 py-3">
        <span className="flex-1 text-[12.5px] text-fg-secondary">
          {q.isSuccess && !atRoots
            ? `${audio} audio · ${transcripts} transcripts in this folder`
            : atRoots
              ? "Pick an allowed folder"
              : ""}
        </span>
        <Button
          size="sm"
          variant="primary"
          disabled={!canWatch || atRoots || !q.isSuccess}
          disabledReason={
            !canWatch
              ? "Only admins can watch folders"
              : atRoots
                ? "Open one of the allowed folders first"
                : "Wait for the folder to load"
          }
          onClick={() => onWatch(path)}
        >
          Watch this folder
        </Button>
      </div>
    </Dialog>
  );
}
