"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Sparkles, X } from "lucide-react";

import { Topics } from "@/app/openapi-client";
import { Label } from "@/components/ui/panel";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

/**
 * What the namespace's summaries say recordings are about that no topic covers yet. Editors add one to the vocabulary
 * (its recordings then get it as a suggestion to accept) or skip it so it isn't offered again.
 */
export function TopicCandidates({ ns, onAdded }: { ns: string; onAdded: (id: number) => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({
    queryKey: ["topic-candidates", ns],
    queryFn: () => data(Topics.topicCandidates({ client, path: { name: ns } })),
  });
  const failed = (err: Error) =>
    toast({
      title: "Couldn’t change the suggestions",
      body: err instanceof ApiError ? err.message : "Please try again.",
      tone: "red",
    });
  const add = useMutation({
    mutationFn: (label: string) => data(Topics.createTopic({ client, path: { name: ns }, body: { label } })),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ["topic-candidates", ns] });
      qc.invalidateQueries({ queryKey: ["topics"] });
      onAdded(t.id);
    },
    onError: failed,
  });
  const skip = useMutation({
    mutationFn: (label: string) => data(Topics.skipTopicCandidate({ client, path: { name: ns }, body: { label } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["topic-candidates", ns] }),
    onError: failed,
  });
  const items = list.data ?? [];
  if (!items.length) return null;
  const busy = add.isPending || skip.isPending;
  return (
    <section
      className="flex flex-col gap-1.5 rounded-md border border-dashed border-border p-3"
      aria-label="Suggested topics"
    >
      <Label as="h3" className="flex items-center gap-1.5 pb-0.5">
        <Sparkles aria-hidden className="size-[13px]" />
        Suggested by summaries
      </Label>
      <p className="m-0 text-[12.5px] text-fg-muted">
        What recordings are about that no topic covers yet. Add one and its recordings get it as a suggestion to accept.
      </p>
      <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
        {items.map((c) => (
          <li
            key={c.label}
            className="inline-flex h-7 items-center gap-1 rounded-pill border border-border bg-surface pl-1 pr-1 text-[12.5px] font-semibold text-fg"
          >
            <button
              type="button"
              aria-label={`Add ${c.label} as a topic`}
              disabled={busy}
              onClick={() => add.mutate(c.label)}
              className="inline-flex h-6 items-center gap-1 rounded-pill pl-1.5 pr-1 hover:bg-surface-neutral"
            >
              <Plus className="size-3" aria-hidden />
              {c.label}
              <span className="tabular font-normal text-fg-muted">{plural(c.recordings, "recording")}</span>
            </button>
            <button
              type="button"
              aria-label={`Don’t suggest ${c.label}`}
              disabled={busy}
              onClick={() => skip.mutate(c.label)}
              className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
            >
              <X className="size-3" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
