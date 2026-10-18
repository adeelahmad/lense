"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ListVideo, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Playlists } from "@/app/openapi-client";
import { pk, usePlaylists } from "@/components/playlists/hooks";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { tc } from "@/lib/format";

/** Playlists: yours (the latest used first) and the ones others shared, with a name field that makes one on Enter. */
export function PlaylistsPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const list = usePlaylists();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: (n: string) => data(Playlists.createPlaylist({ client, body: { name: n } })),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: pk.all });
      router.push(`/playlists/${p.id}`);
    },
    onError: (e: unknown) =>
      toast({
        title: "Couldn't make the playlist",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      }),
  });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };
  const mine = list.data?.filter((p) => p.mine) ?? [];
  const shared = list.data?.filter((p) => !p.mine) ?? [];
  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-6">
      <PageHeader className="mb-0" title="Playlists" meta="Clips of moments, played back to back" />
      <form onSubmit={submit} className="flex max-w-[760px] gap-2" aria-label="New playlist">
        <Input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New playlist name"
          aria-label="New playlist name"
          maxLength={120}
          className="flex-1"
        />
        <Button type="submit" variant="primary" icon={<Plus />} disabled={!name.trim() || create.isPending}>
          New
        </Button>
      </form>
      {list.isLoading && <SkeletonRows rows={3} />}
      {list.isError && (
        <Banner
          tone="error"
          action={
            <Button size="sm" variant="secondary" onClick={() => list.refetch()}>
              Try again
            </Button>
          }
        >
          {list.error.message}
        </Banner>
      )}
      {list.data?.length === 0 && (
        <EmptyState icon={<ListVideo />} title="No playlists yet">
          Select words in a transcript and choose Add to playlist, or add a highlight from its tab: the moment goes in
          your latest playlist, or a new one called Clips.
        </EmptyState>
      )}
      {mine.length > 0 && <Section title="Your playlists" rows={mine} />}
      {shared.length > 0 && <Section title="Shared with you" rows={shared} />}
    </div>
  );
}

function Section({ title, rows }: { title: string; rows: NonNullable<ReturnType<typeof usePlaylists>["data"]> }) {
  const id = `playlists-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section aria-labelledby={id} className="max-w-[760px] rounded-lg border border-border px-5 py-4">
      <h2 id={id} className="pb-2 text-[15px] font-bold text-fg">
        {title}
      </h2>
      <ul className="m-0 list-none p-0">
        {rows.map((p) => (
          <li key={p.id} className="border-t border-border first:border-t-0">
            <Link href={`/playlists/${p.id}`} className="flex items-center gap-3 rounded-sm px-1 py-3 hover:bg-surface">
              {p.shared ? (
                <Users aria-hidden className="size-[18px] shrink-0 text-fg-secondary" />
              ) : (
                <ListVideo aria-hidden className="size-[18px] shrink-0 text-fg-secondary" />
              )}
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-[13.5px] font-semibold text-fg">{p.name}</span>
                <span className="truncate text-[11.5px] text-fg-muted">
                  {p.mine ? (p.shared ? "Shared" : "Only you") : `By ${p.created_by_name || p.created_by || "someone"}`}
                  {p.description ? ` · ${p.description}` : ""}
                </span>
              </span>
              <span className="tabular text-[12.5px] font-semibold text-fg-secondary">
                {p.count === 1 ? "1 clip" : `${p.count} clips`} · {tc(p.duration_ms)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
