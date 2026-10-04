import type { Metadata } from "next";

import { GraphHistoryPage } from "@/components/routines/graph-history";

export const metadata: Metadata = { title: "Graph history" };

/** Every change to the entity graph, to see what changed, name a version or roll back to one. */
export default async function Page({ searchParams }: { searchParams: Promise<{ entity?: string }> }) {
  const { entity } = await searchParams;
  const n = Number(entity);
  return <GraphHistoryPage entity={Number.isInteger(n) && n > 0 ? n : undefined} />;
}
