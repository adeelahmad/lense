"use client";

import { ArrowUp, Eye, List, Table2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { rememberView } from "@/components/home/recently-viewed";
import { isFileDrag, useSendToImport } from "@/components/import/pending";
import { useRecordingActions } from "@/components/library/actions";
import { BulkBar, ReprocessDialog } from "@/components/library/bulk-bar";
import { FiltersBar } from "@/components/library/filters-bar";
import { LibraryEmpty } from "@/components/library/library-empty";
import { LibraryTabs } from "@/components/library/library-tabs";
import {
  NO_FILTERS,
  activeFilterCount,
  isActiveJob,
  matchesFilters,
  matchesView,
  needsAttention,
  rangeIds,
  sortRows,
  totalDuration,
  type Filters,
  type LibraryView,
  type SortDir,
  type SortKey,
  type StatusView,
} from "@/components/library/model";
import { RecordingCards, RecordingList } from "@/components/library/recording-list";
import { RecordingTable } from "@/components/library/recording-table";
import { SourcesStrip } from "@/components/library/sources-strip";
import { PAGE, useLibrary, useReviewsByRecording, useWatchedSources } from "@/components/library/use-library";
import { useIsNarrow } from "@/components/library/use-media";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { count, plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const VIEW_KEY = "lens.library.view";

/** A value that changes at most once per `ms`: live progress is announced politely, not on every poll. */
function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    const wait = Math.max(0, last.current + ms - Date.now());
    const t = setTimeout(() => {
      last.current = Date.now();
      setShown(value);
    }, wait);
    return () => clearTimeout(t);
  }, [value, ms]);
  return shown;
}

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return Boolean(el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable));
}

