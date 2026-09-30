"use client";

import {
  BookOpen,
  Box,
  Building2,
  CalendarDays,
  ChevronRight,
  Hash,
  MapPin,
  Tag,
  User,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { usePlayerApi } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { useRecordingEntities } from "@/components/recording/hooks";
import { fold, type EntityRef } from "@/components/recording/model";
import { Label } from "@/components/ui/panel";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export const ENTITY_GROUPS: {
  type: string;
  label: string;
  icon: LucideIcon;
}[] = [
  { type: "PERSON", label: "People", icon: User },
  { type: "ORG", label: "Organisations", icon: Building2 },
  { type: "PRODUCT", label: "Products", icon: Box },
  { type: "PLACE", label: "Places", icon: MapPin },
  { type: "EVENT", label: "Events", icon: CalendarDays },
  { type: "WORK", label: "Works", icon: BookOpen },
  { type: "TERM", label: "Topics", icon: Hash },
];

/** Player entities grouped by type, in the design's order; unknown types go last under their own name. */
export function groupEntities(
  entities: EntityRef[],
): { type: string; label: string; icon: LucideIcon; items: EntityRef[] }[] {
  const by = new Map<string, EntityRef[]>();
  for (const e of entities) by.set(e.type, [...(by.get(e.type) ?? []), e]);
  const known = ENTITY_GROUPS.filter((g) => by.has(g.type)).map((g) => ({
    ...g,
    items: by.get(g.type)!,
  }));
  const other = [...by.keys()]
    .filter((t) => !ENTITY_GROUPS.some((g) => g.type === t))
    .map((t) => ({
      type: t,
      label: t[0] + t.slice(1).toLowerCase(),
      icon: Tag,
      items: by.get(t)!,
    }));
  return [...known, ...other].map((g) => ({
    ...g,
    items: [...g.items].sort((a, b) => b.segs.length - a.segs.length || a.name.localeCompare(b.name)),
  }));
}

/**
 * Entities tab: the keyword cloud, then entities by type with mentions in this recording. Choosing one marks its
 * mentions ● on the waveform and jumps to the first; the chevron opens it in the graph.
 */
export function EntitiesTab() {
  const { model, id, entity } = useRec();
  const api = usePlayerApi();
  const list = useRecordingEntities(id, model.entities.length > 0);
  const groups = useMemo(() => groupEntities(model.entities), [model.entities]);
  // The player's entities carry no ids; match them to the entity index by name, key or alias to link to the graph.
  const ids = useMemo(() => {
    const m = new Map<string, number>();
    for (const raw of list.data?.items ?? []) {
      const e = raw as {
        id?: unknown;
        name?: unknown;
        key?: unknown;
        type?: unknown;
        aliases?: unknown;
      };
      if (typeof e.id !== "number") continue;
      const names = [e.name, e.key, ...(Array.isArray(e.aliases) ? e.aliases : [])];
      for (const n of names) if (typeof n === "string" && n) m.set(`${String(e.type)}:${fold(n)}`, e.id);
    }
    return m;
  }, [list.data]);
  const top = model.keywords.slice(0, 18);
  const max = Math.max(1, ...top.map((k) => k.weight));
  const min = Math.min(...top.map((k) => k.weight), max);

  if (!model.entities.length && !model.keywords.length)
    return (
      <EmptyState title="No entities yet" className="py-10">
        People, organisations, products, places and topics are found by the Analyze step, with keywords for the cloud.
      </EmptyState>
    );

  return (
    <>
      {top.length > 0 && (
        <div
          className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-md border border-border bg-surface p-3.5"
          role="img"
          aria-label={`Keyword cloud: ${top.map((k) => k.text).join(", ")}`}
        >
          {top.map((k, i) => {
            const f = max > min ? (k.weight - min) / (max - min) : 1;
            const size = 12 + Math.round(f * 10);
            return (
              <span
                key={k.text}
                className={cn(size > 16 ? "font-bold" : "font-medium", i % 3 === 0 ? "text-fg" : "text-fg-secondary")}
                style={{ fontSize: size, lineHeight: 1.2 }}
              >
                {k.text}
              </span>
            );
          })}
        </div>
      )}
      {groups.map((g) => (
        <section key={g.type} className="flex flex-col gap-0.5" aria-label={g.label}>
          <Label as="h3" className="flex items-center gap-1.5 pb-1.5">
            <g.icon aria-hidden className="size-[13px]" />
            {g.label}
          </Label>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
            {g.items.map((e) => {
              const eid = ids.get(`${e.type}:${fold(e.name)}`);
              const on = entity.selected?.name === e.name && entity.selected.type === e.type;
              const first = model.segments[e.segs[0]];
              return (
                <li
                  key={e.name}
                  className={cn(
                    "flex h-8 items-center gap-1 rounded-sm pl-2 pr-1 text-[13.5px] font-medium",
                    on ? "bg-hl" : "hover:bg-surface-neutral",
                  )}
                >
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => {
                      entity.select(on ? null : e);
                      if (!on && first) api.seek(first.t0, { manual: true });
                    }}
                    className="flex h-full min-w-0 flex-1 items-center gap-2.5 text-left"
                    title={
                      on
                        ? "Hide its mentions on the timeline"
                        : `Show mentions on the timeline${first ? ` and go to ${tc(first.t0)}` : ""}`
                    }
                  >
                    <span className="min-w-0 flex-1 truncate text-fg">{e.name}</span>
                    <span className="tabular shrink-0 text-[12px] font-normal text-fg-muted">
                      {e.segs.length} {e.segs.length === 1 ? "mention" : "mentions"}
                    </span>
                  </button>
                  {eid ? (
                    <Link
                      href={`/graph?focus=e${eid}`}
                      aria-label={`Open ${e.name} in the graph`}
                      className="grid size-7 shrink-0 place-items-center rounded-full text-fg-muted hover:bg-background hover:text-fg"
                    >
                      <ChevronRight className="size-3.5" />
                    </Link>
                  ) : list.isLoading ? (
                    <Skeleton className="mx-2 w-3" />
                  ) : (
                    <span className="w-7" />
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </>
  );
}
