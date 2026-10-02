"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Lock, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Public } from "@/app/openapi-client";
import type { PublicResult } from "@/app/openapi-client/types.gen";
import { KIND_ICON } from "@/components/public/cards";
import { usePublicClient } from "@/components/public/hooks";
import { cardLine, collectionPath, hitWhere, publicPath, searchPath } from "@/components/public/model";
import { LoadError } from "@/components/public/states";
import { ByMeaning, MeaningToggle } from "@/components/search/meaning";
import { splitSnippet } from "@/components/search/snippet";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Pagination } from "@/components/ui/table";
import { data } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE = 20;

/** The search box for the pages visitors see; it opens the search page. */
export function PublicSearchForm({
  initial = "",
  meaning = false,
  className,
}: {
  initial?: string;
  /** Keep searching by meaning too. */
  meaning?: boolean;
  className?: string;
}) {
  const router = useRouter();
  const [q, setQ] = useState(initial);
  useEffect(() => setQ(initial), [initial]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    router.push(searchPath(q, meaning));
  };
  return (
    <form role="search" onSubmit={submit} className={cn("flex min-w-0 gap-2", className)}>
      <SearchInput
        aria-label="Search the archive"
        placeholder="Search titles and transcripts"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="min-w-0 flex-1"
      />
      <Button type="submit" variant="primary" size="sm" className="h-9">
        Search
      </Button>
    </form>
  );
}

/**
 * Search for visitors (Aviary's search results page): titles of the recordings they see, and lines of the transcripts
 * they may read. Restricted recordings show signed-in people their title behind a lock.
 */
export function PublicSearchView({ q, meaning = false }: { q: string; meaning?: boolean }) {
  const router = useRouter();
  const { client, signedIn, ready } = usePublicClient();
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [q, meaning]);
  const query = q.trim();
  const res = useQuery({
    queryKey: ["public", "search", query, offset, signedIn, meaning],
    queryFn: () => data(Public.searchPublic({ client, query: { q: query, limit: PAGE, offset, semantic: meaning } })),
    enabled: ready && Boolean(query),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-col gap-5 px-4 py-8 sm:px-6">
      <h1 className="text-[24px] font-bold leading-tight text-fg">Search the archive</h1>
      <PublicSearchForm initial={q} meaning={meaning} />
      {/* offered where the archive searches by meaning (the first answer says so) */}
      {res.data?.semantic && (
        <MeaningToggle checked={meaning} available onChange={(on) => router.push(searchPath(q, on))} />
      )}
      {!query ? (
        <EmptyState icon={<Search />} title="What are you looking for?">
          Search finds recordings by their title, and by what’s said in the transcripts open to you.
        </EmptyState>
      ) : res.isError ? (
        <LoadError what="the results" message={res.error.message} retry={() => res.refetch()} />
      ) : !res.data ? (
        <SkeletonRows rows={4} />
      ) : !res.data.total ? (
        <EmptyState icon={<Search />} title={`Nothing matches “${query}”`}>
          {signedIn
            ? "Try other words, or fewer of them."
            : "Try other words, or fewer of them. Signing in may show recordings that aren’t public."}
        </EmptyState>
      ) : (
        <>
          <p className="text-[13px] text-fg-secondary" aria-live="polite">
            {count(res.data.total)} recording{res.data.total === 1 ? "" : "s"}
            {res.data.capped ? " (the first matches)" : ""}
          </p>
          <ol className="flex flex-col gap-3" aria-busy={res.isFetching || undefined}>
            {res.data.items.map((r) => (
              <Result key={r.id} r={r} />
            ))}
          </ol>
          {res.data.total > PAGE && (
            <Pagination
              offset={offset}
              limit={PAGE}
              total={res.data.total}
              onChange={setOffset}
              loading={res.isFetching}
            />
          )}
        </>
      )}
    </div>
  );
}

function Result({ r }: { r: PublicResult }) {
  const locked = r.view === "locked";
  const hits = r.hits ?? [];
  const Icon = KIND_ICON[r.media_kind];
  return (
    <li className="flex gap-3.5 rounded-md border border-border bg-background p-3.5">
      <Link
        href={publicPath(r.id)}
        tabIndex={-1}
        aria-hidden
        className="relative hidden h-[68px] w-[120px] shrink-0 overflow-hidden rounded-sm bg-surface-neutral sm:block"
      >
        {r.poster ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={r.poster} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="grid size-full place-items-center text-fg-muted">
            <Icon className="size-6" />
          </span>
        )}
        {locked && (
          <span className="absolute left-1.5 top-1.5 grid size-6 place-items-center rounded-full bg-background text-fg shadow-1">
            <Lock className="size-3" />
          </span>
        )}
      </Link>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <Link
          href={publicPath(r.id)}
          className="text-[15px] font-bold leading-snug text-fg hover:underline"
          aria-label={locked ? `${r.title || "Untitled"} (content locked)` : undefined}
        >
          {r.title || "Untitled"}
        </Link>
        <span className="flex flex-wrap gap-x-2 text-[12.5px] text-fg-secondary">
          {r.namespace && (
            <Link href={collectionPath(r.namespace)} className="font-semibold hover:underline">
              {r.namespace}
            </Link>
          )}
          <span className="tabular">{cardLine(r)}</span>
        </span>
        {locked ? (
          <span className="flex items-center gap-1.5 text-[12.5px] font-semibold text-fg-muted">
            <Lock aria-hidden className="size-3.5" />
            Content locked
          </span>
        ) : hits.length ? (
          <ul className="mt-1 flex flex-col gap-1">
            {hits.map((h, i) => {
              const where = hitWhere(r.id, h);
              return (
                <li key={i} className="grid grid-cols-[44px_minmax(0,1fr)] gap-2 text-[13.5px] leading-[1.45]">
                  <Link
                    href={where.href}
                    className="tabular text-[12.5px] font-medium text-fg-accent hover:underline"
                    aria-label={`Open at ${where.label}`}
                  >
                    {where.label}
                  </Link>
                  <span className="font-serif text-fg">
                    {h.match === "meaning" && <ByMeaning />}
                    {splitSnippet(h.snippet).map((p, k) =>
                      p.mark ? (
                        <mark key={k} className="rounded-[2px] bg-hl-word text-fg">
                          {p.text}
                        </mark>
                      ) : (
                        <span key={k}>{p.text}</span>
                      ),
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : r.summary ? (
          <p className="line-clamp-2 text-[13px] leading-[1.45] text-fg-secondary">{r.summary}</p>
        ) : null}
      </div>
    </li>
  );
}
