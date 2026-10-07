"use client";

import { useQuery } from "@tanstack/react-query";
import { Image as ImageIcon } from "lucide-react";
import Link from "next/link";

import { Search } from "@/app/openapi-client";
import { recordingHref } from "@/components/search/links";
import { data, useApiClient } from "@/lib/api/browser";

/** Photos that show what was searched for (Settings › Search › Search photos by what they show). Nothing shows while
 * that's off, or when no photo is like the query. */
export function PhotoHits({ q, namespace }: { q: string; namespace?: string | null }) {
  const client = useApiClient();
  const photos = useQuery({
    queryKey: ["search-photos", q, namespace ?? null],
    queryFn: () => data(Search.searchPhotos({ client, query: { q, ns: namespace ?? undefined, limit: 12 } })),
    enabled: Boolean(q.trim()),
    retry: false,
    staleTime: 60_000,
  });
  const hits = photos.data?.hits ?? [];
  if (!hits.length) return null;
  return (
    <section aria-label="Photos" className="flex flex-col gap-2 border-b border-border py-4">
      <h2 className="m-0 flex items-center gap-2 text-[13px] font-bold">
        <ImageIcon aria-hidden className="size-[15px] text-fg-secondary" />
        Photos that show it
      </h2>
      <ul className="m-0 flex list-none gap-2.5 overflow-x-auto p-0 pb-1">
        {hits.map((h) => (
          <li key={h.recording} className="shrink-0">
            <Link
              href={recordingHref(h.recording)}
              title={`${h.title ?? "Untitled"} · ${h.namespace ?? ""} · ${h.similarity.toFixed(2)} alike`}
              className="flex w-[120px] flex-col gap-1 rounded-sm text-[12px] text-fg-secondary hover:text-fg"
            >
              {h.thumb ? (
                // eslint-disable-next-line @next/next/no-img-element -- a signed link to a thumbnail the API drew
                <img
                  src={h.thumb}
                  alt=""
                  loading="lazy"
                  className="aspect-[4/3] w-full rounded-[3px] bg-surface-neutral object-cover"
                />
              ) : (
                <span className="grid aspect-[4/3] w-full place-items-center rounded-[3px] bg-surface-neutral">
                  <ImageIcon aria-hidden className="size-5 text-fg-muted" />
                </span>
              )}
              <span className="truncate">{h.title ?? "Untitled"}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
