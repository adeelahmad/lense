"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Entities } from "@/app/openapi-client";
import type { EntitySetup, EntityTypeInfo } from "@/app/openapi-client/types.gen";
import { keptTypes, MATCHING, type Mode, MODES, typesWording } from "@/components/entities/model";
import { useCollectionTree } from "@/components/library/use-collections";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Select, Textarea } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural, shortDate } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

export const setupKey = (ns: string) => ["entity-setup", ns] as const;

export function useEntitySetup(ns: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: setupKey(ns),
    queryFn: () => data(Entities.getEntitySetup({ client, path: { name: ns } })),
  });
}

/** One setup's form: the types kept and what the place is about. */
function SetupForm({
  ns,
  setup,
  types,
  collection,
  onDone,
}: {
  ns: string;
  setup: EntitySetup;
  types: EntityTypeInfo[];
  collection: number | null;
  onDone?: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>((setup.mode as Mode) ?? "self");
  const [matching, setMatching] = useState<"rules" | "model">(setup.matching === "model" ? "model" : "rules");
  const [kept, setKept] = useState<string[]>(setup.types ?? []);
  const [description, setDescription] = useState(setup.description ?? "");
  useEffect(() => {
    setMode((setup.mode as Mode) ?? "self");
    setMatching(setup.matching === "model" ? "model" : "rules");
    setKept(setup.types ?? []);
    setDescription(setup.description ?? "");
  }, [setup]);
  const apply = useMutation({
    mutationFn: () => data(Entities.applyEntitySetup({ client, path: { name: ns }, body: { collection } })),
    onSuccess: (r) =>
      toast({
        title: r.recordings
          ? `Analysing ${plural(r.recordings, "recording")} again`
          : "No analysed recordings to update",
        body: r.recordings ? "Their entities follow this setup once the jobs finish (Activity)." : undefined,
        tone: "green",
      }),
    onError: (e) =>
      toast({ title: "Couldn’t start it", body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" }),
  });
  const words = typesWording(mode);
  const save = useMutation({
    mutationFn: () =>
      data(
        Entities.saveEntitySetup({
          client,
          path: { name: ns },
          body: { collection, mode, types: kept, description, matching },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: setupKey(ns) });
      toast({ title: "Saved. Recordings analysed from now on follow it.", tone: "green" });
      onDone?.();
    },
    onError: (e) =>
      toast({ title: "Couldn’t save", body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" }),
  });
  const can = setup.can_change ?? false;
  const all = !kept.length;
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-[13px] font-bold text-fg-strong">How entities are organised</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {MODES.map((m) => (
            <label
              key={m.value}
              className={cn(
                "flex cursor-pointer gap-2.5 rounded-md border p-3 transition-colors duration-fast",
                mode === m.value ? "border-blue bg-blue-surface" : "border-border hover:bg-surface",
                !can && "cursor-not-allowed opacity-70",
              )}
            >
              <input
                type="radio"
                name={`mode-${collection ?? "ns"}`}
                value={m.value}
                checked={mode === m.value}
                disabled={!can}
                onChange={() => setMode(m.value)}
                className="mt-0.5 accent-[var(--aladdin-blue)]"
              />
              <span className="flex flex-col gap-0.5">
                <span className="text-[14px] font-bold text-fg">{m.label}</span>
                <span className="text-[12.5px] leading-snug text-fg-secondary">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
        {mode === "fixed" && (
          <p className="m-0 text-[12.5px] text-fg-secondary">
            Define the entities on the Entities tab with “Add entity”. Unknown and Unlabeled are always there.
          </p>
        )}
      </fieldset>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 text-[13px] font-bold text-fg-strong">{words.legend}</legend>
        <Checkbox
          checked={all}
          disabled={!can}
          label={words.all}
          onCheckedChange={(v) => setKept(v ? [] : types.filter((t) => !t.quiet).map((t) => t.type))}
        />
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 pl-7 sm:grid-cols-3">
          {types.map((t) => (
            <Checkbox
              key={t.type}
              checked={all ? !(mode === "fixed" && t.quiet) : kept.includes(t.type)}
              disabled={!can || all}
              label={t.label}
              onCheckedChange={(v) => setKept((k) => (v ? [...k, t.type] : k.filter((x) => x !== t.type)))}
            />
          ))}
        </div>
        <p className="m-0 text-[12.5px] text-fg-muted">{words.hint}</p>
      </fieldset>
      <Field
        label="What this is about"
        optional
        hint="A sentence or two about what is recorded here. It helps tell which names belong."
      >
        {(ids) => (
          <Textarea
            id={ids.id}
            aria-describedby={ids.describedBy}
            rows={3}
            maxLength={2000}
            value={description}
            disabled={!can}
            placeholder={can ? "e.g. Weekly calls with our biotech clients" : "No description"}
            onChange={(e) => setDescription(e.target.value)}
          />
        )}
      </Field>
      <Field label="Matching names" hint={MATCHING.find((m) => m.value === matching)?.hint}>
        {(ids) => (
          <Select
            id={ids.id}
            aria-describedby={ids.describedBy}
            value={matching}
            disabled={!can}
            onChange={(e) => setMatching(e.target.value as "rules" | "model")}
            options={MATCHING.map((m) => ({ value: m.value, label: m.label }))}
          />
        )}
      </Field>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          size="sm"
          variant="primary"
          disabled={!can || save.isPending || (!all && !kept.length)}
          disabledReason={!can ? needRole("editor", ns) : undefined}
        >
          Save
        </Button>
        {onDone && (
          <Button type="button" size="sm" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        )}
        {setup.updated_at && !onDone && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={!can || apply.isPending}
            disabledReason={!can ? needRole("editor", ns) : undefined}
            onClick={() => apply.mutate()}
          >
            Apply to analysed recordings
          </Button>
        )}
        {setup.updated_at && (
          <span className="text-[12.5px] text-fg-muted">
            Saved {shortDate(setup.updated_at)}
            {setup.updated_by ? ` by ${setup.updated_by}` : ""}
          </span>
        )}
      </div>
    </form>
  );
}

/** How the namespace organises its entities, and the collections that do it their own way. */
export function SetupTab({ ns }: { ns: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const view = useEntitySetup(ns);
  const tree = useCollectionTree(ns);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [adding, setAdding] = useState<string>("");
  const clear = useMutation({
    mutationFn: (cid: number) => data(Entities.clearEntitySetup({ client, path: { name: ns, cid } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: setupKey(ns) }),
    onError: (e) => toast({ title: "Couldn’t remove it", body: e instanceof ApiError ? e.message : "", tone: "red" }),
  });

  if (view.isLoading) return <SkeletonRows rows={4} />;
  if (view.isError) return <Banner tone="error">{view.error.message}</Banner>;
  const v = view.data!;
  const types = v.types ?? [];
  const own = new Set((v.collections ?? []).map((c) => c.collection));
  const free = (tree.data ?? []).filter((c) => !own.has(c.id));
  const newSetup: EntitySetup = {
    ...v.namespace,
    collection: adding ? Number(adding) : null,
    updated_at: null,
    updated_by: null,
    can_change: v.can_change,
  };

  return (
    <div className="flex max-w-[860px] flex-col gap-4">
      <Panel title={ns} subtitle="The namespace’s setup. Recordings analysed from now on follow it.">
        <SetupForm ns={ns} setup={v.namespace} types={types} collection={null} />
      </Panel>
      <Panel
        title="Collections with their own setup"
        subtitle="A collection’s setup holds for the collections inside it too."
        actions={
          editing !== "new" && (
            <Button
              size="sm"
              disabled={!free.length}
              disabledReason={!free.length ? "Every collection has its own setup" : undefined}
              onClick={() => {
                setAdding(String(free[0]?.id ?? ""));
                setEditing("new");
              }}
            >
              Add a collection
            </Button>
          )
        }
      >
        <div className="flex flex-col gap-3">
          {editing === "new" && (
            <div className="flex flex-col gap-3 rounded-md border border-border p-3">
              <Field label="Collection">
                {(ids) => (
                  <Select
                    id={ids.id}
                    value={adding}
                    onChange={(e) => setAdding(e.target.value)}
                    options={free.map((c) => ({ value: String(c.id), label: (c.path ?? [c.name]).join(" / ") }))}
                  />
                )}
              </Field>
              {adding && (
                <SetupForm
                  key={adding}
                  ns={ns}
                  setup={newSetup}
                  types={types}
                  collection={Number(adding)}
                  onDone={() => setEditing(null)}
                />
              )}
            </div>
          )}
          {!(v.collections ?? []).length && editing !== "new" && (
            <p className="m-0 text-[13px] text-fg-secondary">Every collection follows the namespace’s setup.</p>
          )}
          {(v.collections ?? []).map((c) => (
            <div key={c.collection} className="flex flex-col gap-3 rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="flex-1 font-semibold text-fg">{(c.collection_path ?? []).join(" / ")}</span>
                {editing !== c.collection && (
                  <>
                    <Button
                      size="xs"
                      variant="ghost"
                      disabled={!c.can_change}
                      disabledReason={!c.can_change ? needRole("editor", ns) : undefined}
                      onClick={() => setEditing(c.collection!)}
                    >
                      Edit
                    </Button>
                    <Button
                      size="xs"
                      variant="danger-ghost"
                      disabled={!c.can_change || clear.isPending}
                      disabledReason={!c.can_change ? needRole("editor", ns) : undefined}
                      onClick={() => clear.mutate(c.collection!)}
                    >
                      Follow the namespace
                    </Button>
                  </>
                )}
              </div>
              {editing === c.collection ? (
                <SetupForm ns={ns} setup={c} types={types} collection={c.collection!} onDone={() => setEditing(null)} />
              ) : (
                <p className="m-0 text-[13px] text-fg-secondary">
                  {MODES.find((m) => m.value === c.mode)?.label ?? c.mode} · {keptTypes(c.types ?? [], types)}
                  {c.description ? ` · ${c.description}` : ""}
                </p>
              )}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
