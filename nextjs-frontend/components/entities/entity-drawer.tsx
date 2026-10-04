"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, History, Waypoints } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Entities } from "@/app/openapi-client";
import type { EntityType } from "@/app/openapi-client/types.gen";
import { recordingHref } from "@/components/search/links";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Drawer } from "@/components/ui/dialog";
import { splitAliases } from "@/components/entities/model";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count, shortDate } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

type Mention = {
  recording_id: number;
  title?: string | null;
  t0?: number | null;
  time?: string | null;
  speaker?: string | null;
  text?: string | null;
};

/** One entity: what it is (name, type, description), where it's said, and hiding it. Editors change it here. */
export function EntityDrawer({
  id,
  ns,
  types,
  onClose,
}: {
  id: number;
  ns: string;
  types: EntityType[];
  onClose: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useArchive();
  const canEdit = can("editor", ns);
  const e = useQuery({
    queryKey: ["entity", id],
    queryFn: () => data(Entities.getEntity({ client, path: { eid: id } })),
  });
  const mentions = useQuery({
    queryKey: ["entity-mentions", id],
    queryFn: () => data(Entities.listEntityMentions({ client, path: { eid: id }, query: { limit: 5 } })),
  });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [aliases, setAliases] = useState("");
  useEffect(() => {
    if (e.data) {
      setName(e.data.name);
      setDescription(e.data.description ?? "");
      setAliases((e.data.aliases ?? []).join(", "));
    }
  }, [e.data]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["entity", id] });
    qc.invalidateQueries({ queryKey: ["entities"] });
  };
  const fail = (title: string) => (err: unknown) =>
    toast({ title, body: err instanceof ApiError ? err.message : "Please try again.", tone: "red" });

  const save = useMutation({
    mutationFn: async () => {
      const d = e.data!;
      if (name.trim() && name.trim() !== d.name)
        await data(Entities.renameEntity({ client, path: { eid: id }, body: { name: name.trim() } }));
      const body: { description?: string; aliases?: string[] } = {};
      if (description.trim() !== (d.description ?? "")) body.description = description;
      if (aliasesChanged) body.aliases = splitAliases(aliases);
      if (Object.keys(body).length) await data(Entities.updateEntity({ client, path: { eid: id }, body }));
    },
    onSuccess: () => {
      refresh();
      toast({ title: "Saved", tone: "green" });
    },
    onError: fail("Couldn’t save the entity"),
  });
  const retype = useMutation({
    mutationFn: (type: string) => data(Entities.retypeEntities({ client, body: { ids: [id], type } })),
    onSuccess: refresh,
    onError: fail("Couldn’t change the type"),
  });
  const listed = useMutation({
    mutationFn: (defined: boolean) => data(Entities.updateEntity({ client, path: { eid: id }, body: { defined } })),
    onSuccess: refresh,
    onError: fail("Couldn’t change it"),
  });
  const remove = useMutation({
    mutationFn: () => data(Entities.deleteEntity({ client, path: { eid: id } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["entities"] });
      toast({ title: "Taken off the list", tone: "green" });
      onClose();
    },
    onError: fail("Couldn’t delete it"),
  });
  const hide = useMutation({
    mutationFn: (hidden: boolean) => data(Entities.hideEntity({ client, path: { eid: id }, body: { hidden } })),
    onSuccess: (_, hidden) => {
      refresh();
      toast({ title: hidden ? "Hidden from lists and the graph" : "Shown again", tone: "green" });
    },
    onError: fail("Couldn’t change it"),
  });

  const d = e.data;
  const aliasesChanged = Boolean(d) && splitAliases(aliases).join("\n") !== (d!.aliases ?? []).join("\n");
  const dirty =
    Boolean(d) && (name.trim() !== d!.name || description.trim() !== (d!.description ?? "") || aliasesChanged);
  const builtin = Boolean(d?.builtin);
  return (
    <Drawer
      open
      onOpenChange={(o) => !o && onClose()}
      title={d?.name ?? "Entity"}
      width={440}
      actions={
        d && (
          <>
            <Button asChild size="xs" variant="ghost" icon={<History />}>
              <Link href={`/routines/history?entity=${id}`}>History</Link>
            </Button>
            <Button asChild size="xs" variant="ghost" icon={<Waypoints />}>
              <Link href={`/graph?focus=e${id}`}>Graph</Link>
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-5 p-4">
        {e.isLoading && <Skeleton className="h-40 w-full" />}
        {e.isError && <Banner tone="error">{e.error.message}</Banner>}
        {d && (
          <>
            <dl className="m-0 grid grid-cols-3 gap-2 text-center">
              {[
                ["Mentions", count(d.mentions)],
                ["Recordings", count(d.recordings)],
                ["Last said", d.last ? shortDate(d.last) : "—"],
              ].map(([k, v]) => (
                <div key={k} className="rounded-md border border-border px-2 py-2">
                  <dt className="text-[11.5px] font-semibold text-fg-muted">{k}</dt>
                  <dd className="tabular m-0 text-[15px] font-bold text-fg">{v}</dd>
                </div>
              ))}
            </dl>
            <form
              className="flex flex-col gap-3"
              onSubmit={(ev) => {
                ev.preventDefault();
                save.mutate();
              }}
            >
              <Field label="Name">
                {(ids) => (
                  <Input
                    id={ids.id}
                    value={name}
                    disabled={!canEdit || builtin}
                    maxLength={200}
                    onChange={(ev) => setName(ev.target.value)}
                  />
                )}
              </Field>
              <Field label="Type">
                {(ids) => (
                  <Select
                    id={ids.id}
                    value={d.type}
                    disabled={!canEdit || builtin || retype.isPending}
                    onChange={(ev) => retype.mutate(ev.target.value)}
                    options={[
                      ...types.map((t) => ({ value: t.type, label: t.label })),
                      ...(types.some((t) => t.type === d.type) ? [] : [{ value: d.type, label: d.type_label }]),
                    ]}
                  />
                )}
              </Field>
              <Field
                label="Description"
                optional
                hint="What this is, in your words. The assistant reads it when it talks about this entity."
              >
                {(ids) => (
                  <Textarea
                    id={ids.id}
                    aria-describedby={ids.describedBy}
                    rows={4}
                    maxLength={2000}
                    value={description}
                    disabled={!canEdit}
                    placeholder={canEdit ? "e.g. Our hosting provider since 2021" : "No description"}
                    onChange={(ev) => setDescription(ev.target.value)}
                  />
                )}
              </Field>
              {!builtin && (
                <Field label="Also said as" optional hint="Other names and spellings, separated by commas.">
                  {(ids) => (
                    <Input
                      id={ids.id}
                      aria-describedby={ids.describedBy}
                      value={aliases}
                      disabled={!canEdit}
                      onChange={(ev) => setAliases(ev.target.value)}
                    />
                  )}
                </Field>
              )}
              {!builtin && (
                <Checkbox
                  checked={Boolean(d.defined)}
                  disabled={!canEdit || listed.isPending}
                  label="On the fixed list"
                  onCheckedChange={(v) => listed.mutate(v)}
                />
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  type="submit"
                  size="sm"
                  variant="primary"
                  disabled={!canEdit || !dirty || save.isPending}
                  disabledReason={!canEdit ? needRole("editor", ns) : undefined}
                >
                  Save
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  icon={d.hidden ? <Eye /> : <EyeOff />}
                  disabled={!canEdit || builtin || hide.isPending}
                  disabledReason={!canEdit ? needRole("editor", ns) : undefined}
                  onClick={() => hide.mutate(!d.hidden)}
                >
                  {d.hidden ? "Show again" : "Hide"}
                </Button>
                {d.defined && !d.mentions && (
                  <Button
                    type="button"
                    size="sm"
                    variant="danger-ghost"
                    disabled={!canEdit || remove.isPending}
                    onClick={() => remove.mutate()}
                  >
                    Delete
                  </Button>
                )}
              </div>
            </form>
            <section className="flex flex-col gap-2">
              <h3 className="m-0 text-[13px] font-bold text-fg-strong">Where it’s said</h3>
              {mentions.isLoading && <Skeleton className="h-24 w-full" />}
              {mentions.data && !mentions.data.items.length && (
                <p className="m-0 text-[13px] text-fg-secondary">No mentions you can read.</p>
              )}
              <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
                {((mentions.data?.items ?? []) as unknown as Mention[]).map((m, i) => (
                  <li key={i} className="flex flex-col gap-[3px]">
                    <Link
                      href={recordingHref(m.recording_id, m.t0 ?? undefined)}
                      className="text-[12px] font-semibold text-fg-secondary hover:text-fg-accent hover:underline"
                    >
                      {m.title ?? `Recording ${m.recording_id}`}
                      {m.time ? ` · ${m.time}` : ""}
                      {m.speaker ? ` · ${m.speaker}` : ""}
                    </Link>
                    <span className="font-serif text-[13.5px] leading-[1.4] text-fg">{m.text}</span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
    </Drawer>
  );
}
