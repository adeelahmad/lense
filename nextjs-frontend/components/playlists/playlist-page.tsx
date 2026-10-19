"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, GripVertical, ListVideo, Play, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Playlists } from "@/app/openapi-client";
import type { Clip, PlaylistDetail } from "@/app/openapi-client/types.gen";
import { pk, usePlaylist } from "@/components/playlists/hooks";
import {
  clipTitle,
  followCurrent,
  moveBy,
  moveTo,
  readdBody,
  removeItem,
  restoreItem,
  totalMs,
} from "@/components/playlists/model";
import { PlaylistPlayerBar, usePlaylistPlayer } from "@/components/playlists/player";
import { recordingHref } from "@/components/search/links";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Input, Switch } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * A playlist: its clips played back to back, reordered by dragging or with Up and Down, and taken out with Undo. Its
 * maker renames, shares and deletes it (Undo makes it again with its clips).
 */
export function PlaylistPage({ id }: { id: number }) {
  const q = usePlaylist(id);
  if (q.isLoading) return <SkeletonRows rows={4} className="px-4 py-6 md:px-6" />;
  if (q.isError || !q.data)
    return (
      <div className="px-4 py-6 md:px-6">
        <Banner tone="error">{q.error?.message ?? "Couldn’t load the playlist"}</Banner>
      </div>
    );
  return <Loaded p={q.data} />;
}

