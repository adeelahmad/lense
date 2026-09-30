"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { FolderOpen, Star } from "lucide-react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useState } from "react";

import { Public } from "@/app/openapi-client";
import { rightsFor } from "@/components/iiif/rights";
import { CollectionCard, RecordingCard } from "@/components/public/cards";
import { safeHref } from "@/components/public/model";
import { LoadError, Unavailable } from "@/components/public/states";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Pagination } from "@/components/ui/table";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";

const PAGE = 48;
const GRID = "grid gap-4 sm:grid-cols-2 lg:grid-cols-3";

function usePublicQuery() {
  const client = useApiClient();
  const { status } = useSession();
  return { client, signedIn: status === "authenticated", ready: status !== "loading" };
}

function CardsSkeleton({ n = 3 }: { n?: number }) {
  return (
    <div className={GRID} aria-busy="true" aria-label="Loading">
      {Array.from({ length: n }, (_, i) => (
        <Skeleton key={i} className="h-[260px] w-full" />
      ))}
    </div>
  );
}

/** The home page for visitors (Aviary's home): featured public recordings, then the collections they can browse. */
export function PublicHomeView() {
  const { client, signedIn, ready } = usePublicQuery();
  const q = useQuery({
    queryKey: ["public", "home", signedIn],
    queryFn: () => data(Public.getPublicHome({ client })),
    enabled: ready,
  });
  if (q.isError) return <LoadError what="the archive" message={q.error.message} retry={() => q.refetch()} />;
  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-8 px-4 py-8 sm:px-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-[28px] font-bold leading-[1.15] text-fg sm:text-[34px]">Explore the archive</h1>
        <p className="max-w-[62ch] text-[15px] leading-[1.55] text-fg-secondary">
          Listen to and read the recordings their owners have made public.
        </p>
      </header>
      <section aria-labelledby="featured-heading" className="flex flex-col gap-3">
        <h2 id="featured-heading" className="flex items-center gap-2 text-[18px] font-bold text-fg">
          <Star aria-hidden className="size-4 fill-current text-fg-secondary" />
          Featured
        </h2>
        {!q.data ? (
          <CardsSkeleton />
        ) : q.data.featured.length ? (
          <div className={GRID}>
            {q.data.featured.map((c) => (
              <RecordingCard key={c.id} card={c} showNamespace showFeatured={false} />
            ))}
          </div>
        ) : (
          <EmptyState icon={<Star />} title="Nothing featured yet">
            Owners feature public recordings in a recording’s access settings.
          </EmptyState>
        )}
      </section>
      <section aria-labelledby="collections-heading" className="flex flex-col gap-3">
        <h2 id="collections-heading" className="flex items-center gap-2 text-[18px] font-bold text-fg">
          <FolderOpen aria-hidden className="size-4 text-fg-secondary" />
          Collections
        </h2>
        {!q.data ? (
          <Skeleton className="h-20 w-full" />
        ) : q.data.collections.length ? (
          <div className={GRID}>
            {q.data.collections.map((c) => (
              <CollectionCard key={c.name} c={c} />
            ))}
          </div>
        ) : (
          <EmptyState icon={<FolderOpen />} title="Nothing is public yet">
            Collections appear here once they have recordings you can see.
          </EmptyState>
        )}
      </section>
    </div>
  );
}

/** A collection's page for visitors (Aviary's collection splash page): its description and the recordings they see. */
export function PublicCollectionView({ name }: { name: string }) {
  const { client, signedIn, ready } = usePublicQuery();
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["public", "collection", name, offset, signedIn],
    queryFn: () => data(Public.getPublicCollection({ client, path: { name }, query: { limit: PAGE, offset } })),
    enabled: ready,
    placeholderData: keepPreviousData,
  });
  if (q.isError)
    return q.error instanceof ApiError && q.error.status === 404 ? (
      <Unavailable what="collection" signedIn={signedIn} />
    ) : (
      <LoadError what="this collection" message={q.error.message} retry={() => q.refetch()} />
    );
  const c = q.data;
  const rights = rightsFor(c?.rights);
  const provider = c?.provider as { name?: string; homepage?: string } | null | undefined;
  const rightsHref = safeHref(c?.rights);
  const providerHref = safeHref(provider?.homepage);
  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-4 py-8 sm:px-6">
      <header className="flex flex-col gap-2">
        <Link href="/explore" className="w-fit text-[12.5px] font-semibold text-fg-muted hover:text-fg hover:underline">
          Explore
        </Link>
        {c ? (
          <>
            <h1 className="text-[26px] font-bold leading-[1.2] text-fg sm:text-[30px]">{c.label}</h1>
            {c.summary && <p className="max-w-[72ch] text-[15px] leading-[1.55] text-fg">{c.summary}</p>}
            <p className="tabular flex flex-wrap gap-x-3 gap-y-1 text-[13px] text-fg-secondary">
              <span>
                {count(c.total)} recording{c.total === 1 ? "" : "s"}
              </span>
              {c.rights && (
                <span>
                  {rightsHref ? (
                    <a href={rightsHref} target="_blank" rel="noopener noreferrer" className="hover:underline">
                      {rights?.code ?? c.rights}
                    </a>
                  ) : (
                    (rights?.code ?? c.rights)
                  )}
                </span>
              )}
              {c.attribution && <span>{c.attribution}</span>}
              {provider?.name && (
                <span>
                  {providerHref ? (
                    <a href={providerHref} target="_blank" rel="noopener noreferrer" className="hover:underline">
                      {provider.name}
                    </a>
                  ) : (
                    provider.name
                  )}
                </span>
              )}
            </p>
          </>
        ) : (
          <>
            <Skeleton className="h-8 w-2/5" />
            <Skeleton className="h-4 w-3/5" />
          </>
        )}
      </header>
      {c?.member && (
        <Banner
          action={
            <Button asChild size="sm">
              <Link href="/library">Open the Library</Link>
            </Button>
          }
        >
          You’re a member of <b>{c.name}</b>, so you see all of its recordings. Visitors see the public ones.
        </Banner>
      )}
      {!c ? (
        <CardsSkeleton n={6} />
      ) : c.items.length ? (
        <div className={GRID} aria-busy={q.isFetching || undefined}>
          {c.items.map((it) => (
            <RecordingCard key={it.id} card={it} />
          ))}
        </div>
      ) : (
        <EmptyState icon={<FolderOpen />} title="No recordings yet">
          Recordings imported into {c.name} appear here.
        </EmptyState>
      )}
      {c && c.total > PAGE && (
        <Pagination offset={offset} limit={PAGE} total={c.total} onChange={setOffset} loading={q.isFetching} />
      )}
    </div>
  );
}
