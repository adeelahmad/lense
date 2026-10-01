"use client";

import { ArrowUp, Eye, List, Table2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { RecordingSummary, SavedView } from "@/app/openapi-client/types.gen";
import { rememberView } from "@/components/home/recently-viewed";
import { isFileDrag, useSendToImport } from "@/components/import/pending";
import { useRecordingActions } from "@/components/library/actions";
import { BulkBar, DeleteDialog, MoveDialog, ReprocessDialog, TagDialog } from "@/components/library/bulk-bar";
import { useNamespaceFields } from "@/components/fields/use-fields";
import { collectionName } from "@/components/library/collections-model";
import { CollectionsDialog, PlaceDialog } from "@/components/library/collections-ui";
import { FiltersBar } from "@/components/library/filters-bar";
import { LibraryEmpty } from "@/components/library/library-empty";
import { LibraryTabs } from "@/components/library/library-tabs";
import {
  NO_FILTERS,
  activeFilterCount,
  blockedBy,
  libraryQuery,
  moveTargets,
  rangeIds,
  totalDuration,
  type FieldFilter,
  type Filters,
  type LibraryView,
  type SortDir,
  type SortKey,
  type StatusView,
} from "@/components/library/model";
import { RecordingCards, RecordingList } from "@/components/library/recording-list";
import { RecordingTable } from "@/components/library/recording-table";
import { SavedViews } from "@/components/library/saved-views";
import { SourcesStrip } from "@/components/library/sources-strip";
import {
  PAGE,
  useLibrary,
  useLibraryCounts,
  useReviewsByRecording,
  useSpeakerChoices,
  useLanguages,
  useOrigins,
  useObjectCounts,
  useTagCounts,
  useWatchedSources,
} from "@/components/library/use-library";
import { useCollectionTree } from "@/components/library/use-collections";
import { useIsNarrow } from "@/components/library/use-media";
import { fromView, viewState } from "@/components/library/views-model";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { count, plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { atLeast } from "@/lib/roles";
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

/** The value once it has stopped changing for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return Boolean(el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable));
}

/** The Library (L1–L6): every recording you can see, across namespaces, with live job progress in its row. `initial`
 * opens it on a namespace's collection (/library?namespace=…&collection=…, as a recording's breadcrumb links). */
export function LibraryScreen({ initial }: { initial?: { namespace: string; collection: number | null } } = {}) {
  const { namespace, namespaces, isPartial, roleIn, can, me, setNamespace } = useArchive();
  const [layout, setLayout] = useState<"table" | "list">("table");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [view, setView] = useState<LibraryView>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({
    key: "date",
    dir: "desc",
  });
  // The server filters, sorts and counts; typing in the filter box waits for a pause before asking again.
  const q = useDebounced(filters.q, 250);
  const query = useMemo(
    () => libraryQuery({ ...filters, q }, view, sort, namespace),
    [filters, q, view, sort, namespace],
  );
  const queryKey = JSON.stringify(query);
  const lib = useLibrary(namespace, query);
  const counts = useLibraryCounts(namespace);
  const src = useWatchedSources(namespace);
  const actions = useRecordingActions();
  const toast = useToast();
  const narrow = useIsNarrow();
  const send = useSendToImport(namespace);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [placeOpen, setPlaceOpen] = useState(false);
  const [collectionsOpen, setCollectionsOpen] = useState(false);
  const tree = useCollectionTree(namespace);
  const tagCounts = useTagCounts(namespace);
  const objectCounts = useObjectCounts(namespace);
  const origins = useOrigins(namespace);
  const languages = useLanguages(namespace);
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

  // A new namespace is a new list: forget the speaker, collection and field filters (each namespace has its own).
  useEffect(() => {
    setFilters((f) => ({ ...f, speaker: null, collection: null, field: null }));
  }, [namespace]);
  // Other filters, another tab or sort: a new list too, so the selection starts over.
  useEffect(() => {
    setSelected(new Set());
    lastIndex.current = null;
  }, [queryKey]);

  // New rows collect behind a "3 new" pill instead of shifting the list under the cursor, unless you're at the top.
  const seen = useRef<{ key: string; ids: Set<number> } | null>(null);
  const [held, setHeld] = useState<number[]>([]);
  useEffect(() => {
    // While another query's rows stand in for this one's, there is nothing new to compare.
    if (!lib.recordings.isSuccess || lib.recordings.isPlaceholderData) return;
    if (!seen.current || seen.current.key !== queryKey) {
      seen.current = { key: queryKey, ids: new Set(lib.rows.map((r) => r.id)) };
      setHeld([]);
      return;
    }
    const known = seen.current.ids;
    // Only rows newer than anything seen are "new"; older ones arrive by loading more and just join the list.
    const newest = Math.max(
      0,
      ...lib.rows.filter((r) => known.has(r.id)).map((r) => Date.parse(r.recorded_at ?? "") || 0),
    );
    const unseen = lib.rows.filter((r) => !known.has(r.id));
    const fresh = unseen.filter((r) => (Date.parse(r.recorded_at ?? "") || 0) >= newest).map((r) => r.id);
    unseen.filter((r) => !fresh.includes(r.id)).forEach((r) => known.add(r.id));
    if (!fresh.length) return setHeld([]);
    if (window.scrollY < 80) {
      fresh.forEach((id) => known.add(id));
      setHeld([]);
    } else setHeld(fresh);
  }, [lib.rows, lib.recordings.isSuccess, lib.recordings.isPlaceholderData, queryKey]);
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
  // voice matches and speakers are namespace-wide: only where the person has a role
  const reviewScope = useMemo(
    () => (namespace ? (can("viewer", namespace) ? [namespace] : []) : namespaces.map((n) => n.name)),
    [namespace, namespaces, can],
  );
  const reviews = useReviewsByRecording(reviewScope);
  const speakers = useSpeakerChoices(reviewScope);
  // A saved view brings back its namespace, tab, filters and sort; its speaker by name, once that namespace's
  // speakers are known (each namespace has its own ids).
  const [pendingSpeaker, setPendingSpeaker] = useState<{ name: string; ns: string | null } | null>(null);
  // Its collection too, once that namespace's collections are known (it may have been deleted since); and its field.
  const [pendingCollection, setPendingCollection] = useState<{ id: number; ns: string } | null>(null);
  const [pendingField, setPendingField] = useState<(FieldFilter & { ns: string }) | null>(null);
  const fieldDefs = useNamespaceFields(namespace);
  const resourceFields = useMemo(() => (fieldDefs.data ?? []).filter((f) => f.target === "resource"), [fieldDefs.data]);
  useEffect(() => {
    if (!pendingField || pendingField.ns !== namespace || !fieldDefs.isSuccess) return;
    const known = resourceFields.some((f) => f.id === pendingField.id);
    setFilters((f) => ({ ...f, field: known ? { id: pendingField.id, value: pendingField.value } : null }));
    setPendingField(null);
    if (!known) toast({ title: `That field isn’t in ${namespace} now`, body: "The view shows its other filters." });
  }, [pendingField, namespace, fieldDefs.isSuccess, resourceFields, toast]);
  const applyView = (v: SavedView) => {
    const s = fromView(v.state);
    if ((v.namespace ?? null) !== namespace) setNamespace(v.namespace ?? null);
    setFilters({ ...s.filters, collection: null, field: null });
    setPendingField(s.filters.field && v.namespace ? { ...s.filters.field, ns: v.namespace } : null);
    setView(s.view);
    setSort(s.sort);
    setPendingSpeaker(s.speaker ? { name: s.speaker, ns: v.namespace ?? null } : null);
    setPendingCollection(
      s.filters.collection != null && v.namespace ? { id: s.filters.collection, ns: v.namespace } : null,
    );
  };
  // A link to a collection (a recording's breadcrumb) opens its namespace on it.
  useEffect(() => {
    if (!initial?.namespace) return;
    setNamespace(initial.namespace);
    if (initial.collection != null) setPendingCollection({ id: initial.collection, ns: initial.namespace });
  }, [initial?.namespace, initial?.collection, setNamespace]);
  useEffect(() => {
    if (!pendingCollection || pendingCollection.ns !== namespace || !tree.isSuccess) return;
    const name = collectionName(tree.data, pendingCollection.id);
    setFilters((f) => ({ ...f, collection: name != null ? pendingCollection.id : null }));
    setPendingCollection(null);
    if (name == null)
      toast({
        title: `That collection isn’t in ${namespace} now`,
        body: "The Library shows the whole namespace instead.",
      });
  }, [pendingCollection, namespace, tree.isSuccess, tree.data, toast]);
  useEffect(() => {
    if (!pendingSpeaker || pendingSpeaker.ns !== namespace || speakers.loading) return;
    const choice = speakers.choices.find((c) => c.name === pendingSpeaker.name) ?? null;
    setFilters((f) => ({ ...f, speaker: choice }));
    setPendingSpeaker(null);
    if (!choice)
      toast({
        title: `No one called ${pendingSpeaker.name} speaks in ${namespace ?? "your namespaces"} now`,
        body: "The view shows everyone’s recordings instead.",
      });
  }, [pendingSpeaker, namespace, speakers.loading, speakers.choices, toast]);
  const shownState = useMemo(() => viewState({ filters, view, sort }), [filters, view, sort]);
  const heldSet = useMemo(() => new Set(held), [held]);
  const shown = useMemo(() => lib.rows.filter((r) => !heldSet.has(r.id)), [lib.rows, heldSet]);

  const onSort = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "title" || key === "speakers" ? "asc" : "desc" },
    );

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

  // a recording's own role counts its collection too (an editor of a collection edits its recordings)
  const roleOf = useCallback((r: RecordingSummary) => r.role ?? roleIn(r.namespace), [roleIn]);
  const editReason = useCallback(
    (r: RecordingSummary) => (atLeast(roleOf(r), "editor") ? null : needRole("editor", r.namespace)),
    [roleOf],
  );
  const onRetry = useCallback(
    (rec: RecordingSummary, v: StatusView) => {
      if (v.retry?.kind === "job") void actions.retryJob(v.retry.job);
      else void actions.reprocess([rec.id]);
    },
    [actions],
  );
  const onOpen = useCallback(
    (r: RecordingSummary) =>
      rememberView({
        kind: "recording",
        href: `/resources/${r.id}`,
        title: r.title || "Untitled",
      }),
    [],
  );

  const selectedRows = lib.rows.filter((r) => selected.has(r.id));
  const blocked = blockedBy(selectedRows, (r) => atLeast(roleOf(r), "editor"));
  const targets = moveTargets(
    namespaces.map((n) => n.name).filter((ns) => can("editor", ns)),
    selectedRows.map((r) => r.namespace),
  );
  // Collections belong to a namespace: the selection files into one only when it's all from the same one.
  const selectedNs = [...new Set(selectedRows.map((r) => r.namespace).filter((n): n is string => Boolean(n)))];
  const placeNs = selectedNs.length === 1 ? selectedNs[0] : null;
  const placeReason =
    selectedNs.length > 1
      ? `Collections belong to one namespace: select recordings of one namespace (these are in ${selectedNs.join(", ")}).`
      : undefined;
  const sharedHome = new Set(selectedRows.map((r) => r.collection ?? null));
  const placeCurrent = sharedHome.size === 1 ? [...sharedHome][0] : null;

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
      } else if (
        (e.key === "x" || (e.key === " " && (e.target as HTMLElement).matches("a[data-row-link]"))) &&
        current
      ) {
        // X anywhere in a row, or Space on its title, selects it (Space on the checkbox works natively).
        e.preventDefault();
        const id = Number(current.dataset.rowId);
        toggle(
          id,
          shown.findIndex((r) => r.id === id),
          e.shiftKey,
        );
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
      toast({
        title: "Can’t import here",
        body: needRole("editor", namespace),
        tone: "red",
      });
      return;
    }
    send.open(files);
  };

  const role = roleIn(namespace);
  const viewerEverywhere = !namespace && namespaces.length > 0 && !can("editor");
  const loaded = lib.recordings.isSuccess;
  const filtering = activeFilterCount(filters) > 0 || view !== "all";
  const empty = loaded && !filtering && lib.rows.length === 0;
  const matching = lib.matching ?? lib.rows.length;
  const processingNow = lib.jobsCounts.running + lib.jobsCounts.queued;
  const countLine = [
    plural(lib.total, "recording"),
    lib.ms ? `${totalDuration(lib.ms)}${namespace ? ` in ${namespace}` : ""}` : null,
    processingNow ? `${count(processingNow)} processing` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const announced = useThrottled(processingNow ? `${count(processingNow)} processing` : "", 10_000);
  const rowProps = {
    rows: shown,
    jobs,
    reviews,
    selected,
    onToggle: toggle,
    onToggleAll: toggleAll,
    onRetry,
    editReason,
    onOpen,
  };

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
          <span className="tabular hidden text-[13px] text-fg-muted sm:inline">
            {lib.recordings.isLoading ? <Skeleton className="w-56" /> : countLine}
          </span>
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

        {isPartial(namespace) && (
          <div
            role="note"
            className="flex items-center gap-2.5 rounded-[10px] border border-border bg-surface-neutral px-3 py-[9px] text-[13px] leading-snug text-fg-strong"
          >
            <Eye className="size-[15px] shrink-0" aria-hidden />
            <span className="flex-1">
              You see the collections of <b className="font-bold">{namespace}</b> you were given access to: their
              recordings, search and the recordings’ pages. Ask an owner of {namespace} for a role in it to see the
              rest.
            </span>
          </div>
        )}
        {(role === "viewer" || viewerEverywhere) && (
          <div
            role="note"
            className="flex items-center gap-2.5 rounded-[10px] border border-border bg-surface-neutral px-3 py-[9px] text-[13px] leading-snug text-fg-strong"
          >
            <Eye className="size-[15px] shrink-0" aria-hidden />
            <span className="flex-1">
              You’re a <b className="font-bold">viewer</b> in {namespace ?? namespaces.map((n) => n.name).join(", ")}:
              you can read, listen, search and chat. Import, reprocess and edits are turned off — ask an owner for
              editor access.
            </span>
          </div>
        )}

        {!empty && (
          <>
            {!narrow && (
              <SourcesStrip
                watches={src.watches}
                sources={src.sources}
                running={lib.jobsCounts.running}
                queued={lib.jobsCounts.queued}
              />
            )}
            {!narrow && (
              <LibraryTabs
                value={view}
                onChange={setView}
                items={[
                  {
                    value: "all",
                    label: "All recordings",
                    count: count(lib.total),
                  },
                  {
                    value: "attention",
                    label: "Needs attention",
                    count: counts.attention ? count(counts.attention) : undefined,
                  },
                  {
                    value: "processing",
                    label: "Processing",
                    count: counts.processing ? count(counts.processing) : undefined,
                  },
                  {
                    value: "mine",
                    label: "Edited by me",
                    count: counts.mine ? count(counts.mine) : undefined,
                  },
                ]}
              />
            )}
            <FiltersBar
              filters={filters}
              onChange={setFilters}
              speakers={speakers.choices}
              speakersLoading={speakers.loading}
              tags={tagCounts.data ?? []}
              objects={objectCounts.data ?? []}
              tagsLoading={tagCounts.isPending}
              inputRef={filterRef}
              compact={narrow}
              origins={origins.data ?? []}
              languages={languages.data ?? []}
              collections={tree.data}
              collectionsLoading={tree.isLoading}
              onManageCollections={() => setCollectionsOpen(true)}
              fields={resourceFields}
              fieldsLoading={fieldDefs.isLoading}
              trailing={
                <SavedViews
                  state={shownState}
                  namespace={namespace}
                  onApply={applyView}
                  originName={(k) => origins.data?.find((o) => o.origin === k)?.name ?? k}
                  collectionName={(id) => collectionName(tree.data, id)}
                  fieldName={(id) => resourceFields.find((f) => f.id === id)?.label ?? null}
                />
              }
            />
          </>
        )}
      </div>

      {held.length > 0 && (
        <div className="sticky top-[72px] z-20 flex justify-center">
          <button
            type="button"
            onClick={() => showHeld()}
            className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-pill bg-blue px-3.5 text-[13px] font-bold text-white shadow-2 hover:bg-blue-dark"
          >
            <ArrowUp className="size-4" aria-hidden />
            {count(held.length)} new
          </button>
        </div>
      )}

      <div
        ref={listRef}
        aria-busy={lib.recordings.isPlaceholderData || undefined}
        className={cn(
          "mt-2.5 flex min-h-0 flex-1 flex-col transition-opacity duration-fast",
          lib.recordings.isPlaceholderData && "opacity-60",
        )}
      >
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
        ) : me && !me.user.admin && Object.keys(me.roles ?? {}).length === 0 && !(me.partial ?? []).length ? (
          <EmptyState className="border-t border-border" title="No namespaces yet">
            You don’t have a role in any namespace. Ask an admin to add you, and the recordings you can see will show up
            here.
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
            Try fewer filters.
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

        {loaded && !empty && matching > 0 && (lib.recordings.hasNextPage || filtering) && (
          <div className="flex flex-wrap items-center gap-3 px-4 py-4 text-[13px] text-fg-secondary md:px-6">
            <span className="tabular">
              {filtering
                ? `${plural(matching, "recording")} ${matching === 1 ? "matches" : "match"}`
                : plural(matching, "recording")}
              {lib.recordings.hasNextPage ? ` · ${count(lib.rows.length)} loaded` : ""}
            </span>
            {lib.recordings.hasNextPage && (
              <Button
                variant="secondary"
                size="sm"
                disabled={lib.recordings.isFetchingNextPage}
                onClick={() => lib.recordings.fetchNextPage()}
              >
                {lib.recordings.isFetchingNextPage ? "Loading…" : `Load ${count(PAGE)} more`}
              </Button>
            )}
          </div>
        )}
        <div className="flex-1" />
        <BulkBar
          selected={selected.size}
          blocked={blocked}
          notOwner={blockedBy(selectedRows, (r) => atLeast(roleOf(r), "owner"))}
          moveTargets={targets}
          onReprocess={() => setReprocessOpen(true)}
          onExport={(fmt) => actions.exportMany([...selected], fmt)}
          onMove={() => setMoveOpen(true)}
          onPlace={() => setPlaceOpen(true)}
          placeReason={placeReason}
          onTag={() => setTagOpen(true)}
          onDelete={() => setDeleteOpen(true)}
          onClear={() => setSelected(new Set())}
        />
      </div>

      <ReprocessDialog
        open={reprocessOpen}
        onOpenChange={setReprocessOpen}
        n={selected.size}
        withoutAudio={selectedRows.filter((r) => r.media_kind === "transcript").length}
        onConfirm={(steps) => actions.reprocess([...selected], steps)}
      />
      <TagDialog
        open={tagOpen}
        onOpenChange={setTagOpen}
        rows={selectedRows}
        known={(tagCounts.data ?? []).map((t) => t.tag)}
        onConfirm={(add, remove) => actions.retag([...selected], add, remove)}
      />
      <MoveDialog
        open={moveOpen}
        onOpenChange={setMoveOpen}
        rows={selectedRows}
        targets={targets}
        onConfirm={async (rows, to, opts, progress) => {
          const moved = await actions.moveMany(rows, to, opts, progress);
          setSelected((cur) => new Set([...cur].filter((id) => !moved.includes(id))));
        }}
      />
      {placeNs && (
        <PlaceDialog
          ns={placeNs}
          ids={selectedRows.map((r) => r.id)}
          title={
            selectedRows.length === 1
              ? `“${selectedRows[0].title || "Untitled"}”`
              : plural(selectedRows.length, "recording")
          }
          current={placeCurrent}
          open={placeOpen}
          onOpenChange={setPlaceOpen}
          onDone={() => setSelected(new Set())}
        />
      )}
      {namespace && (
        <CollectionsDialog
          ns={namespace}
          open={collectionsOpen}
          onOpenChange={setCollectionsOpen}
          onPick={(id) => setFilters((f) => ({ ...f, collection: id }))}
        />
      )}
      <DeleteDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        rows={selectedRows}
        onConfirm={async (progress) => {
          const gone = await actions.deleteMany(selectedRows, progress);
          setSelected((cur) => new Set([...cur].filter((id) => !gone.includes(id))));
        }}
      />

      {dragging && (
        <div className="pointer-events-none absolute inset-2 z-40 grid place-items-center rounded-lg border-2 border-dashed border-blue bg-[color-mix(in_srgb,var(--intent-surface)_92%,transparent)]">
          <div className="flex flex-col items-center gap-2 text-center">
            <Upload className="size-8 text-blue" aria-hidden />
            <p className="text-[16px] font-bold text-fg">
              {canImport ? `Drop to import${namespace ? ` into ${namespace}` : ""}` : "You can’t import here"}
            </p>
            <p className="text-[13px] text-fg-secondary">
              {canImport
                ? "You’ll see how each file was read before anything is saved."
                : needRole("editor", namespace)}
            </p>
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
