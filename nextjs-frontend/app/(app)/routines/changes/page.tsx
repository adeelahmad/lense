import type { Metadata } from "next";

import { GraphChangesPage } from "@/components/routines/graph-changes";

export const metadata: Metadata = { title: "Graph changes" };

/** Changes graph workflows proposed or made to the entity graph, to accept, dismiss or undo. */
export default async function Page({ searchParams }: { searchParams: Promise<{ run?: string }> }) {
  const { run } = await searchParams;
  const n = Number(run);
  return <GraphChangesPage run={Number.isInteger(n) && n > 0 ? n : undefined} />;
}
