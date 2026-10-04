"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Tags, X } from "lucide-react";
import Link from "next/link";

import { Topics } from "@/app/openapi-client";
import type { TopicItem } from "@/components/topics/model";
import { Label } from "@/components/ui/panel";
import { Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

type Mine = { id: number; label: string; source?: string | null; status?: string | null };

/**
 * The topics a recording is about, from its namespace's vocabulary. Editors of the namespace add and remove them and
 * accept the suggested ones; everyone else sees the accepted ones.
 */
export function RecordingTopics({ id, ns, canEdit }: { id: number; ns: string | null; canEdit: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const mine = useQuery({
    queryKey: ["recording-topics", id],
    queryFn: () => data(Topics.recordingTopics({ client, path: { rid: id } })) as Promise<Mine[]>,
  });
  const vocab = useQuery({
    queryKey: ["topics", ns],
    queryFn: () => data(Topics.listTopics({ client, query: { ns: ns!, limit: 1000 } })),
    enabled: Boolean(ns) && canEdit,
    staleTime: 30_000,
  });
  const tag = useMutation({
    mutationFn: ({ topic, remove }: { topic: number; remove: boolean }) =>
      data(Topics.tagRecordings({ client, path: { tid: topic }, body: { recordings: [id], remove } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recording-topics", id] });
      qc.invalidateQueries({ queryKey: ["topics"] });
      qc.invalidateQueries({ queryKey: ["topic"] });
    },
    onError: (err) =>
      toast({
        title: "Couldn’t change the topics",
        body: err instanceof ApiError ? err.message : "Please try again.",
        tone: "red",
      }),
  });
  const rows = mine.data ?? [];
  const accepted = rows.filter((t) => t.status === "accepted");
  const suggested = canEdit ? rows.filter((t) => t.status === "suggested") : [];
  const left = ((vocab.data?.items ?? []) as TopicItem[]).filter((t) => !rows.some((r) => r.id === t.id));
  if (!rows.length && !canEdit) return null;
  return (
    <section className="flex flex-col gap-1.5" aria-label="Topics">
      <Label as="h3" className="flex items-center gap-1.5 pb-0.5">
        <Tags aria-hidden className="size-[13px]" />
        Topics
      </Label>
      {!rows.length && (
        <p className="m-0 text-[12.5px] text-fg-muted">
          Not about any topic yet. Topics come from the namespace’s{" "}
          <Link href={`/topics${ns ? `?ns=${encodeURIComponent(ns)}` : ""}`} className="text-fg-accent hover:underline">
            vocabulary
          </Link>
          .
        </p>
      )}
      {(accepted.length > 0 || suggested.length > 0) && (
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {accepted.map((t) => (
            <li
              key={t.id}
              className="inline-flex h-7 items-center gap-1 rounded-pill border border-border bg-surface pl-2.5 pr-1 text-[12.5px] font-semibold"
            >
              <Link href={`/topics/${t.id}`} className="text-fg hover:text-fg-accent hover:underline">
                {t.label}
              </Link>
              {canEdit ? (
                <button
                  type="button"
                  aria-label={`Not about ${t.label}`}
                  disabled={tag.isPending}
                  onClick={() => tag.mutate({ topic: t.id, remove: true })}
                  className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                >
                  <X className="size-3" />
                </button>
              ) : (
                <span className="w-1" />
              )}
            </li>
          ))}
          {suggested.map((t) => (
            <li
              key={t.id}
              className="inline-flex h-7 items-center gap-1 rounded-pill border border-dashed border-border pl-2.5 pr-1 text-[12.5px] font-medium text-fg-secondary"
              title="Suggested: accept it, or dismiss it"
            >
              {t.label}
              <button
                type="button"
                aria-label={`Accept ${t.label}`}
                disabled={tag.isPending}
                onClick={() => tag.mutate({ topic: t.id, remove: false })}
                className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
              >
                <Check className="size-3" />
              </button>
              <button
                type="button"
                aria-label={`Dismiss ${t.label}`}
                disabled={tag.isPending}
                onClick={() => tag.mutate({ topic: t.id, remove: true })}
                className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
              >
                <X className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {canEdit && left.length > 0 && (
        <Select
          size="sm"
          className="w-full sm:w-[260px]"
          aria-label="Add a topic"
          value=""
          disabled={tag.isPending}
          onChange={(e) => e.target.value && tag.mutate({ topic: Number(e.target.value), remove: false })}
          options={[
            { value: "", label: "Add a topic…" },
            ...left.map((t) => ({ value: String(t.id), label: t.label })),
          ]}
        />
      )}
    </section>
  );
}
