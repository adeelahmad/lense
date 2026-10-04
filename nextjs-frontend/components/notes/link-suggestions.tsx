"use client";

import { useQuery } from "@tanstack/react-query";
import { Link2 } from "lucide-react";

import { Notes } from "@/app/openapi-client";
import type { NoteLinkSuggestion } from "@/app/openapi-client/types.gen";
import { data, useApiClient } from "@/lib/api/browser";

/** What the note names but doesn't link yet (the namespace's topics, people, organisations...): one click links it,
 * so the note joins the graph. Found by matching the namespace's own vocabulary, without a model. */
export function LinkSuggestions({
  pid,
  version,
  onLink,
  busy,
}: {
  pid: number;
  version: string | null | undefined;
  onLink: (s: NoteLinkSuggestion) => void;
  busy: boolean;
}) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["note-suggestions", pid, version],
    queryFn: () => data(Notes.linkSuggestions({ client, path: { pid } })),
    staleTime: 30_000,
  });
  const items = q.data ?? [];
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-fg-muted" aria-label="Suggested links">
      <Link2 className="size-3.5" aria-hidden />
      <span>Link what it names:</span>
      {items.map((s) => (
        <button
          key={s.target}
          type="button"
          disabled={busy}
          onClick={() => onLink(s)}
          className="rounded-full border border-border px-2 py-0.5 text-fg-strong hover:bg-surface-neutral disabled:opacity-50"
        >
          {s.sign}
          {s.label}
        </button>
      ))}
    </div>
  );
}
