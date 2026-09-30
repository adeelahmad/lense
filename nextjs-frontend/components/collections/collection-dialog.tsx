"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Collections } from "@/app/openapi-client";
import type { Collection } from "@/app/openapi-client/types.gen";
import type { CollectionFilter } from "@/components/collections/describe";
import { useRecordingIndex } from "@/components/search/data";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, SearchInput, Select, Switch, Textarea } from "@/components/ui/field";
import { Segmented } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** Create or change a collection: a filter that keeps up to date, or a fixed list of recordings. */
export function CollectionDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  editing?: Collection | null;
  onSaved?: (id: number) => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const { namespaces } = useArchive();
  const index = useRecordingIndex(open);
  const f0 = (editing?.filter ?? {}) as CollectionFilter;
  const [name, setName] = useState(editing?.name ?? "");
  const [description, setDescription] = useState(editing?.description ?? "");
  const [kind, setKind] = useState<"filter" | "fixed">(editing?.kind ?? "filter");
  const [nss, setNss] = useState<string[]>(f0.namespaces ?? []);
  const [q, setQ] = useState(f0.q ?? "");
  const [from, setFrom] = useState(f0.from ?? "");
  const [to, setTo] = useState(f0.to ?? "");
  const [media, setMedia] = useState(f0.media ?? "");
  const [picked, setPicked] = useState<number[]>(editing?.recordings ?? []);
  const [find, setFind] = useState("");
  const [shared, setShared] = useState(editing?.shared ?? false);
  const filter: CollectionFilter = {
    ...(nss.length ? { namespaces: nss } : {}),
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(from ? { from } : {}),
    ...(to ? { to } : {}),
    ...(media ? { media } : {}),
    ...(f0.speakers ? { speakers: f0.speakers } : {}),
    ...(f0.entities ? { entities: f0.entities } : {}),
  };
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        shared,
        ...(kind === "filter" ? { filter } : { recordings: picked }),
      };
      if (editing) {
        await data(
          Collections.updateCollection({
            client,
            path: { cid: editing.id },
            body,
          }),
        );
        return editing.id;
      }
      return (await data(Collections.createCollection({ client, body }))).id;
    },
    onSuccess: (id) => {
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.invalidateQueries({ queryKey: ["collection", id] });
      onOpenChange(false);
      onSaved?.(id);
    },
  });
  const t = find.trim().toLowerCase();
  const reason = !name.trim()
    ? "Give it a name"
    : kind === "fixed" && !picked.length
      ? "Pick at least one recording"
      : undefined;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title={editing ? "Edit collection" : "New collection"}
      description="Use a collection as a chat scope, for batch runs and for collection reports. It only ever lists recordings the viewer can read."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={Boolean(reason) || save.isPending}
            disabledReason={reason}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : editing ? "Save" : "Create collection"}
          </Button>
        </>
      }
    >
      <Field label="Name">
        {({ id }) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} autoFocus />}
      </Field>
      <Field label="Description" optional>
        {({ id }) => <Textarea id={id} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      {!editing && (
        <Segmented
          value={kind}
          onChange={(v) => setKind(v as "filter" | "fixed")}
          className="self-start"
          items={[
            { value: "filter", label: "A filter (keeps up to date)" },
            { value: "fixed", label: "A fixed list" },
          ]}
        />
      )}
      {kind === "filter" ? (
        <div className="flex flex-col gap-3">
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1.5 text-[13px] font-bold text-fg-strong">Namespaces</legend>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {namespaces.map((n) => (
                <Checkbox
                  key={n.name}
                  checked={nss.includes(n.name)}
                  onCheckedChange={(on) => setNss((x) => (on ? [...x, n.name] : x.filter((y) => y !== n.name)))}
                  label={n.name}
                />
              ))}
            </div>
            <span className="text-[12px] text-fg-muted">None ticked: every namespace the viewer can read.</span>
          </fieldset>
          <Field label="Words said" optional hint="The same rules as Search: every word, “phrases”, OR.">
            {({ id, describedBy }) => (
              <Input id={id} aria-describedby={describedBy} value={q} onChange={(e) => setQ(e.target.value)} mono />
            )}
          </Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Recorded from" optional>
              {({ id }) => <Input id={id} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />}
            </Field>
            <Field label="Recorded until" optional>
              {({ id }) => <Input id={id} type="date" value={to} onChange={(e) => setTo(e.target.value)} />}
            </Field>
            <Field label="Media" optional>
              {({ id }) => (
                <Select
                  id={id}
                  value={media}
                  onChange={(e) => setMedia(e.target.value)}
                  options={[
                    { value: "", label: "Any" },
                    { value: "audio", label: "Audio" },
                    { value: "video", label: "Video" },
                    { value: "transcript", label: "Transcript only" },
                  ]}
                />
              )}
            </Field>
          </div>
          {Boolean(f0.speakers?.length || f0.entities?.length) && (
            <p className="m-0 text-[12.5px] text-fg-muted">This filter also keeps its speaker and entity conditions.</p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <SearchInput
            value={find}
            onChange={(e) => setFind(e.target.value)}
            placeholder="Find recordings"
            aria-label="Find recordings"
          />
          <ul className="m-0 max-h-[260px] list-none overflow-y-auto rounded-md border border-border p-2">
            {index.isLoading && <li className="p-2 text-[13px] text-fg-muted">Loading recordings…</li>}
            {(index.data ?? [])
              .filter((r) => !t || (r.title ?? "").toLowerCase().includes(t))
              .slice(0, 100)
              .map((r) => (
                <li key={r.id} className="flex items-center gap-2 px-1 py-1">
                  <Checkbox
                    checked={picked.includes(r.id)}
                    onCheckedChange={(on) => setPicked((x) => (on ? [...x, r.id] : x.filter((y) => y !== r.id)))}
                    label={r.title ?? `Recording ${r.id}`}
                  />
                  <span className="ml-auto shrink-0 text-[12px] text-fg-muted">{r.namespace}</span>
                </li>
              ))}
          </ul>
          <span className="text-[12.5px] text-fg-secondary">{picked.length} picked</span>
        </div>
      )}
      <Switch
        checked={shared}
        onCheckedChange={setShared}
        label="Share it (others see only the recordings they can read)"
      />
      {save.isError && <Banner tone="error">{save.error.message}</Banner>}
    </Dialog>
  );
}
