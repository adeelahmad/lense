import type { Metadata } from "next";
import { Suspense } from "react";

import { GraphPage } from "@/components/graph/graph-page";

export const metadata: Metadata = { title: "Graph" };

/** Graph (GR1–GR2). Supports ?focus=e12 (an entity) or s4 (a speaker), ?ns=, ?scope=all and ?view=table. */
export default function Page() {
  return (
    <Suspense>
      <GraphPage />
    </Suspense>
  );
}
