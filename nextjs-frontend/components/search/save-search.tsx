"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Collections, Searches } from "@/app/openapi-client";
import type { SearchFilters } from "@/components/search/query";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";

/**
 * Save a search: its words and every filter, under a name, in Saved searches. Editors can share it with the namespace
 * it searches. It can also be kept as a collection (to chat with it or run things on it), which holds the words,
 * namespace and speaker only.
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
  const { can } = useArchive();
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [collection, setCollection] = useState(false);
  const ns = filters.namespace ?? null;
  const noShare = !ns
    ? "Pick a namespace first: a saved search is shared with the namespace it searches"
    : can("editor", ns)
      ? null
      : needRole("editor", ns);
  const save = useMutation({
    mutationFn: async () => {
      const label = name.trim() || q;
      const saved = await data(
        Searches.createSearch({
          client,
          body: {
            name: label,
            q,
            namespace: ns,
            speaker: filters.speaker ?? null,
            emotion: filters.emotion ?? null,
            recording: filters.recording ?? null,
            shared: shared && !noShare,
          },
        }),
      );
      if (collection)
        await data(
          Collections.createCollection({
            client,
            body: {
              name: label,
              filter: {
                q,
                ...(ns ? { namespaces: [ns] } : {}),
                ...(filters.speaker != null ? { speakers: [filters.speaker] } : {}),
              },
            },
          }),
        );
      return saved;
    },
    onSuccess: (s) => {
      void qc.invalidateQueries({ queryKey: ["saved-searches"] });
      if (collection) void qc.invalidateQueries({ queryKey: ["collections"] });
      toast({
        title: "Search saved",
        body: s.shared
          ? `Everyone in ${s.namespace} finds it in Saved searches.`
          : collection
            ? "It’s in Saved searches, and in your collections."
            : "It’s in Saved searches.",
        tone: "green",
      });
      onOpenChange(false);
      setName("");
      setShared(false);
      setCollection(false);
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
      description="Its words and filters stay in Saved searches, so you can run it again whenever you like."
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
        {({ id }) => (
          <Input
            id={id}
            value={name}
            placeholder={q}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        )}
      </Field>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 rounded-sm bg-surface px-3.5 py-3 text-[13px]">
        <dt className="text-fg-secondary">Words</dt>
        <dd className="m-0 font-mono text-fg">{q}</dd>
        {ns && (
          <>
            <dt className="text-fg-secondary">Namespace</dt>
            <dd className="m-0 text-fg">{ns}</dd>
          </>
        )}
        {filters.speaker != null && (
          <>
            <dt className="text-fg-secondary">Speaker</dt>
            <dd className="m-0 text-fg">{labels.speaker ?? filters.speaker}</dd>
          </>
        )}
        {filters.emotion && (
          <>
            <dt className="text-fg-secondary">Emotion</dt>
            <dd className="m-0 text-fg">{filters.emotion}</dd>
          </>
        )}
        {filters.recording != null && (
          <>
            <dt className="text-fg-secondary">Recording</dt>
            <dd className="m-0 text-fg">{labels.recording ?? filters.recording}</dd>
          </>
        )}
      </dl>
      <div className="flex flex-col gap-1">
        <Checkbox
          checked={shared && !noShare}
          disabled={Boolean(noShare)}
          onCheckedChange={setShared}
          label={`Share with ${ns ?? "its namespace"}`}
        />
        <span className="pl-7 text-[12.5px] text-fg-muted">
          {noShare ?? "Everyone with a role there sees it; only you can change it."}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <Checkbox checked={collection} onCheckedChange={setCollection} label="Also keep it as a collection" />
        <span className="pl-7 text-[12.5px] text-fg-muted">
          A collection updates as new recordings match, so you can chat with it or run things on it.
        </span>
      </div>
      {collection && dropped.length > 0 && (
        <Banner tone="warning">Collections can’t keep {dropped.join(" or ")}; the collection leaves those out.</Banner>
      )}
      {save.isError && <Banner tone="error">{save.error.message}</Banner>}
    </Dialog>
  );
}
