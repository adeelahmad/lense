"use client";

import { useQuery } from "@tanstack/react-query";
import { FolderInput } from "lucide-react";

import { Notes } from "@/app/openapi-client";
import type { NoteHomeSuggestion } from "@/app/openapi-client/types.gen";
import { data, useApiClient } from "@/lib/api/browser";

/** The project or area a note at the top of the tree fits under (from the links and words they share): one click
 * puts it there. Found without a model; nothing moves on its own. */
export function HomeSuggestion({
  pid,
  version,
  onMove,
  busy,
}: {
  pid: number;
  version: string | null | undefined;
  onMove: (s: NoteHomeSuggestion) => void;
  busy: boolean;
}) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["note-homes", pid, version],
    queryFn: () => data(Notes.homeSuggestions({ client, path: { pid } })),
    staleTime: 30_000,
  });
  const best = q.data?.[0];
  if (!best) return null;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => onMove(best)}
      className="inline-flex items-center gap-1 rounded-pill border border-border px-2 py-0.5 text-fg-accent hover:bg-blue-surface disabled:opacity-50"
      title={`It fits there: ${best.why.join("; ")}`}
    >
      <FolderInput className="size-3.5" />
      Put inside {best.title}
    </button>
  );
}
