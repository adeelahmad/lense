"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, X } from "lucide-react";
import { useState } from "react";

import { Circles } from "@/app/openapi-client";
import type { Circle, Person } from "@/app/openapi-client/types.gen";
import { addable, isHandle, personLabel, remoteLabel, withLet, type Let } from "@/components/circles/model";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Checkbox, Input, Select, Switch } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { RenameTitle } from "@/components/ui/rename-title";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** The person's circles of trust under Profile (docs/circles.md): the people their agent shares with. Only they see
 * their circles. A suggested name makes a circle in one click; deleting one is undone from the toast. */
export function CirclesPanel() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const circles = useQuery({ queryKey: ["circles"], queryFn: () => data(Circles.listCircles({ client })) });
  const people = useQuery({ queryKey: ["circle-people"], queryFn: () => data(Circles.listCirclePeople({ client })) });
  const elsewhere = useQuery({
    queryKey: ["circles-elsewhere"],
    queryFn: () => data(Circles.getElsewhere({ client })),
  });
  const findable = useMutation({
    mutationFn: (on: boolean) => data(Circles.setElsewhere({ client, body: { findable: on } })),
    onSuccess: (got) => qc.setQueryData(["circles-elsewhere"], got),
  });
  const [name, setName] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["circles"] });

  const create = useMutation({
    mutationFn: (body: { name: string; members?: number[] }) => data(Circles.createCircle({ client, body })),
    onSuccess: () => {
      setName("");
      void refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (c: Circle) => data(Circles.deleteCircle({ client, path: { cid: c.id } })),
    onSuccess: (_, c) => {
      void refresh();
      toast({
        title: `Deleted ${c.name}`,
        action: { label: "Undo", onClick: () => create.mutate({ name: c.name, members: c.members.map((m) => m.id) }) },
      });
    },
  });

  const error = create.error ?? remove.error ?? findable.error;
  const far = elsewhere.data?.on ?? false;
  return (
    <section id="circles" className="scroll-mt-4">
      <Panel
        title="Circles"
        subtitle="The people you share with: your family, friends and colleagues. Only you see your circles, and adding someone tells them nothing."
      >
        <div className="flex flex-col gap-3">
          {far && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Switch
                checked={elsewhere.data?.findable ?? false}
                disabled={findable.isPending}
                onCheckedChange={(on) => findable.mutate(on)}
                label="People on other servers can find you and add you to their circles"
                className="text-[13px]"
              />
              {elsewhere.data?.handle && (
                <code className="rounded-sm bg-surface-neutral px-1.5 py-0.5 text-[12.5px] text-fg">
                  {elsewhere.data.handle}
                </code>
              )}
            </div>
          )}
          {circles.isPending ? (
            <Skeleton className="h-20 w-full" />
          ) : (
            circles.data?.circles.map((c) => (
              <CircleRow key={c.id} circle={c} people={people.data ?? []} far={far} onDelete={() => remove.mutate(c)} />
            ))
          )}
          {!!circles.data?.suggested.length && (
            <div className="flex flex-wrap gap-2">
              {circles.data.suggested.map((s) => (
                <Button
                  key={s}
                  size="xs"
                  variant="secondary"
                  icon={<Plus />}
                  disabled={create.isPending}
                  onClick={() => create.mutate({ name: s })}
                >
                  {s}
                </Button>
              ))}
            </div>
          )}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate({ name: name.trim() });
            }}
          >
            <Input
              aria-label="New circle's name"
              placeholder="New circle"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              className="max-w-[300px]"
            />
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              Add circle
            </Button>
          </form>
          {error && <Banner tone="error">{error.message}</Banner>}
        </div>
      </Panel>
    </section>
  );
}

type Change = {
  name?: string;
  add?: number[];
  remove?: number[];
  lets?: Let[];
  about?: string[];
  add_remote?: string[];
  remove_remote?: string[];
};

