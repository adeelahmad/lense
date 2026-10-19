"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback } from "react";

import { Playlists } from "@/app/openapi-client";
import type { ClipCreate } from "@/app/openapi-client/types.gen";
import { defaultPlaylist, FIRST_PLAYLIST } from "@/components/playlists/model";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

export const pk = {
  all: ["playlists"] as const,
  one: (id: number) => ["playlists", id] as const,
};

export function usePlaylists() {
  const client = useApiClient();
  return useQuery({ queryKey: pk.all, queryFn: () => data(Playlists.listPlaylists({ client })), staleTime: 30_000 });
}

export function usePlaylist(id: number) {
  const client = useApiClient();
  // the media links in it are signed for a while; changes refresh it
  return useQuery({
    queryKey: pk.one(id),
    queryFn: () => data(Playlists.getPlaylist({ client, path: { pid: id } })),
    staleTime: 60_000,
  });
}

/**
 * Keep a moment as a clip in one click: it goes at the end of your latest used playlist (a new one called Clips when
 * you have none), and the toast says where, with Undo.
 */
export function useKeepClip() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const add = useMutation({
    mutationFn: async (body: Omit<ClipCreate, "position">) => {
      const list = await qc.fetchQuery({
        queryKey: pk.all,
        queryFn: () => data(Playlists.listPlaylists({ client })),
        staleTime: 30_000,
      });
      const target =
        defaultPlaylist(list) ?? (await data(Playlists.createPlaylist({ client, body: { name: FIRST_PLAYLIST } })));
      const clip = await data(Playlists.addClip({ client, path: { pid: target.id }, body }));
      return { clip, playlist: target };
    },
    onSuccess: ({ clip, playlist }) => {
      void qc.invalidateQueries({ queryKey: pk.all });
      toast({
        title: `Added to ${playlist.name}`,
        tone: "green",
        body: (
          <Link href={`/playlists/${playlist.id}`} className="underline">
            Open the playlist
          </Link>
        ),
        action: {
          label: "Undo",
          onClick: () =>
            void data(Playlists.removeClip({ client, path: { pid: playlist.id, iid: clip.id } })).then(
              () => qc.invalidateQueries({ queryKey: pk.all }),
              () => toast({ title: "Couldn't undo it", tone: "red" }),
            ),
        },
      });
    },
    onError: (e: unknown) =>
      toast({
        title: "Couldn't add it to a playlist",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      }),
  });
  const keep = useCallback((body: Omit<ClipCreate, "position">) => add.mutate(body), [add]);
  return { keep, pending: add.isPending };
}
