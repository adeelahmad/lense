"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { Replies } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";

export const REPLIES_KEY = ["replies"] as const;
export const COUNT_KEY = ["replies", "count"] as const;

/** How many emails wait for an answer, drafts among them (GET /api/v1/replies), for the nav: looked at again every
 * minute, and none at all for a Lens that reads no mail. */
export function useReplyCount() {
  const client = useApiClient();
  return useQuery({
    queryKey: COUNT_KEY,
    queryFn: () => data(Replies.listReplies({ client, query: { limit: 50 } })).then((l) => l.emails.length),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** After a draft, an edit or setting one aside: the page and the count read again. */
export function useRepliesChanged() {
  const qc = useQueryClient();
  return useCallback(() => void qc.invalidateQueries({ queryKey: REPLIES_KEY }), [qc]);
}
