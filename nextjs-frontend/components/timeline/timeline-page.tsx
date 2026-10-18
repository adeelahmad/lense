"use client";

import { ArrowUp, CheckCheck, History, ListFilter, Sparkles, VolumeX, Waypoints, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ActivityMark, Reaction } from "@/app/openapi-client/types.gen";
import { askAbout } from "@/components/page-chat/page-chat-model";
import { FiltersBar, type Actor } from "@/components/timeline/filters";
import { TimelineRow, groupMark } from "@/components/timeline/item";
import {
  askQuote,
  filterCount,
  fromParams,
  group,
  isFiltered,
  lastFilters,
  mergeNew,
  newest,
  NO_FILTERS,
  rememberFilters,
  toParams,
  toQuery,
  unseen,
  type Activity,
  type Filters,
  type Mark,
} from "@/components/timeline/model";
import { Social } from "@/components/timeline/social";
import { useCommentCounts, useReactions } from "@/components/timeline/use-social";
import { useKinds, useMarks, useMutes, useNewItems, useTimeline } from "@/components/timeline/use-timeline";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/tabs";
import { Drawer } from "@/components/ui/dialog";
import { SearchInput } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { useMediaQuery } from "@/components/player/use-media-query";
import { useArchive } from "@/lib/hooks/session";

/** Whether a key press is for the page (not typed into a field, no modifier). */
function forPage(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (e.metaKey || e.ctrlKey || e.altKey) return false;
  return !(t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)));
}

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The words filter, always in sight: applied on Enter. */
function WordsBox({ value, onApply }: { value: string; onApply: (q: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <SearchInput
      aria-label="Words in the summary"
      placeholder="Find in the timeline"
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        if (!e.target.value && value) onApply("");
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onApply(draft.trim());
        }
      }}
      className="min-w-0 flex-1 md:max-w-xs"
    />
  );
}

/** The timeline (docs/timeline.md): what came in from every source, newest first, with filters in the address, runs
 * of the same actor and kind as one line, review marks with keys, and new items as they arrive. */