/** The Library (L1–L6): every recording you can see, across namespaces, with live job progress in its row. */
export function LibraryScreen() {
  const { namespace, namespaces, roleIn, can, me } = useArchive();
  const lib = useLibrary(namespace);
  const src = useWatchedSources(namespace);
  const actions = useRecordingActions();
  const toast = useToast();
  const narrow = useIsNarrow();
  const send = useSendToImport(namespace);

  const [layout, setLayout] = useState<"table" | "list">("table");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [view, setView] = useState<LibraryView>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "date", dir: "desc" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const lastIndex = useRef<number | null>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const v = localStorage.getItem(VIEW_KEY);
      if (v === "list" || v === "table") setLayout(v);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const chooseLayout = (v: string) => {
    setLayout(v as "table" | "list");
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* storage unavailable */
    }
  };

  // A new namespace is a new list: forget the selection and the speaker filter (names differ per namespace).
  useEffect(() => {
    setSelected(new Set());
    setFilters((f) => ({ ...f, speaker: null }));
    lastIndex.current = null;
  }, [namespace]);

  // New rows collect behind a "3 new" pill instead of shifting the list under the cursor, unless you're at the top.
  const seen = useRef<{ ns: string | null; ids: Set<number> } | null>(null);
  const [held, setHeld] = useState<number[]>([]);
  useEffect(() => {
    if (!lib.recordings.isSuccess) return;
    if (!seen.current || seen.current.ns !== namespace) {
      seen.current = { ns: namespace, ids: new Set(lib.rows.map((r) => r.id)) };
      setHeld([]);
      return;
    }
    const known = seen.current.ids;
    // Only rows newer than anything seen are "new"; older ones arrive by loading more and just join the list.
    const newest = Math.max(0, ...lib.rows.filter((r) => known.has(r.id)).map((r) => Date.parse(r.recorded_at ?? "") || 0));
    const unseen = lib.rows.filter((r) => !known.has(r.id));
    const fresh = unseen.filter((r) => (Date.parse(r.recorded_at ?? "") || 0) >= newest).map((r) => r.id);
    unseen.filter((r) => !fresh.includes(r.id)).forEach((r) => known.add(r.id));
    if (!fresh.length) return setHeld([]);
    if (window.scrollY < 80) {
      fresh.forEach((id) => known.add(id));
      setHeld([]);
    } else setHeld(fresh);
  }, [lib.rows, lib.recordings.isSuccess, namespace]);
  const showHeld = useCallback(
    (scroll = true) => {
      held.forEach((id) => seen.current?.ids.add(id));
      setHeld([]);
      if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
    },
    [held],
  );
  // Scrolling back to the top lets the held rows in.
  useEffect(() => {
    if (!held.length) return;
    const onScroll = () => {
      if (window.scrollY < 80) showHeld(false);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [held, showHeld]);

  const jobs = lib.jobsByRecording;
  const reviewScope = useMemo(() => (namespace ? [namespace] : namespaces.map((n) => n.name)), [namespace, namespaces]);
  const reviews = useReviewsByRecording(reviewScope);
  const heldSet = useMemo(() => new Set(held), [held]);
  const base = useMemo(() => lib.rows.filter((r) => !heldSet.has(r.id)), [lib.rows, heldSet]);
  const shown = useMemo(() => {
    const now = Date.now();
    const rows = base.filter((r) => matchesView(r, view, jobs.get(r.id), reviews.get(r.id)) && matchesFilters(r, filters, jobs.get(r.id), now));
    return sort.key === "date" && sort.dir === "desc" ? rows : sortRows(rows, sort.key, sort.dir);
  }, [base, view, filters, jobs, sort, reviews]);
  const attention = useMemo(() => base.filter((r) => needsAttention(r, jobs.get(r.id), reviews.get(r.id))).length, [base, jobs, reviews]);
  const processing = useMemo(() => base.filter((r) => isActiveJob(jobs.get(r.id))).length, [base, jobs]);

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "title" || key === "speakers" ? "asc" : "desc" }));

  const toggle = useCallback(
    (id: number, index: number, shiftKey: boolean) => {
      setSelected((cur) => {
        const next = new Set(cur);
        if (shiftKey && lastIndex.current != null) {
          const ids = rangeIds(
            shown.map((r) => r.id),
            lastIndex.current,
            index,
          );
          const on = !cur.has(id);
          ids.forEach((x) => (on ? next.add(x) : next.delete(x)));
        } else if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      lastIndex.current = index;
    },
    [shown],
  );
  const toggleAll = (on: boolean) => setSelected(on ? new Set(shown.map((r) => r.id)) : new Set());

  const editReason = useCallback((ns: string | null | undefined) => (can("editor", ns) ? null : needRole("editor", ns)), [can]);
  const onRetry = useCallback(
    (rec: RecordingSummary, v: StatusView) => {
      if (v.retry?.kind === "job") void actions.retryJob(v.retry.job);
      else void actions.reprocess([rec.id]);
    },
    [actions],
  );
  const onOpen = useCallback((r: RecordingSummary) => rememberView({ kind: "recording", href: `/recordings/${r.id}`, title: r.title || "Untitled" }), []);

  const selectedRows = lib.rows.filter((r) => selected.has(r.id));
  const blockedNs = [...new Set(selectedRows.filter((r) => !can("editor", r.namespace)).map((r) => r.namespace ?? "?"))];
  const blocked = { count: selectedRows.filter((r) => !can("editor", r.namespace)).length, namespaces: blockedNs };

  // Keyboard: J/K move between rows, X selects, Enter opens (the title link), / focuses the filter, Esc clears.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector("[role=dialog]")) return;
      const links = Array.from(listRef.current?.querySelectorAll<HTMLAnchorElement>("a[data-row-link]") ?? []);
      const current = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-row-id]");
      const idx = current ? links.findIndex((a) => a.closest("[data-row-id]") === current) : -1;
      if (e.key === "/") {
        e.preventDefault();
        filterRef.current?.focus();
      } else if (e.key === "j" || e.key === "k") {
        if (!links.length) return;
        e.preventDefault();
        const next = e.key === "j" ? Math.min(links.length - 1, idx + 1) : Math.max(0, idx < 0 ? 0 : idx - 1);
        links[next].focus();
        links[next].scrollIntoView({ block: "nearest" });
      } else if ((e.key === "x" || (e.key === " " && (e.target as HTMLElement).matches("a[data-row-link]"))) && current) {
        // X anywhere in a row, or Space on its title, selects it (Space on the checkbox works natively).
        e.preventDefault();
        const id = Number(current.dataset.rowId);
        toggle(id, shown.findIndex((r) => r.id === id), e.shiftKey);
      } else if (e.key === "Escape" && selected.size) {
        setSelected(new Set());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, toggle, selected.size]);

  const canImport = can("editor", namespace);
  const onDrop = (e: React.DragEvent) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (!canImport) {
      toast({ title: "Can’t import here", body: needRole("editor", namespace), tone: "red" });
      return;
    }
    send.open(files);
  };

  const role = roleIn(namespace);
  const viewerEverywhere = !namespace && namespaces.length > 0 && !can("editor");
  const loaded = lib.recordings.isSuccess;
  const empty = loaded && lib.rows.length === 0 && !lib.recordings.hasNextPage;
  const filtering = activeFilterCount(filters) > 0 || view !== "all";
  const processingNow = lib.jobsCounts.running + lib.jobsCounts.queued;
  const countLine = [
    plural(lib.total, "recording"),
    lib.ms ? `${totalDuration(lib.ms)}${namespace ? ` in ${namespace}` : ""}` : null,
    processingNow ? `${count(processingNow)} processing` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const announced = useThrottled(processingNow ? `${count(processingNow)} processing` : "", 10_000);
  const rowProps = { rows: shown, jobs, reviews, selected, onToggle: toggle, onToggleAll: toggleAll, onRetry, editReason, onOpen };

  return (
    <div
      className="relative flex min-h-[calc(100vh-64px)] flex-col"
      onDragOver={(e) => {
        if (isFileDrag(e)) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div className="flex flex-col gap-3 px-4 pt-4 md:px-6 md:pt-[18px]">
        <div className="flex items-center gap-3.5">
          <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Library</h1>
          <span className="tabular hidden text-[13px] text-fg-muted sm:inline">{lib.recordings.isLoading ? <Skeleton className="w-56" /> : countLine}</span>
          <span className="sr-only" aria-live="polite">
            {announced}
          </span>
          <span className="flex-1" />
          {!narrow && (
            <Segmented
              value={layout}
              onChange={chooseLayout}
              items={[
                { value: "table", label: "Table", icon: <Table2 /> },
                { value: "list", label: "List", icon: <List /> },
              ]}
            />
          )}
        </div>

        {(role === "viewer" || viewerEverywhere) && (
          <div role="note" className="flex items-center gap-2.5 rounded-[10px] border border-border bg-surface-neutral px-3 py-[9px] text-[13px] leading-snug text-fg-strong">
            <Eye className="size-[15px] shrink-0" aria-hidden />
            <span className="flex-1">
              You’re a <b className="font-bold">viewer</b> in {namespace ?? namespaces.map((n) => n.name).join(", ")}: you can read, listen, search and chat. Import, reprocess and
              edits are turned off — ask an owner for editor access.
            </span>
          </div>
        )}

        {!empty && (
          <>
            {!narrow && <SourcesStrip watches={src.watches} sources={src.sources} running={lib.jobsCounts.running} queued={lib.jobsCounts.queued} />}
            {!narrow && (
              <LibraryTabs
                value={view}
                onChange={setView}
                items={[
                  { value: "all", label: "All recordings", count: count(lib.total) },
                  { value: "attention", label: "Needs attention", count: attention || undefined },
                  { value: "processing", label: "Processing", count: processing || undefined },
                  { value: "mine", label: "Edited by me", disabledReason: "Not available yet: the archive doesn’t list recordings by who edited them." },
                ]}
              />
            )}
            <FiltersBar filters={filters} onChange={setFilters} rows={lib.rows} inputRef={filterRef} compact={narrow} />
          </>
        )}
      </div>

      {held.length > 0 && (
        <div className="sticky top-[72px] z-20 flex justify-center">
          <button type="button" onClick={() => showHeld()} className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-pill bg-blue px-3.5 text-[13px] font-bold text-white shadow-2 hover:bg-blue-dark">
            <ArrowUp className="size-4" aria-hidden />
            {count(held.length)} new
          </button>
        </div>
      )}

      <div ref={listRef} className="mt-2.5 flex min-h-0 flex-1 flex-col">
        {lib.recordings.isLoading ? (
          <LibrarySkeleton />
        ) : lib.recordings.isError ? (
          <EmptyState
            tone="error"
            className="border-t border-border"
            title="Couldn’t load the library"
            actions={
              <Button variant="secondary" onClick={() => lib.recordings.refetch()}>
                Try again
              </Button>
            }
          >
            {(lib.recordings.error as Error).message}
          </EmptyState>
        ) : me && !me.user.admin && Object.keys(me.roles ?? {}).length === 0 ? (
          <EmptyState className="border-t border-border" title="No namespaces yet">
            You don’t have a role in any namespace. Ask an admin to add you, and the recordings you can see will show up here.
          </EmptyState>
        ) : empty ? (
          <LibraryEmpty namespace={namespace} />
        ) : shown.length === 0 ? (
          <EmptyState
            className="border-t border-border"
            title="No recordings match"
            actions={
              <Button
                variant="secondary"
                onClick={() => {
                  setFilters(NO_FILTERS);
                  setView("all");
                }}
              >
                Clear filters
              </Button>
            }
          >
            {lib.recordings.hasNextPage ? `Filters look at the ${count(lib.rows.length)} most recent recordings loaded so far. Load more to look further back.` : "Try fewer filters."}
          </EmptyState>
        ) : narrow ? (
          <RecordingCards {...rowProps} />
        ) : layout === "table" ? (
          <div className="border-t border-border">
            <RecordingTable {...rowProps} sort={sort} onSort={onSort} />
          </div>
        ) : (
          <RecordingList {...rowProps} />
        )}

        {loaded && !empty && (lib.recordings.hasNextPage || filtering) && (
          <div className="flex flex-wrap items-center gap-3 px-4 py-4 text-[13px] text-fg-secondary md:px-6">
            <span className="tabular">
              {filtering ? `${count(shown.length)} shown · ` : ""}
              {count(lib.rows.length)} of {count(Math.max(lib.total, lib.rows.length))} loaded
              {filtering && lib.recordings.hasNextPage ? " — filters and sorting apply to the loaded recordings" : ""}
            </span>
            {lib.recordings.hasNextPage && (
              <Button variant="secondary" size="sm" disabled={lib.recordings.isFetchingNextPage} onClick={() => lib.recordings.fetchNextPage()}>
                {lib.recordings.isFetchingNextPage ? "Loading…" : `Load ${count(PAGE)} more`}
              </Button>
            )}
          </div>
        )}
        <div className="flex-1" />
        <BulkBar selected={selected.size} blocked={blocked} onReprocess={() => setReprocessOpen(true)} onExport={(fmt) => actions.exportMany([...selected], fmt)} onClear={() => setSelected(new Set())} />
      </div>

      <ReprocessDialog
        open={reprocessOpen}
        onOpenChange={setReprocessOpen}
        n={selected.size}
        withoutAudio={selectedRows.filter((r) => r.media_kind === "transcript").length}
        onConfirm={(steps) => actions.reprocess([...selected], steps)}
      />

      {dragging && (
        <div className="pointer-events-none absolute inset-2 z-40 grid place-items-center rounded-lg border-2 border-dashed border-blue bg-[color-mix(in_srgb,var(--intent-surface)_92%,transparent)]">
          <div className="flex flex-col items-center gap-2 text-center">
            <Upload className="size-8 text-blue" aria-hidden />
            <p className="text-[16px] font-bold text-fg">{canImport ? `Drop to import${namespace ? ` into ${namespace}` : ""}` : "You can’t import here"}</p>
            <p className="text-[13px] text-fg-secondary">{canImport ? "You’ll see how each file was read before anything is saved." : needRole("editor", namespace)}</p>
          </div>
        </div>
      )}
      {lib.recordings.isError === false && lib.jobs.isError && (
        <div className="px-4 pb-4 md:px-6">
          <Banner tone="warning" title="Job progress isn’t updating.">
            {(lib.jobs.error as Error).message}
          </Banner>
        </div>
      )}
    </div>
  );
}

function LibrarySkeleton() {
  return (
    <div className="border-t border-border" aria-busy="true" aria-label="Loading recordings">
      {Array.from({ length: 10 }, (_, i) => (
        <div key={i} className={cn("flex h-[52px] items-center gap-4 border-b border-border px-6")}>
          <Skeleton className="size-[18px] shrink-0 rounded-xs" />
          <div className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3.5" style={{ width: `${30 + ((i * 17) % 30)}%` }} />
            <Skeleton className="h-2.5 w-24" />
          </div>
          <Skeleton className="hidden w-20 md:block" />
          <Skeleton className="hidden w-10 md:block" />
          <Skeleton className="hidden w-28 lg:block" />
          <Skeleton className="h-[22px] w-24 rounded-pill" />
          <Skeleton className="hidden w-14 lg:block" />
        </div>
      ))}
    </div>
  );
}
