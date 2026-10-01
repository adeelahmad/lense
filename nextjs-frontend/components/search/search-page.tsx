"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, MessagesSquare, Play, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Search, Searches, Speakers } from "@/app/openapi-client";
import type { SavedSearch, SearchResults } from "@/app/openapi-client/types.gen";
import { useRecordingIndex } from "@/components/search/data";
import { FacetPanel } from "@/components/search/facet-panel";
import { fromServer, groupByRecording } from "@/components/search/facets";
import { hasMedia } from "@/components/search/links";
import { NoResults } from "@/components/search/no-results";
import { InlinePlayerBar, useInlinePlayer } from "@/components/search/player";
import {
  activeFilterCount,
  fromParams,
  hasTerms,
  normalizeEmotion,
  parseQuery,
  toParams,
  type SearchFilters,
} from "@/components/search/query";
import { ResultGroups } from "@/components/search/results";
import { SaveSearchDialog } from "@/components/search/save-search";
import { FilterChip, SearchBox, type Chip } from "@/components/search/search-box";
import { SyntaxHelp } from "@/components/search/syntax-help";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

const PAGE = 200;

function ResultsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Searching" className="flex flex-col">
      {[0, 1, 2].map((g) => (
        <div key={g} className="flex flex-col gap-3 border-b border-border py-4">
          <Skeleton className="h-4 w-[40%]" />
          {[0, 1].map((r) => (
            <div key={r} className="flex items-center gap-3">
              <Skeleton className="size-[26px] rounded-full" />
              <Skeleton className="w-12" />
              <Skeleton className="w-16" />
              <Skeleton className="flex-1" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** SE1–SE2: search what was said, grouped by recording, with facets, chips, syntax help and hints when nothing matches. */
export function SearchPage() {
  const params = useSearchParams();
  const router = useRouter();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { namespaces, can } = useArchive();
  const { q, filters } = useMemo(() => fromParams(new URLSearchParams(params.toString())), [params]);
  const [draft, setDraft] = useState(q);
  const [problem, setProblem] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [mobileHelp, setMobileHelp] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const player = useInlinePlayer();
  const index = useRecordingIndex();

  useEffect(() => setDraft(q), [q]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) && !t.isContentEditable) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const enabled = hasTerms(q);
  const nFilters = activeFilterCount(filters);
  const results = useInfiniteQuery({
    queryKey: ["search-results", q, filters],
    queryFn: ({ pageParam }) =>
      data(
        Search.searchTranscripts({
          client,
          query: {
            q,
            ns: filters.namespace,
            speaker: filters.speaker,
            emotion: filters.emotion,
            recording: filters.recording,
            object: filters.object,
            limit: PAGE,
            offset: pageParam,
            // without filters, the first page brings the facets too
            facets: nFilters === 0 && pageParam === 0,
          },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last: SearchResults, pages: SearchResults[]) => {
      const got = pages.reduce((a, p) => a + p.hits.length, 0);
      return got < last.total && last.hits.length === PAGE ? got : undefined;
    },
    enabled,
    staleTime: 30_000,
  });
  // Facets and "without filters" counts come from the words alone.
  const base = useQuery({
    queryKey: ["search-base", q],
    queryFn: () => data(Search.searchTranscripts({ client, query: { q, limit: PAGE, facets: true } })),
    enabled: enabled && nFilters > 0,
    staleTime: 30_000,
  });
  const first = results.data?.pages[0];
  const baseData = nFilters > 0 ? base.data : first;
  const hits = useMemo(() => results.data?.pages.flatMap((p) => p.hits) ?? [], [results.data]);
  const groups = useMemo(() => groupByRecording(hits), [hits]);
  const facets = useMemo(() => (baseData?.facets ? fromServer(baseData.facets) : null), [baseData]);
  const total = first?.total ?? 0;

  const labels = useMemo(() => {
    const out: Partial<Record<keyof SearchFilters, string>> = {};
    if (filters.namespace) out.namespace = filters.namespace;
    if (filters.emotion) out.emotion = filters.emotion;
    if (filters.object) out.object = filters.object;
    if (filters.speaker != null) {
      const hit = [...(baseData?.hits ?? []), ...hits].find((h) => h.speaker_id === filters.speaker);
      out.speaker = hit?.speaker ?? `Speaker #${filters.speaker}`;
    }
    if (filters.recording != null)
      out.recording =
        index.byId.get(filters.recording)?.title ??
        hits.find((h) => h.recording_id === filters.recording)?.title ??
        `Recording #${filters.recording}`;
    return out;
  }, [filters, baseData, hits, index.byId]);

  const go = useCallback((nq: string, f: SearchFilters) => router.push(`/search?${toParams(nq, f)}`), [router]);
  const setFilter = (key: keyof SearchFilters, value: string | number | undefined) =>
    go(q, { ...filters, [key]: value });
  const clearFilter = (key: keyof SearchFilters | "all") =>
    go(q, key === "all" ? {} : { ...filters, [key]: undefined });

  const resolveSpeaker = async (name: string, ns?: string): Promise<number | undefined> => {
    const want = name.trim().toLowerCase();
    const fromHits = [...(baseData?.hits ?? []), ...hits].filter(
      (h) => (h.speaker ?? "").toLowerCase() === want && (!ns || h.namespace === ns),
    );
    if (fromHits[0]?.speaker_id != null) return fromHits[0].speaker_id;
    const spaces = namespaces.filter((n) => !ns || n.name === ns);
    for (const n of spaces) {
      const dir = await qc.fetchQuery({
        queryKey: ["speakers", n.name],
        queryFn: () => data(Speakers.listSpeakers({ client, query: { ns: n.name } })),
        staleTime: 60_000,
      });
      const s = dir.speakers.find(
        (x) =>
          x.display.toLowerCase() === want || (x.name ?? "").toLowerCase() === want || x.label.toLowerCase() === want,
      );
      if (s) return s.id;
    }
    return undefined;
  };

  const submit = async () => {
    const { text, typed } = parseQuery(draft);
    const next: SearchFilters = { ...filters };
    const issues: string[] = [];
    if (typed.namespace) {
      const ns = namespaces.find((n) => n.name.toLowerCase() === typed.namespace!.toLowerCase());
      if (ns) next.namespace = ns.name;
      else issues.push(`You have no namespace called “${typed.namespace}”.`);
    }
    if (typed.emotion) next.emotion = normalizeEmotion(typed.emotion);
    if (typed.object) next.object = typed.object.trim().toLowerCase();
    if (typed.recording) {
      const want = typed.recording.toLowerCase();
      const all = index.data ?? [];
      const r =
        all.find((x) => (x.title ?? "").toLowerCase() === want) ??
        all.find((x) => (x.title ?? "").toLowerCase().startsWith(want)) ??
        all.find((x) => (x.title ?? "").toLowerCase().includes(want));
      if (r) next.recording = r.id;
      else issues.push(`No recording called “${typed.recording}”.`);
    }
    if (typed.speaker) {
      try {
        const id = await resolveSpeaker(typed.speaker, next.namespace);
        if (id != null) next.speaker = id;
        else issues.push(`No speaker called “${typed.speaker}”${next.namespace ? ` in ${next.namespace}` : ""}.`);
      } catch {
        issues.push(`Couldn’t look up the speaker “${typed.speaker}”.`);
      }
    }
    setProblem(issues.join(" ") || null);
    if (hasTerms(text)) go(text, next);
    else if (issues.length === 0 && !text) setProblem("Type the words to look for, as well as the filters.");
    setDraft(text);
  };

  const chips: Chip[] = (["namespace", "speaker", "emotion", "recording", "object"] as const)
    .filter((k) => filters[k] != null && filters[k] !== "")
    .map((k) => ({
      key: k,
      label: `${k}: ${labels[k]}`,
      onRemove: () => clearFilter(k),
    }));

  const saved = useQuery({
    queryKey: ["saved-searches"],
    queryFn: () => data(Searches.listSearches({ client })),
    staleTime: 60_000,
  });
  const forget = useMutation({
    mutationFn: (s: SavedSearch) => data(Searches.deleteSearch({ client, path: { sid: s.id } })),
    onSuccess: (_r, s) => {
      void qc.invalidateQueries({ queryKey: ["saved-searches"] });
      toast({ title: `“${s.name}” deleted` });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t delete the saved search", body: e.message }),
  });

  const chatHref = `/chat?${new URLSearchParams({ q, ...(filters.namespace ? { ns: filters.namespace } : {}), ...(filters.speaker != null ? { speaker: String(filters.speaker) } : {}), ...(filters.recording != null ? { recording: String(filters.recording) } : {}) })}`;
  const runHref = `/batches/new?${new URLSearchParams({ q, from: "search", ...(filters.namespace ? { ns: filters.namespace } : {}), ...(filters.speaker != null ? { speaker: String(filters.speaker) } : {}), ...(filters.recording != null ? { recordings: String(filters.recording) } : {}) })}`;
  const canRun = can("editor", filters.namespace);

  const understood =
    first?.query && first.query.replace(/\s+/g, " ").toLowerCase() !== q.replace(/\s+/g, " ").toLowerCase()
      ? first.query
      : null;
  const audioOf = (rid: number) => hasMedia(index.byId.get(rid));

  const facetPanel = (
    <FacetPanel
      facets={facets}
      filters={filters}
      labels={labels}
      loading={enabled && !facets && (results.isLoading || base.isLoading)}
      partial={Boolean(baseData?.facets?.partial)}
      onToggle={(k, v) => {
        setFilter(k, v);
        setFiltersOpen(false);
      }}
      saved={saved.data ?? null}
      onDeleteSaved={(s) => forget.mutate(s)}
      deletingSaved={forget.isPending ? (forget.variables?.id ?? null) : null}
    />
  );

  return (
    <div className="flex min-h-[calc(100vh-4rem)]">
      <aside
        aria-label="Filters"
        className="sticky top-16 hidden h-[calc(100vh-4rem)] w-[240px] shrink-0 overflow-y-auto border-r border-border bg-surface px-3.5 py-[18px] md:block"
      >
        {facetPanel}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-16 z-10 flex flex-col gap-2.5 border-b border-border bg-background px-4 pb-3 pt-[18px] md:px-7">
          <h1 className="sr-only">Search</h1>
          <div className="flex items-center gap-2.5">
            <SearchBox
              ref={inputRef}
              value={draft}
              onChange={setDraft}
              onSubmit={submit}
              onArrowDown={() => listRef.current?.querySelector<HTMLElement>("[data-hit]")?.focus()}
              chips={chips}
              helpOpen={helpOpen}
              onHelpOpenChange={setHelpOpen}
              onPickExample={(ex) => {
                setDraft((d) => (d.trim() ? `${d.trim()} ${ex}` : ex));
                inputRef.current?.focus();
              }}
              className="hidden md:flex"
            />
            <SearchBox
              value={draft}
              onChange={setDraft}
              onSubmit={submit}
              onClear={() => setDraft("")}
              onArrowDown={() => listRef.current?.querySelector<HTMLElement>("[data-hit]")?.focus()}
              chips={chips}
              helpOpen={false}
              onHelpOpenChange={() => undefined}
              compact
              className="md:hidden"
            />
            <Button asChild variant="secondary" className="hidden md:inline-flex" disabled={!enabled}>
              {enabled ? (
                <Link href={chatHref}>
                  <MessagesSquare /> Ask in chat
                </Link>
              ) : (
                <span>
                  <MessagesSquare /> Ask in chat
                </span>
              )}
            </Button>
            <Button
              variant="ghost"
              icon={<BookmarkPlus />}
              className="hidden md:inline-flex"
              disabled={!enabled}
              disabledReason="Search for something first"
              onClick={() => setSaveOpen(true)}
            >
              Save
            </Button>
          </div>
          <div className="flex gap-1.5 md:hidden">
            <Button
              size="sm"
              variant="ghost"
              className="border-border"
              icon={<SlidersHorizontal />}
              onClick={() => setFiltersOpen(true)}
            >
              Filters{nFilters ? ` · ${nFilters}` : ""}
            </Button>
            {enabled && (
              <Button asChild size="sm" variant="ghost" className="border-border">
                <Link href={chatHref}>Ask in chat</Link>
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              className="w-8 border-border px-0"
              aria-label="Search syntax help"
              onClick={() => setMobileHelp(true)}
            >
              ?
            </Button>
          </div>
          {problem && (
            <p role="alert" className="m-0 text-[13px] text-red-dark">
              {problem}
            </p>
          )}
          {chips.length > 0 && (
            <div className="flex flex-wrap gap-1.5 md:hidden">
              {chips.map((c) => (
                <FilterChip key={c.key} chip={c} />
              ))}
            </div>
          )}
          {enabled && first && total > 0 && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="tabular m-0 text-[13px] text-fg-secondary" aria-live="polite">
                <b className="text-fg">{plural(total, "moment")}</b> in {count(groups.length)}
                {hits.length < total ? "+" : ""} {groups.length === 1 ? "recording" : "recordings"}
                {understood && (
                  <>
                    {" "}
                    · searched for <code className="font-mono text-[12.5px] text-fg-strong">{understood}</code>
                  </>
                )}
                {nFilters > 0 && base.data && base.data.total !== total && (
                  <> · {plural(base.data.total, "moment")} without filters</>
                )}
              </p>
              <span className="flex-1" />
              <Button
                asChild={canRun}
                size="xs"
                variant="ghost"
                icon={canRun ? undefined : <Play />}
                disabled={!canRun}
                disabledReason={needRole("editor", filters.namespace)}
                className="text-fg-accent"
              >
                {canRun ? (
                  <Link href={runHref}>
                    <Play /> Run on {groups.length}
                    {hits.length < total ? "+" : ""}
                  </Link>
                ) : (
                  `Run on ${groups.length}`
                )}
              </Button>
            </div>
          )}
        </div>

        <div className="flex-1 px-4 pb-24 md:px-7">
          {!enabled && (
            <Intro
              onPick={(ex) => {
                setDraft(ex);
                inputRef.current?.focus();
              }}
            />
          )}
          {enabled && results.isLoading && <ResultsSkeleton />}
          {enabled && results.isError && (
            <Banner
              tone="error"
              className="mt-6"
              title="Search didn’t work."
              action={
                <Button size="sm" variant="secondary" onClick={() => results.refetch()}>
                  Try again
                </Button>
              }
            >
              {results.error.message}
            </Banner>
          )}
          {enabled && first && total === 0 && (
            <NoResults
              q={q}
              filters={filters}
              labels={labels}
              baseTotal={nFilters > 0 ? (base.data?.facets?.moments ?? base.data?.total ?? null) : 0}
              nsRecordings={namespaces.find((n) => n.name === filters.namespace)?.recordings as number | undefined}
              onSearch={(nq) => go(nq, filters)}
              onClearFilter={clearFilter}
            />
          )}
          {groups.length > 0 && (
            <ResultGroups ref={listRef} groups={groups} player={player} audioOf={audioOf} className="pt-1.5" />
          )}
          {results.hasNextPage && (
            <div className="flex justify-center py-5">
              <Button variant="secondary" onClick={() => results.fetchNextPage()} disabled={results.isFetchingNextPage}>
                {results.isFetchingNextPage ? "Loading…" : `Show more moments (${count(total - hits.length)} more)`}
              </Button>
            </div>
          )}
          {first?.capped && !results.hasNextPage && hits.length > 0 && (
            <p className="py-4 text-center text-[12.5px] text-fg-muted">
              The search stopped at its limit; add words or filters to narrow it.
            </p>
          )}
        </div>
        <InlinePlayerBar player={player} className="sticky bottom-4 z-20 px-4 md:px-7" />
      </div>

      <Dialog open={filtersOpen} onOpenChange={setFiltersOpen} title="Filters">
        {facetPanel}
      </Dialog>
      <Dialog open={mobileHelp} onOpenChange={setMobileHelp} title="Search help">
        <SyntaxHelp
          onPick={(ex) => {
            setDraft((d) => (d.trim() ? `${d.trim()} ${ex}` : ex));
            setMobileHelp(false);
          }}
        />
      </Dialog>
      <SaveSearchDialog open={saveOpen} onOpenChange={setSaveOpen} q={q} filters={filters} labels={labels} />
    </div>
  );
}

function Intro({ onPick }: { onPick: (example: string) => void }) {
  return (
    <div className="flex max-w-[620px] flex-col gap-5 py-10">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-[20px] font-bold leading-tight text-fg">Search what was said</h2>
        <p className="m-0 text-[14px] leading-normal text-fg-secondary">
          Every word must appear in the same segment, and word forms match (English stemming). Results only come from
          namespaces you can read, grouped by recording. Pick an example to start from it.
        </p>
      </div>
      <div className="rounded-lg border border-border p-4">
        <SyntaxHelp onPick={onPick} />
      </div>
    </div>
  );
}
