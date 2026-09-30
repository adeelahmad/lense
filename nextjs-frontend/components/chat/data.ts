"use client";

import { useQuery } from "@tanstack/react-query";

import { Admin, Chats } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

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
};

/**
 * Whether chat has a model. Only admins can read Settings, so for everyone else this stays unknown until an answer
 * shows it (the backend then answers with the best passages instead).
 */
export function useLlmStatus(): LlmStatus {
  const client = useApiClient();
  const { admin } = useArchive();
  const q = useQuery({
    queryKey: ["settings"],
    queryFn: () => data(Admin.getSettings({ client })),
    enabled: admin,
    staleTime: 60_000,
  });
  const llm = ((q.data?.llm as { values?: Record<string, unknown> } | undefined)?.values ?? {}) as {
    base_url?: string | null;
    model?: string | null;
  };
  const ai = ((q.data?.ai as { values?: Record<string, unknown> } | undefined)?.values ?? {}) as { tools?: boolean };
  if (!q.data) return { known: false, configured: true, model: null, tools: true };
  return {
    known: true,
    configured: Boolean(llm.base_url && llm.model),
    model: llm.model ?? null,
    tools: ai.tools !== false,
  };
}
