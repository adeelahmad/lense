"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { Activities } from "@/app/openapi-client";
import type { Comment, Reaction, ReactionsOut } from "@/app/openapi-client/types.gen";
import { applyReaction } from "@/components/timeline/social-model";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** Everyone's reactions on the page's activities: what the pages say, with your own on top as soon as you give
 * them; `react` gives or takes back an emoji and puts the list back if the server refuses. */
export function useReactions(server: Record<string, Reaction[] | undefined>) {
  const client = useApiClient();
  const toast = useToast();
  const [mine, setMine] = useState<Record<string, Reaction[]>>({});
  const of = useCallback((id: string) => mine[id] ?? server[id] ?? [], [mine, server]);
  const { mutate } = useMutation({
    mutationFn: ({ id, emoji, on }: { id: string; emoji: string; on: boolean }) =>
      data(
        Activities.reactToActivity({ client, path: { activity_id: id }, body: { emoji, on } }),
      ) as Promise<ReactionsOut>,
  });
  const react = useCallback(
    (id: string, emoji: string, on: boolean) => {
      const before = of(id);
      setMine((m) => ({ ...m, [id]: applyReaction(before, emoji, on) }));
      mutate(
        { id, emoji, on },
        {
          onSuccess: (got) => setMine((m) => ({ ...m, [id]: got.reactions })),
          onError: (e) => {
            setMine((m) => ({ ...m, [id]: before }));
            toast({ tone: "red", title: "Couldn’t save the reaction", body: (e as Error).message });
          },
        },
      );
    },
    [of, mutate, toast],
  );
  return useMemo(() => ({ of, react }), [of, react]);
}

/** How many comments each activity has: the pages' counts, moved as you comment or delete. */
export function useCommentCounts(server: Record<string, number | undefined>) {
  const [delta, setDelta] = useState<Record<string, number>>({});
  const of = useCallback((id: string) => Math.max(0, (server[id] ?? 0) + (delta[id] ?? 0)), [server, delta]);
  const bump = useCallback((id: string, by: number) => setDelta((d) => ({ ...d, [id]: (d[id] ?? 0) + by })), []);
  return useMemo(() => ({ of, bump }), [of, bump]);
}

export const commentsKey = (id: string) => ["activity-comments", id] as const;

/** An activity's comments, threaded, read when its thread is open. */
export function useActivityComments(id: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: commentsKey(id),
    queryFn: () => data(Activities.listActivityComments({ client, path: { activity_id: id } })) as Promise<Comment[]>,
    enabled,
    staleTime: 0, // others comment while the page is open
  });
}

/** Comment or reply on an activity, edit or delete a comment, and resolve or reopen a thread; each refreshes the
 * thread, and adding or deleting moves the line's count. */
export function useActivityCommentActions(id: string, bump: (id: string, by: number) => void) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => qc.invalidateQueries({ queryKey: commentsKey(id) });
  const create = useMutation({
    mutationFn: (body: { text: string; parent?: number | null }) =>
      data(Activities.commentOnActivity({ client, path: { activity_id: id }, body })) as Promise<Comment>,
    onSuccess: () => {
      bump(id, 1);
      void refresh();
    },
    onError: fail("Couldn’t add the comment"),
  });
  const remove = useMutation({
    mutationFn: (c: { cid: number; replies: number }) =>
      data(Activities.deleteActivityComment({ client, path: { activity_id: id, cid: c.cid } })),
    onSuccess: (_, c) => {
      bump(id, -1 - c.replies);
      void refresh();
      toast({ title: "Comment deleted", tone: "green" });
    },
    onError: fail("Couldn’t delete the comment"),
  });
  const update = useMutation({
    mutationFn: ({ cid, ...body }: { cid: number; text?: string; resolved?: boolean }) =>
      data(Activities.updateActivityComment({ client, path: { activity_id: id, cid }, body })) as Promise<Comment>,
    onSuccess: () => void refresh(),
    onError: fail("Couldn’t save the comment"),
  });
  return { create, remove, update };
}
