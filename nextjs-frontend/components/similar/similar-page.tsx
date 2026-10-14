"use client";

import { Search as SearchIcon, Sparkles } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { SimilarNote, SimilarRows } from "@/components/similar/similar-list";
import { basisFromParams, useSimilarPages, type SimilarBasis } from "@/components/similar/model";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { plural } from "@/lib/format";

/** What the page is like, as a heading: the selected text, or a link to the resource, topic or entity. */
function Like({ basis, label }: { basis: SimilarBasis; label: string | null | undefined }) {
  if ("text" in basis)
    return (
      <span className="font-serif font-normal italic">
        “{(label ?? basis.text).length > 110 ? `${(label ?? basis.text).slice(0, 107)}…` : (label ?? basis.text)}”
      </span>
    );
  const [href, what] =
    "recording" in basis
      ? [`/resources/${basis.recording}`, ""]
      : "topic" in basis
        ? [`/topics/${basis.topic}`, "the topic "]
        : [`/entities/${basis.entity}`, ""];
  return (
    <>
      {what}
      <Link href={href} className="hover:text-fg-accent hover:underline">
        {label ?? "…"}
      </Link>
    </>
  );
}

/** Similar (/similar?text= | recording= | topic= | entity=): every resource like it, best first, with why and the
 * moments that show it, a page at a time. */
export function SimilarPage() {
  const params = useSearchParams();
  const basis = useMemo(() => basisFromParams(new URLSearchParams(params.toString())), [params]);
  const q = useSimilarPages(basis);
  const first = q.data?.pages[0];
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];

  if (!basis)
    return (
      <div className="px-4 py-6 md:px-6">
        <PageHeader title="Similar" />
        <EmptyState icon={<Sparkles />} title="Select something first">
          Select any text in Lens, or open a resource&apos;s Similar tab, then choose Find more to see everything like
          it.
        </EmptyState>
      </div>
    );

  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-col gap-3 px-4 py-6 md:px-6">
      <PageHeader
        className="mb-0"
        title={
          <>
            Like <Like basis={basis} label={first?.label} />
          </>
        }
        meta={first ? plural(first.total, "resource") : undefined}
        actions={
          first?.terms?.length ? (
            <Button asChild variant="secondary" size="sm">
              <Link href={`/search?q=${encodeURIComponent(first.terms.slice(0, 4).join(" OR "))}`}>
                <SearchIcon aria-hidden className="size-3.5" />
                Search the key words
              </Link>
            </Button>
          ) : undefined
        }
      >
        {first && <SimilarNote res={first} className="mt-1 text-[13px]" />}
      </PageHeader>
      {q.isLoading ? (
        <SkeletonRows rows={6} />
      ) : q.isError ? (
        <EmptyState tone="error" title="Couldn't find similar content">
          {q.error instanceof Error ? q.error.message : "Try again."}
        </EmptyState>
      ) : !items.length ? (
        <EmptyState icon={<Sparkles />} title="Nothing similar yet">
          Nothing you can read shares its meaning, key words, topics or entities. Similar content shows up as more is
          added and analysed.
        </EmptyState>
      ) : (
        <>
          <SimilarRows items={items} />
          {q.hasNextPage && (
            <Button
              variant="secondary"
              className="self-center"
              onClick={() => void q.fetchNextPage()}
              disabled={q.isFetchingNextPage}
            >
              {q.isFetchingNextPage ? "Loading…" : "Show more"}
            </Button>
          )}
        </>
      )}
    </div>
  );
}