export function TimelinePage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const qs = params.toString();
  const filters = useMemo(() => fromParams(new URLSearchParams(qs)), [qs]);
  const { namespaces } = useArchive();
  const phone = useMediaQuery("(max-width: 767px)");
  const [sheet, setSheet] = useState(false);

  // the last filters used in this browser, when the address names none
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const last = qs ? null : lastFilters();
    if (last && isFiltered(last)) router.replace(`${pathname}?${toParams(last)}`);
  }, [qs, pathname, router]);

  const setFilters = useCallback(
    (f: Filters) => {
      rememberFilters(f);
      const next = toParams(f).toString();
      router.replace(next ? `${pathname}?${next}` : pathname);
    },
    [pathname, router],
  );

  const timeline = useTimeline(filters);
  const kinds = useKinds();
  const pages = timeline.data?.pages;
  const query = useMemo(() => toQuery(filters), [filters]);

  // new items brought in by the pill, kept until the filters change
  const [extra, setExtra] = useState<{
    items: Activity[];
    marks: Record<string, ActivityMark>;
    reactions: Record<string, Reaction[]>;
    comments: Record<string, number>;
    ns: Record<string, string>;
    why: Record<string, string>;
  }>({
    items: [],
    marks: {},
    reactions: {},
    comments: {},
    ns: {},
    why: {},
  });
  useEffect(() => setExtra({ items: [], marks: {}, reactions: {}, comments: {}, ns: {}, why: {} }), [qs]);

  const loaded = useMemo(() => (pages ?? []).flatMap((p) => p.items), [pages]);
  const all = useMemo(() => mergeNew(loaded, extra.items), [loaded, extra.items]);
  const serverMarks = useMemo(
    () => Object.assign({}, ...(pages ?? []).map((p) => p.marks ?? {}), extra.marks) as Record<string, ActivityMark>,
    [pages, extra.marks],
  );
  const nsNames = useMemo(
    () => Object.assign({}, ...(pages ?? []).map((p) => p.namespaces ?? {}), extra.ns) as Record<string, string>,
    [pages, extra.ns],
  );
  const serverReactions = useMemo(
    () =>
      Object.assign({}, ...(pages ?? []).map((p) => p.reactions ?? {}), extra.reactions) as Record<string, Reaction[]>,
    [pages, extra.reactions],
  );
  const serverComments = useMemo(
    () => Object.assign({}, ...(pages ?? []).map((p) => p.comments ?? {}), extra.comments) as Record<string, number>,
    [pages, extra.comments],
  );
  const forYou = filters.order === "for_you";
  const why = useMemo(
    () => Object.assign({}, ...(pages ?? []).map((p) => p.why ?? {}), extra.why) as Record<string, string>,
    [pages, extra.why],
  );
  const whyOf = useCallback((id: string) => (forYou ? why[id] : undefined), [forYou, why]);
  const marks = useMarks(serverMarks);
  const reactions = useReactions(serverReactions);
  const counts = useCommentCounts(serverComments);
  const mutes = useMutes();
  const mutedActors = useMemo(() => new Set(mutes.mutes.map((m) => m.actor).filter(Boolean)), [mutes.mutes]);
  const shown = useMemo(
    () => all.filter((a) => !marks.of(a.id).hidden && !mutedActors.has(a.actor.id)),
    [all, marks, mutedActors],
  );
  const groups = useMemo(() => group(shown), [shown]);
  const labels = useMemo(() => Object.fromEntries((kinds.data ?? []).map((k) => [k.name, k.label])), [kinds.data]);

  const live = !filters.to || filters.to >= today();
  const fresh = useNewItems(query, newest(all), live && !timeline.isLoading);
  const pending = useMemo(() => unseen(all, fresh.data?.items ?? []), [all, fresh.data]);
  const bringIn = () => {
    const d = fresh.data;
    setExtra((x) => ({
      items: [...pending, ...x.items],
      marks: { ...x.marks, ...(d?.marks ?? {}) },
      reactions: { ...x.reactions, ...(d?.reactions ?? {}) },
      comments: { ...x.comments, ...(d?.comments ?? {}) },
      ns: { ...x.ns, ...(d?.namespaces ?? {}) },
      why: { ...x.why, ...(d?.why ?? {}) },
    }));
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  };

  const actors = useMemo(() => {
    const seen = new Map<string, Actor>();
    for (const a of all) if (!seen.has(a.actor.id)) seen.set(a.actor.id, { id: a.actor.id, name: a.actor.name });
    return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [all]);
  const tags = useMemo(
    () => [...new Set(all.flatMap((a) => (a.tag ?? []).map((t) => String(t.name ?? "")).filter(Boolean)))].sort(),
    [all],
  );

  // opening, and moving with j and k
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (key: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  const [active, setActive] = useState(-1);
  const rows = useRef<(HTMLLIElement | null)[]>([]);

  const mark = useCallback((ids: string[], flags: Partial<Mark>) => marks.set(ids, flags), [marks]);
  const hide = useCallback(
    (ids: string[]) =>
      marks.set(
        ids,
        { hidden: true },
        { title: ids.length === 1 ? "Hidden from your timeline" : `Hid ${ids.length}`, undo: true },
      ),
    [marks],
  );
  const muteActor = useCallback((a: Activity) => mutes.mute({ actor: a.actor.id, name: a.actor.name }), [mutes]);
  const markAllShown = () => {
    const ids = shown.filter((a) => !marks.of(a.id).reviewed).map((a) => a.id);
    marks.set(ids, { reviewed: true }, { title: `Marked ${ids.length} reviewed`, undo: true });
  };
  const unreviewedShown = shown.filter((a) => !marks.of(a.id).reviewed).length;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!forPage(e) || !groups.length) return;
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        const next = Math.max(0, Math.min(groups.length - 1, active + (e.key === "j" ? 1 : -1)));
        setActive(next);
        rows.current[next]?.focus();
        return;
      }
      const g = groups[active];
      if (g && e.key === "a") {
        e.preventDefault();
        askAbout(askQuote(g.items));
        return;
      }
      if (!g || (e.key !== "r" && e.key !== "i")) return;
      e.preventDefault();
      const m = groupMark(g, marks.of);
      const ids = g.items.map((a) => a.id);
      if (e.key === "r") marks.set(ids, { reviewed: !m.reviewed });
      else marks.set(ids, { important: !m.important });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [groups, active, marks]);

  const bar = (stacked: boolean) => (
    <FiltersBar
      value={filters}
      onChange={(f) => {
        setFilters(f);
        if (stacked) setSheet(false);
      }}
      kinds={kinds.data ?? []}
      actors={actors}
      namespaces={namespaces.map((n) => n.name)}
      tags={tags}
      stacked={stacked}
    />
  );
  const filtered = isFiltered(filters);

  return (
    <div className="flex flex-col gap-3 px-4 pb-10 pt-[18px] md:px-6">
      <PageHeader
        className="mb-1"
        title="Timeline"
        meta={forYou ? "What came in, what you care for most first" : "What came in from every source, newest first"}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm" variant="ghost" icon={<Waypoints />}>
              <Link href="/graph?show=actors" title="Every source, sensor, person and agent, and what they brought in">
                Graph
              </Link>
            </Button>
            <Button
              size="sm"
              variant="secondary"
              icon={<CheckCheck />}
              disabled={!unreviewedShown}
              onClick={markAllShown}
            >
              Mark all shown as reviewed
            </Button>
          </div>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          label="Order"
          value={filters.order}
          onChange={(v) => setFilters({ ...filters, order: v === "for_you" ? "for_you" : "latest" })}
          items={[
            { value: "latest", label: "Latest", icon: <History /> },
            { value: "for_you", label: "For you", icon: <Sparkles /> },
          ]}
        />
        <WordsBox value={filters.q} onApply={(q) => setFilters({ ...filters, q })} />
        {phone && (
          <Button
            size="sm"
            variant={filterCount(filters) ? "primary" : "secondary"}
            icon={<ListFilter />}
            onClick={() => setSheet(true)}
          >
            Filters{filterCount(filters) ? ` · ${filterCount(filters)}` : ""}
          </Button>
        )}
      </div>
      {!phone && bar(false)}
      {mutes.mutes.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-fg-secondary" aria-label="Muted">
          <VolumeX className="size-3.5" aria-hidden />
          <span>Muted:</span>
          {mutes.mutes.map((m) => (
            <span
              key={`${m.actor ?? ""}|${m.kind ?? ""}`}
              className="inline-flex items-center gap-1 rounded-pill border border-border px-2 py-0.5 text-fg"
            >
              {m.name ?? m.actor ?? m.kind}
              {m.until && <span className="text-fg-muted">until {new Date(m.until).toLocaleString()}</span>}
              <button
                type="button"
                aria-label={`Unmute ${m.name ?? m.actor ?? m.kind}`}
                onClick={() => mutes.unmute(m)}
                className="grid size-4 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {phone && (
        <Drawer open={sheet} onOpenChange={setSheet} title="Filters" width={360}>
          <div className="p-4">{bar(true)}</div>
        </Drawer>
      )}

      {forYou && !timeline.isLoading && !Object.keys(why).length && (
        <p className="text-[12.5px] text-fg-muted">
          For you learns from what you react to, comment on and mark important or hidden; so far it is the same as
          Latest.
        </p>
      )}
      {pending.length > 0 && (
        <Button size="sm" variant="primary" icon={<ArrowUp />} className="self-center" onClick={bringIn}>
          {pending.length} new
        </Button>
      )}

      {timeline.isLoading ? (
        <SkeletonRows rows={8} />
      ) : timeline.error ? (
        <EmptyState
          tone="error"
          icon={<History />}
          title="Couldn’t load the timeline"
          actions={<Button onClick={() => timeline.refetch()}>Try again</Button>}
        >
          {(timeline.error as Error).message}
        </EmptyState>
      ) : groups.length ? (
        <>
          <ul aria-label="Timeline" className="flex flex-col rounded-md border border-border bg-surface px-3">
            {groups.map((g, i) => (
              <TimelineRow
                key={g.key}
                ref={(el) => {
                  rows.current[i] = el;
                }}
                group={g}
                namespaces={nsNames}
                labels={labels}
                markOf={marks.of}
                onMark={mark}
                onHide={hide}
                onMute={muteActor}
                open={open.has(g.key)}
                onToggle={() => toggle(g.key)}
                focused={i === active}
                onFocus={() => setActive(i)}
                reactionsOf={reactions.of}
                commentsOf={counts.of}
                whyOf={whyOf}
                social={(a) => (
                  <Social
                    a={a}
                    reactions={reactions.of(a.id)}
                    onReact={(emoji, on) => reactions.react(a.id, emoji, on)}
                    comments={counts.of(a.id)}
                    bump={counts.bump}
                  />
                )}
              />
            ))}
          </ul>
          {timeline.hasNextPage && (
            <Button
              variant="secondary"
              className="self-start"
              disabled={timeline.isFetchingNextPage}
              onClick={() => timeline.fetchNextPage()}
            >
              {timeline.isFetchingNextPage ? "Loading…" : "Older"}
            </Button>
          )}
          <p className="hidden text-[12px] text-fg-muted md:block">
            Keys: j and k move, Enter opens, r marks reviewed, i marks important, a asks about it.
          </p>
        </>
      ) : (
        <EmptyState
          icon={<History />}
          title={filtered ? "Nothing matches these filters" : "Nothing here yet"}
          actions={filtered ? <Button onClick={() => setFilters(NO_FILTERS)}>Clear the filters</Button> : undefined}
        >
          {filtered
            ? "Try fewer filters, or another date range."
            : "What your sources bring in shows here as it arrives, newest first."}
        </EmptyState>
      )}
    </div>
  );
}
