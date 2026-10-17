"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { Chats } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";

export const COUNT_KEY = ["approvals", "count"] as const;
export const INBOX_KEY = ["approvals", "inbox"] as const;

/** How many approvals wait for the person (GET /api/v1/approvals/count), for the top bar's badge, the nav and Home:
 * one query for all of them, looked at again every minute. */
export function useApprovalCount() {
  const client = useApiClient();
  return useQuery({
    queryKey: COUNT_KEY,
    queryFn: () => data(Chats.countApprovals({ client })),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** After a pick: the inbox, the count, and a conversation's own approvals read again. */
export function useApprovalsChanged() {
  const qc = useQueryClient();
  return useCallback(() => void qc.invalidateQueries({ queryKey: ["approvals"] }), [qc]);
}
