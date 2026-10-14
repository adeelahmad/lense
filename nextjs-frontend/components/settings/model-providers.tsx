"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { Setup } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

/** A model server found nearby or a cloud provider: its address, the model picking it fills in, and others to offer. */
export type ModelProvider = {
  name: string;
  base_url: string;
  model: string;
  models: string[];
  /** Where a cloud provider hands out API keys; servers found nearby have none. */
  key_url?: string;
};

/**
 * What the model provider step offers in one click (the setup wizard and Settings › LLM provider): OpenAI-compatible
 * servers running on this machine or the Docker host, and cloud providers (backend: setup.CLOUD_PROVIDERS).
 */
export function useModelProviders(enabled: boolean) {
  const client = useApiClient();
  const nearby = useQuery({
    queryKey: ["setup", "llm", "detect"],
    queryFn: () => data(Setup.detectLlm({ client })),
    enabled,
    staleTime: Infinity,
  });
  const cloud = useQuery({
    queryKey: ["setup", "llm", "providers"],
    queryFn: () => data(Setup.llmProviders({ client })),
    enabled,
    staleTime: Infinity,
  });
  const servers = useMemo<ModelProvider[]>(
    () =>
      (nearby.data ?? []).map((s) => ({ name: s.kind, base_url: s.base_url, model: s.suggested, models: s.models })),
    [nearby.data],
  );
  const providers = useMemo<ModelProvider[]>(() => cloud.data ?? [], [cloud.data]);
  return { servers, cloud: providers, looking: enabled && nearby.isPending };
}

/** The address's provider, for the model suggestions and the key link. */
export function providerAt(list: ModelProvider[], baseUrl: string) {
  const at = baseUrl.trim().replace(/\/+$/, "");
  return at ? list.find((p) => p.base_url === at) : undefined;
}

/** Servers found nearby and cloud providers as buttons: picking one fills in its address and model. */
export function ModelProviderPicker({
  servers,
  cloud,
  looking,
  baseUrl,
  onPick,
}: {
  servers: ModelProvider[];
  cloud: ModelProvider[];
  looking: boolean;
  baseUrl: string;
  onPick: (p: ModelProvider) => void;
}) {
  const on = providerAt([...servers, ...cloud], baseUrl);
  const chip = (selected: boolean) =>
    cn(
      "flex flex-col rounded-md border px-3 py-1.5 text-left text-[12.5px]",
      selected ? "border-blue bg-blue-surface" : "border-border hover:border-blue-border",
    );
  return (
    <>
      {looking && <p className="text-[12.5px] text-fg-muted">Looking for model servers nearby…</p>}
      {servers.length > 0 && (
        <div className="flex flex-col gap-1.5" role="group" aria-label="Model servers found">
          <p className="text-[12.5px] font-semibold text-fg">Found running nearby</p>
          <div className="flex flex-wrap gap-2">
            {servers.map((s) => (
              <button
                key={s.base_url}
                type="button"
                onClick={() => onPick(s)}
                aria-pressed={s === on}
                className={chip(s === on)}
              >
                <span className="font-semibold text-fg">{s.name}</span>
                <span className="text-fg-muted">
                  {s.models.length} model{s.models.length === 1 ? "" : "s"} · {s.base_url}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      {cloud.length > 0 && (
        <div className="flex flex-col gap-1.5" role="group" aria-label="Cloud providers">
          <p className="text-[12.5px] font-semibold text-fg">Cloud providers</p>
          <div className="flex flex-wrap gap-2">
            {cloud.map((p) => (
              <button
                key={p.base_url}
                type="button"
                onClick={() => onPick(p)}
                aria-pressed={p === on}
                title={`${p.base_url} · ${p.model}`}
                className={cn(chip(p === on), "font-semibold text-fg")}
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

/** "Get a key from …" for a cloud provider, opened beside Lens. */
export function KeyLink({ provider }: { provider?: ModelProvider }) {
  if (!provider?.key_url) return null;
  return (
    <a
      href={provider.key_url}
      target="_blank"
      rel="noreferrer"
      className="font-semibold text-fg-accent hover:underline"
    >
      Get a key from {provider.name}
    </a>
  );
}
