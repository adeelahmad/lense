"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Check, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import { Activities } from "@/app/openapi-client";
import type { TimelineView } from "@/app/openapi-client/types.gen";
import { fromViewState, sameFilters, toViewState, viewName, type Filters } from "@/components/timeline/model";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

const KEY = ["timeline-views"] as const;

/** The person's saved views of the timeline, and saving, updating and deleting them (deleting can be undone). */
export function useTimelineViews() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({
    queryKey: KEY,
    queryFn: () => data(Activities.listTimelineViews({ client })) as Promise<TimelineView[]>,
  });
  const fail = (title: string) => (e: unknown) =>
    toast({ title, body: e instanceof ApiError ? e.message : "Please try again.", tone: "red" });
  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  const create = useMutation({
    mutationFn: (v: { name: string; f: Filters }) =>
      data(
        Activities.createTimelineView({ client, body: { name: v.name, state: toViewState(v.f) } }),
      ) as Promise<TimelineView>,
    onSuccess: () => void refresh(),
    onError: fail("Couldn’t save the view"),
  });
  const update = useMutation({
    mutationFn: (v: { id: number; f: Filters }) =>
      data(
        Activities.updateTimelineView({ client, path: { vid: v.id }, body: { state: toViewState(v.f) } }),
      ) as Promise<TimelineView>,
    onSuccess: (v) => {
      void refresh();
      toast({ title: `Saved these filters into “${v.name}”`, tone: "green" });
    },
    onError: fail("Couldn’t update the view"),
  });
  const remove = useMutation({
    mutationFn: (v: TimelineView) => data(Activities.deleteTimelineView({ client, path: { vid: v.id } })),
    onSuccess: (_, v) => {
      void refresh();
      toast({
        title: `Deleted “${v.name}”`,
        action: { label: "Undo", onClick: () => create.mutate({ name: v.name, f: fromViewState(v.state) }) },
      });
    },
    onError: fail("Couldn’t delete the view"),
  });
  return { views: list.data ?? [], create, update, remove };
}

/** The Views menu: your saved views of the timeline, one click to show one, and saving what the timeline shows now
 * as a view (its name guessed from the filters; Enter saves, Escape cancels). */
export function ViewsMenu({
  value,
  onPick,
  labels,
}: {
  value: Filters;
  onPick: (f: Filters) => void;
  labels: Record<string, string>;
}) {
  const { views, create, update, remove } = useTimelineViews();
  const [chosen, setChosen] = useState<number | null>(null);
  const [naming, setNaming] = useState<string | null>(null);
  const current = views.find((v) => sameFilters(fromViewState(v.state), value));
  const picked = views.find((v) => v.id === chosen);
  const target = current ?? picked;
  const save = () => {
    const name = (naming ?? "").trim();
    if (!name) return;
    create.mutate(
      { name, f: value },
      {
        onSuccess: (v) => {
          setChosen(v.id);
          setNaming(null);
        },
      },
    );
  };
  if (naming !== null)
    return (
      <form
        aria-label="Save as a view"
        className="flex items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <Input
          aria-label="View name"
          autoFocus
          value={naming}
          maxLength={60}
          onFocus={(e) => e.target.select()}
          onChange={(e) => setNaming(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setNaming(null)}
          className="h-8 w-56 text-[13px]"
        />
        <Button type="submit" size="sm" variant="primary" disabled={!naming.trim() || create.isPending}>
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setNaming(null)}>
          Cancel
        </Button>
      </form>
    );
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button size="sm" variant={current ? "primary" : "secondary"} icon={<Bookmark />}>
          {current ? current.name : "Views"}
        </Button>
      </MenuTrigger>
      <MenuContent align="start">
        {views.length > 0 && <MenuLabel>Your views</MenuLabel>}
        {views.map((v) => (
          <MenuItem
            key={v.id}
            icon={current?.id === v.id ? <Check /> : <span className="size-4" />}
            onSelect={() => {
              setChosen(v.id);
              onPick(fromViewState(v.state));
            }}
          >
            {v.name}
          </MenuItem>
        ))}
        {views.length > 0 && <MenuSeparator />}
        <MenuItem icon={<Save />} disabled={!!current} onSelect={() => setNaming(viewName(value, labels))}>
          {current ? "These filters are saved" : "Save these filters as a view"}
        </MenuItem>
        {picked && !current && (
          <MenuItem icon={<Save />} onSelect={() => update.mutate({ id: picked.id, f: value })}>
            Save these filters into “{picked.name}”
          </MenuItem>
        )}
        {target && (
          <MenuItem icon={<Trash2 />} danger onSelect={() => remove.mutate(target)}>
            Delete “{target.name}”
          </MenuItem>
        )}
      </MenuContent>
    </Menu>
  );
}
