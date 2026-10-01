/**
 * Saved views of the Library (GET/POST/PATCH/DELETE /views): what a view keeps of the Library (its tab, filters and
 * sort, with the namespace from the top bar), bringing it back, telling whether the Library shows it now, and how
 * the Views dialog lists and describes them. Tested in __tests__/library-views.test.ts.
 */
import type { SavedView, ViewState } from "@/app/openapi-client/types.gen";
import { fieldFilterLabel } from "@/components/fields/fields-model";
import {
  DATE_LABEL,
  DURATION_LABEL,
  MEDIA_LABEL,
  NO_FILTERS,
  STATUS_FILTER_LABEL,
  languageName,
  type Filters,
  type LibraryView,
  type SortDir,
  type SortKey,
} from "@/components/library/model";

export type LibraryState = { filters: Filters; view: LibraryView; sort: { key: SortKey; dir: SortDir } };
type Sort = NonNullable<ViewState["sort"]>;

/** What a view keeps: the tab, the filters (the speaker by name: ids differ per namespace) and the sort. */
export function viewState({ filters, view, sort }: LibraryState): ViewState {
  return {
    tab: view,
    q: filters.q.trim(),
    statuses: [...filters.statuses],
    speaker: filters.speaker?.name ?? null,
    date: filters.date,
    duration: filters.duration,
    media: filters.media,
    tags: [...filters.tags],
    origins: [...filters.origins],
    languages: [...filters.languages],
    sort: (sort.dir === "desc" ? `-${sort.key}` : sort.key) as Sort,
    collection: filters.collection,
    field: filters.field?.id ?? null,
    value: filters.field?.value.trim() || null,
  };
}

/** The Library a view brings back. Its speaker comes back by name, once that namespace's speakers are known. */
export function fromView(s: ViewState | null | undefined): LibraryState & { speaker: string | null } {
  const sort = s?.sort ?? "-date";
  const desc = sort.startsWith("-");
  return {
    filters: {
      ...NO_FILTERS,
      q: s?.q ?? "",
      statuses: [...(s?.statuses ?? [])],
      date: s?.date ?? "any",
      duration: s?.duration ?? "any",
      media: s?.media ?? "any",
      tags: [...(s?.tags ?? [])],
      origins: [...(s?.origins ?? [])],
      languages: [...(s?.languages ?? [])],
      collection: s?.collection ?? null,
      field: s?.field != null ? { id: s.field, value: s.value ?? "" } : null,
    },
    view: s?.tab ?? "all",
    sort: { key: (desc ? sort.slice(1) : sort) as SortKey, dir: desc ? "desc" : "asc" },
    speaker: s?.speaker ?? null,
  };
}

const sameSet = (a: string[] = [], b: string[] = [], fold = false) => {
  const norm = (x: string[]) => [...new Set(x.map((t) => (fold ? t.toLowerCase() : t)))].sort().join("\u0000");
  return norm(a) === norm(b);
};

/** Whether two states show the same recordings in the same order (tags ignore case; order of choices doesn't matter). */
export function sameState(a: ViewState | null | undefined, b: ViewState | null | undefined): boolean {
  const x = fromView(a);
  const y = fromView(b);
  return (
    x.view === y.view &&
    x.filters.q.trim() === y.filters.q.trim() &&
    sameSet(x.filters.statuses, y.filters.statuses) &&
    (x.speaker ?? "") === (y.speaker ?? "") &&
    x.filters.date === y.filters.date &&
    x.filters.duration === y.filters.duration &&
    x.filters.media === y.filters.media &&
    sameSet(x.filters.tags, y.filters.tags, true) &&
    sameSet(x.filters.origins, y.filters.origins) &&
    sameSet(x.filters.languages, y.filters.languages, true) &&
    x.filters.collection === y.filters.collection &&
    (x.filters.field?.id ?? null) === (y.filters.field?.id ?? null) &&
    (x.filters.field?.value.trim() ?? "") === (y.filters.field?.value.trim() ?? "") &&
    x.sort.key === y.sort.key &&
    x.sort.dir === y.sort.dir
  );
}

/** Whether the Library shows exactly this view now: its namespace, tab, filters and sort. */
export function isShowing(v: Pick<SavedView, "namespace" | "state">, now: ViewState, ns: string | null): boolean {
  return (v.namespace ?? null) === ns && sameState(v.state, now);
}

const TAB_LABEL: Record<LibraryView, string> = {
  all: "All recordings",
  attention: "Needs attention",
  processing: "Processing",
  mine: "Edited by me",
};
const SORT_LABEL: Record<SortKey, string> = {
  date: "date",
  title: "title",
  duration: "length",
  speakers: "speakers",
  status: "status",
  importance: "importance",
};

/** "Needs attention · in Talks · “capsid” · Analyzed · Alice · Last 30 days · #Interview · by title"; `origin` names
 * sources and `collection` collections. */
export function describeView(
  s: ViewState | null | undefined,
  origin: (key: string) => string = (k) => k,
  collection: (id: number) => string | null = () => null,
  field: (id: number) => string | null = () => null,
): string {
  const x = fromView(s);
  const f = x.filters;
  const parts = [
    TAB_LABEL[x.view],
    f.collection != null ? `in ${collection(f.collection) ?? "a collection"}` : null,
    f.field ? fieldFilterLabel({ label: field(f.field.id) ?? "A field", value: f.field.value }) : null,
    f.q ? `“${f.q}”` : null,
    f.statuses.length ? f.statuses.map((t) => STATUS_FILTER_LABEL[t]).join(", ") : null,
    x.speaker,
    f.date !== "any" ? DATE_LABEL[f.date] : null,
    f.duration !== "any" ? DURATION_LABEL[f.duration] : null,
    f.media !== "any" ? MEDIA_LABEL[f.media] : null,
    f.tags.length ? f.tags.map((t) => `#${t}`).join(" ") : null,
    f.origins.length ? f.origins.map(origin).join(", ") : null,
    f.languages.length ? f.languages.map(languageName).join(", ") : null,
    x.sort.key !== "date" || x.sort.dir !== "desc"
      ? `by ${SORT_LABEL[x.sort.key]}${x.sort.dir === "asc" && x.sort.key === "date" ? ", oldest first" : ""}`
      : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

/** "podcasts · shared" for your views, "by ed@lens.test" for others'; "All namespaces" when it shows every one. */
export function viewMeta(v: Pick<SavedView, "namespace" | "shared" | "mine" | "created_by">): string {
  const where = v.namespace ?? "All namespaces";
  if (!v.mine) return `by ${v.created_by ?? "someone"}`;
  return v.shared ? `${where} · shared` : where;
}

export type ViewGroup = { title: string; views: SavedView[] };

/** Yours first, then the ones shared with each namespace (as the API lists them, latest changed first). */
export function groupViews(views: SavedView[] | null | undefined): ViewGroup[] {
  const mine = (views ?? []).filter((v) => v.mine);
  const shared = new Map<string, SavedView[]>();
  for (const v of views ?? []) {
    if (v.mine) continue;
    const key = v.namespace ?? "";
    shared.set(key, [...(shared.get(key) ?? []), v]);
  }
  return [
    ...(mine.length ? [{ title: "Your views", views: mine }] : []),
    ...[...shared].map(([ns, vs]) => ({ title: `Shared in ${ns}`, views: vs })),
  ];
}

/** Why a view can't be shared, or null when it can. */
export function whyNoShare(namespace: string | null, canEdit: boolean, editorsReason: string): string | null {
  if (!namespace) return "Pick a namespace in the top bar first: a view is shared with its namespace";
  return canEdit ? null : editorsReason;
}
