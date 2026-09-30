"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Collections } from "@/app/openapi-client";
import type { SearchFilters } from "@/components/search/query";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";

/**
 * Save a search. It is kept as a filter collection (words, namespace, speaker), so it also works as a chat scope and
 * for batch runs. Emotion and recording filters can't be part of a collection and are left out.
 */
export function SaveSearchDialog({
  open,
  onOpenChange,
  q,
  filters,
  labels,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  q: string;
  filters: SearchFilters;
  labels: Partial<Record<keyof SearchFilters, string>>;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const save = useMutation({
    mutationFn: () =>
      data(
        Collections.createCollection({
          client,
          body: {
            name: name.trim() || q,
            filter: {
              q,
              ...(filters.namespace ? { namespaces: [filters.namespace] } : {}),
              ...(filters.speaker != null ? { speakers: [filters.speaker] } : {}),
            },
          },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections"] });
      toast({
        title: "Search saved",
        body: "It’s in Saved searches and in your collections.",
        tone: "green",
      });
      onOpenChange(false);
      setName("");
    },
  });
  const dropped = [
    filters.emotion ? `emotion: ${filters.emotion}` : null,
    filters.recording != null ? `recording: ${labels.recording ?? filters.recording}` : null,
  ].filter(Boolean);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Save this search"
      description="It’s kept as a collection that updates as new recordings match, so you can also chat with it or run things on it."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save search"}
          </Button>
        </>
      }
    >
      <Field label="Name">
        {({ id }) => <Input id={id} value={name} placeholder={q} onChange={(e) => setName(e.target.value)} autoFocus />}
      </Field>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 rounded-sm bg-surface px-3.5 py-3 text-[13px]">
        <dt className="text-fg-secondary">Words</dt>
        <dd className="m-0 font-mono text-fg">{q}</dd>
        {filters.namespace && (
          <>
            <dt className="text-fg-secondary">Namespace</dt>
            <dd className="m-0 text-fg">{filters.namespace}</dd>
          </>
        )}
        {filters.speaker != null && (
          <>
            <dt className="text-fg-secondary">Speaker</dt>
            <dd className="m-0 text-fg">{labels.speaker ?? filters.speaker} (recordings they speak in)</dd>
          </>
        )}
      </dl>
      {dropped.length > 0 && (
        <Banner tone="warning">Collections can’t keep {dropped.join(" or ")}; those filters aren’t saved.</Banner>
      )}
      {save.isError && <Banner tone="error">{save.error.message}</Banner>}
    </Dialog>
  );
}