function CircleRow({
  circle,
  people,
  far,
  onDelete,
}: {
  circle: Circle;
  people: Person[];
  far: boolean;
  onDelete: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const [handle, setHandle] = useState("");
  const update = useMutation({
    mutationFn: (body: Change) => data(Circles.updateCircle({ client, path: { cid: circle.id }, body })),
    onSuccess: (_, body) => {
      if (body.add_remote) setHandle("");
      return qc.invalidateQueries({ queryKey: ["circles"] });
    },
  });
  const remote = circle.remote ?? [];
  const more = addable(people, circle);
  const { me } = useArchive();
  const spaces = Object.keys(me?.roles ?? {}).sort();
  const about = circle.about ?? [];
  const asks = circle.lets?.includes("questions") ?? false;
  return (
    <div className="flex flex-col gap-2 rounded-sm border border-border px-3 py-2.5" data-testid="circle">
      <div className="flex items-center gap-2">
        <RenameTitle
          as="span"
          what="circle"
          name={circle.name}
          onRename={(n) => update.mutateAsync({ name: n })}
          className="min-w-0 flex-1 text-[14px] font-bold text-fg"
        />
        <IconButton label={`Delete ${circle.name}`} size={32} onClick={onDelete}>
          <Trash2 />
        </IconButton>
      </div>
      <ul className="flex flex-wrap gap-1.5">
        {circle.members.map((m) => (
          <li
            key={m.id}
            className="flex items-center gap-1 rounded-full border border-border bg-surface-neutral py-0.5 pl-2.5 pr-1 text-[12.5px] text-fg"
          >
            {personLabel(m)}
            <button
              type="button"
              aria-label={`Take ${personLabel(m)} out of ${circle.name}`}
              onClick={() => update.mutate({ remove: [m.id] })}
              className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-border"
            >
              <X className="size-3" />
            </button>
          </li>
        ))}
        {remote.map((r) => (
          <li
            key={`r${r.id}`}
            title={r.handle}
            className="flex items-center gap-1 rounded-full border border-dashed border-border bg-surface-neutral py-0.5 pl-2.5 pr-1 text-[12.5px] text-fg"
          >
            {remoteLabel(r)}
            <button
              type="button"
              aria-label={`Take ${remoteLabel(r)} out of ${circle.name}`}
              onClick={() => update.mutate({ remove_remote: [r.handle] })}
              className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-border"
            >
              <X className="size-3" />
            </button>
          </li>
        ))}
        {!circle.members.length && !remote.length && <li className="text-[12.5px] text-fg-muted">Nobody yet.</li>}
      </ul>
      {more.length > 0 && (
        <Select
          size="sm"
          aria-label={`Add someone to ${circle.name}`}
          value=""
          onChange={(e) => e.target.value && update.mutate({ add: [Number(e.target.value)] })}
          options={[
            { value: "", label: "Add someone…" },
            ...more.map((p) => ({ value: String(p.id), label: personLabel(p) })),
          ]}
          className="w-[240px]"
        />
      )}
      {far && (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (isHandle(handle)) update.mutate({ add_remote: [handle.trim()] });
          }}
        >
          <Input
            aria-label={`Add someone on another server to ${circle.name}`}
            placeholder="Someone elsewhere, like sam@lens.example"
            value={handle}
            maxLength={200}
            onChange={(e) => setHandle(e.target.value)}
            className="h-8 max-w-[300px] text-[13px]"
          />
          <Button size="sm" type="submit" variant="secondary" disabled={!isHandle(handle) || update.isPending}>
            Add
          </Button>
        </form>
      )}
      <Switch
        checked={circle.lets?.includes("free_busy") ?? false}
        onCheckedChange={(on) => update.mutate({ lets: withLet(circle.lets, "free_busy", on) })}
        label="They can see when you’re free (never what)"
        className="text-[13px]"
      />
      <Switch
        checked={asks}
        onCheckedChange={(on) => update.mutate({ lets: withLet(circle.lets, "questions", on) })}
        label="They can ask your agent questions, answered from:"
        className="text-[13px]"
      />
      {asks && (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 pl-11">
          {spaces.map((ns) => (
            <Checkbox
              key={ns}
              checked={about.includes(ns)}
              onCheckedChange={(on) => update.mutate({ about: on ? [...about, ns] : about.filter((x) => x !== ns) })}
              label={ns}
              className="text-[13px]"
            />
          ))}
          {!spaces.length && <span className="text-[12.5px] text-fg-muted">You have no namespaces to pick.</span>}
        </div>
      )}
      {update.isError && <Banner tone="error">{update.error.message}</Banner>}
    </div>
  );
}
