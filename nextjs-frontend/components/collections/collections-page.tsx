"use client";

import { useQuery } from "@tanstack/react-query";
import { FileText, FolderHeart, FolderSearch, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Collections } from "@/app/openapi-client";
import { CollectionDialog } from "@/components/collections/collection-dialog";
import { describeCollection } from "@/components/collections/describe";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

export function useCollections() {
  const client = useApiClient();
  return useQuery({ queryKey: ["collections"], queryFn: () => data(Collections.listCollections({ client })), staleTime: 30_000 });
}

/** CR1: saved collections — yours and the ones shared with you — with New and a way into collection reports. */
export function CollectionsPage() {
  const router = useRouter();
  const { me, can } = useArchive();
  const list = useCollections();
  const [creating, setCreating] = useState(false);
  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-6">
      <PageHeader
        className="mb-0"
        title="Collections"
        meta="Saved sets of recordings"
        actions={
          <>
            <Button asChild={can("editor")} variant="secondary" icon={can("editor") ? undefined : <FileText />} disabled={!can("editor")} disabledReason="Collection reports need editor access to a namespace">
              {can("editor") ? (
                <Link href="/collections/report">
                  <FileText /> New collection report
                </Link>
              ) : (
                "New collection report"
              )}
            </Button>
            <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
              New
            </Button>
          </>
        }
      />
      {list.isLoading && <SkeletonRows rows={3} />}
      {list.isError && (
        <Banner
          tone="error"
          action={
            <Button size="sm" variant="secondary" onClick={() => list.refetch()}>
              Try again
            </Button>
          }
        >
          {list.error.message}
        </Banner>
      )}
      {list.data?.length === 0 && (
        <EmptyState
          icon={<FolderSearch />}
          title="No collections yet"
          actions={
            <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
              New collection
            </Button>
          }
        >
          A collection is a saved filter (it keeps up to date) or a fixed list of recordings. Saving a search also makes one.
        </EmptyState>
      )}
      {list.data && list.data.length > 0 && (
        <section aria-labelledby="saved" className="max-w-[760px] rounded-lg border border-border px-5 py-4">
          <h2 id="saved" className="pb-2 text-[15px] font-bold text-fg">
            Saved collections
          </h2>
          <ul className="m-0 list-none p-0">
            {list.data.map((c) => {
              const Icon = c.kind === "fixed" ? FolderHeart : FolderSearch;
              return (
                <li key={c.id} className="border-t border-border first:border-t-0">
                  <Link href={`/collections/${c.id}`} className="flex items-center gap-3 rounded-sm px-1 py-3 hover:bg-surface">
                    <Icon aria-hidden className="size-[18px] shrink-0 text-fg-secondary" />
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate text-[13.5px] font-semibold text-fg">{c.name}</span>
                      <span className="truncate text-[11.5px] text-fg-muted">{describeCollection(c, me?.user.id)}</span>
                    </span>
                    <span className="tabular text-[12.5px] font-semibold text-fg-secondary">{count(c.count)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
          <p className="m-0 border-t border-border pt-3 text-[12px] leading-snug text-fg-muted">Use a collection as a chat scope, for batch runs and for collection reports.</p>
        </section>
      )}
      <CollectionDialog open={creating} onOpenChange={setCreating} onSaved={(id) => router.push(`/collections/${id}`)} />
    </div>
  );
}
