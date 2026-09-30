import type { Metadata } from "next";

import { PublicSearchView } from "@/components/public/search-view";

export const metadata: Metadata = { title: "Search", robots: { index: false } };

type Props = { searchParams: Promise<{ q?: string | string[] }> };

/** Search for visitors (docs/access.md). */
export default async function PublicSearchRoute({ searchParams }: Props) {
  const { q } = await searchParams;
  return <PublicSearchView q={(Array.isArray(q) ? q[0] : q) ?? ""} />;
}
