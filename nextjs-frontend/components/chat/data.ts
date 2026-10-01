"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";

import { Chats } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";

export function useChats() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["chats"],
    queryFn: () => data(Chats.listChats({ client })),
    staleTime: 15_000,
  });
}

export function useChat(id: number | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["chat", id],
    queryFn: () => data(Chats.getChat({ client, path: { cid: id as number } })),
    enabled: id != null,
    staleTime: 10_000,
  });
}

export function useApprovals(chatId: number | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["approvals", chatId],
    queryFn: () => data(Chats.listApprovals({ client, query: { chat_id: chatId } })),
    enabled: chatId != null,
    staleTime: 10_000,
  });
}

export type LlmStatus = {
  known: boolean;
  configured: boolean;
  model: string | null;
  tools: boolean;
  /** The models people may pick, the configured one first. */
  models: string[];
};

/** Whether chat has a model, which, and whether it uses tools: anyone signed in can ask (GET /chats/capabilities). */
export function useLlmStatus(): LlmStatus {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["chat-capabilities"],
    queryFn: () => data(Chats.chatCapabilities({ client })),
    staleTime: 60_000,
  });
  if (!q.data) return { known: false, configured: true, model: null, tools: true, models: [] };
  return {
    known: true,
    configured: q.data.configured,
    model: q.data.model ?? null,
    tools: q.data.tools,
    models: q.data.models ?? [],
  };
}

/**
 * Stop an answer. The server ends it after the piece or tool step it's on and saves what came, marked stopped, so the
 * stream closes by itself. When nothing was answering there, the server can't be reached, or it takes longer than
 * `wait` (a model call can't be cut short), stop reading it here instead.
 */
export function useStopAnswer(wait = 15_000) {
  const client = useApiClient();
  return useCallback(
    (cid: number | null | undefined, reading: AbortController | null) => {
      if (!reading) return;
      if (cid == null) return reading.abort();
      window.setTimeout(() => reading.abort(), wait);
      data(Chats.stopAnswer({ client, path: { cid } }))
        .then((r) => {
          if (!r.stopping) reading.abort();
        })
        .catch(() => reading.abort());
    },
    [client, wait],
  );
}