function Loaded({ p }: { p: PlaylistDetail }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [items, setItems] = useState<Clip[]>(p.items);
  const [name, setName] = useState(p.name);
  const [dragging, setDragging] = useState<number | null>(null);
  const player = usePlaylistPlayer(items);
  useEffect(() => setItems(p.items), [p.items]);
  useEffect(() => setName(p.name), [p.name]);
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: pk.one(p.id) });
    void qc.invalidateQueries({ queryKey: pk.all });
  };

  /** Show the new order at once, keep the clip playing where it went, then save it. */
  const apply = (next: Clip[]) => {
    player.setIndex(followCurrent(items, next, player.index));
    setItems(next);
  };
  const order = useMutation({
    mutationFn: (next: Clip[]) =>
      data(Playlists.reorderPlaylist({ client, path: { pid: p.id }, body: { items: next.map((c) => c.id) } })),
    onSuccess: (d) => qc.setQueryData(pk.one(p.id), d),
    onError: (e) => {
      fail("Couldn't save the order")(e);
      refresh();
    },
  });
  const reorder = (next: Clip[]) => {
    if (next === items) return;
    apply(next);
    order.mutate(next);
  };
  const remove = (clip: Clip) => {
    const { items: next, removed } = removeItem(items, clip.id);
    if (!removed) return;
    apply(next);
    void data(Playlists.removeClip({ client, path: { pid: p.id, iid: clip.id } })).then(() => {
      refresh();
      toast({
        title: `Removed ${clipTitle(clip)}`,
        tone: "green",
        action: {
          label: "Undo",
          onClick: () => {
            setItems((now) => restoreItem(now, removed));
            void data(Playlists.addClip({ client, path: { pid: p.id }, body: readdBody(removed) })).then(
              refresh,
              fail("Couldn't put it back"),
            );
          },
        },
      });
    }, fail("Couldn't remove it"));
  };
  const update = useMutation({
    mutationFn: (body: { name?: string; shared?: boolean }) =>
      data(Playlists.updatePlaylist({ client, path: { pid: p.id }, body })),
    onSuccess: refresh,
    onError: (e) => {
      fail("Couldn't change the playlist")(e);
      setName(p.name);
    },
  });
  const rename = () => {
    const n = name.trim();
    if (n && n !== p.name) update.mutate({ name: n });
    else setName(p.name);
  };
  const del = useMutation({
    mutationFn: () => data(Playlists.deletePlaylist({ client, path: { pid: p.id } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: pk.all });
      router.push("/playlists");
      toast({
        title: `Deleted ${p.name}`,
        tone: "green",
        action: {
          label: "Undo",
          onClick: () =>
            void (async () => {
              const made = await data(
                Playlists.createPlaylist({
                  client,
                  body: { name: p.name, description: p.description ?? undefined, shared: p.shared },
                }),
              );
              for (const [index, item] of items.entries())
                await data(Playlists.addClip({ client, path: { pid: made.id }, body: readdBody({ item, index }) }));
              void qc.invalidateQueries({ queryKey: pk.all });
              router.push(`/playlists/${made.id}`);
            })().catch(fail("Couldn't make it again")),
        },
      });
    },
    onError: fail("Couldn't delete the playlist"),
  });

  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-6">
      <PageHeader
        className="mb-0"
        title={
          p.mine ? (
            <Input
              value={name}
              aria-label="Playlist name"
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              onBlur={rename}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
                if (e.key === "Escape") setName(p.name);
              }}
              className="h-11 border-transparent px-1 text-[24px] font-bold hover:border-border"
            />
          ) : (
            p.name
          )
        }
        meta={`${items.length === 1 ? "1 clip" : `${items.length} clips`} · ${tc(totalMs(items))}${
          p.mine ? "" : ` · by ${p.created_by_name || p.created_by || "someone"}`
        }`}
        actions={
          p.mine && (
            <>
              <Switch
                checked={Boolean(p.shared)}
                onCheckedChange={(shared) => update.mutate({ shared })}
                label="Shared"
                aria-label="Share with everyone signed in"
              />
              <Button variant="danger-ghost" icon={<Trash2 />} onClick={() => del.mutate()} disabled={del.isPending}>
                Delete
              </Button>
            </>
          )
        }
      >
        <Link href="/playlists" className="text-[13px] text-fg-accent hover:underline">
          All playlists
        </Link>
      </PageHeader>
      {p.description && <p className="max-w-[760px] text-[14px] text-fg-secondary">{p.description}</p>}
      {(p.hidden ?? 0) > 0 && (
        <Banner tone="info">
          {p.hidden === 1 ? "1 clip is" : `${p.hidden ?? 0} clips are`} of resources you can no longer read, so they’re
          left out.
        </Banner>
      )}
      <div className="max-w-[760px]">
        <PlaylistPlayerBar player={player} count={items.length} />
      </div>
      {items.length === 0 ? (
        <EmptyState icon={<ListVideo />} title="No clips yet">
          Select words in a transcript and choose Add to playlist, or add a highlight from its tab.
        </EmptyState>
      ) : (
        <ol aria-label="Clips" className="m-0 flex max-w-[760px] list-none flex-col p-0">
          {items.map((c, i) => (
            <li
              key={c.id}
              draggable={p.mine}
              onDragStart={() => setDragging(i)}
              onDragEnd={() => setDragging(null)}
              onDragOver={(e) => p.mine && dragging != null && e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (dragging != null) reorder(moveTo(items, dragging, i));
                setDragging(null);
              }}
              aria-current={player.index === i || undefined}
              className={cn(
                "flex items-start gap-2 border-t border-border px-1 py-3 first:border-t-0",
                player.index === i && "rounded-md bg-blue-surface",
                dragging === i && "opacity-50",
              )}
            >
              {p.mine && <GripVertical aria-hidden className="mt-2 size-4 shrink-0 cursor-grab text-fg-muted" />}
              <span className="tabular mt-2 w-5 shrink-0 text-right text-[12px] text-fg-muted">{i + 1}</span>
              <IconButton
                label={c.audio ? `Play ${clipTitle(c)}` : "Text only: open it to read the passage"}
                disabled={!c.audio}
                onClick={() => player.start(i)}
                size={32}
              >
                <Play />
              </IconButton>
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-[13.5px] font-semibold text-fg">{clipTitle(c)}</span>
                <span className="truncate text-[12px] text-fg-muted">
                  <Link href={recordingHref(c.recording, c.t0)} className="hover:underline">
                    {c.recording_title || `Asset ${c.recording}`}
                  </Link>
                  {c.namespace ? ` · ${c.namespace}` : ""} · {tc(c.t0)}–{tc(c.t1)}
                </span>
                {c.quote && c.quote !== c.title && (
                  <blockquote className="line-clamp-2 text-[13px] leading-snug text-fg-secondary">{c.quote}</blockquote>
                )}
                {c.note && <p className="text-[13px] leading-snug text-fg">{c.note}</p>}
              </div>
              {p.mine && (
                <span className="flex shrink-0 items-center">
                  <IconButton
                    label="Move up"
                    size={32}
                    disabled={i === 0}
                    onClick={() => reorder(moveBy(items, c.id, -1))}
                  >
                    <ChevronUp />
                  </IconButton>
                  <IconButton
                    label="Move down"
                    size={32}
                    disabled={i === items.length - 1}
                    onClick={() => reorder(moveBy(items, c.id, 1))}
                  >
                    <ChevronDown />
                  </IconButton>
                  <IconButton label="Remove from playlist" size={32} onClick={() => remove(c)}>
                    <Trash2 />
                  </IconButton>
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
