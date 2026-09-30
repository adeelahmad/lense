import type { Metadata } from "next";
import { Suspense } from "react";

import { RunOnPage } from "@/components/batches/run-on";

export const metadata: Metadata = { title: "Run on…" };

/**
 * "Run on…" (BA1–BA2). Links carry the selection: ?recordings=1,2 · ?collection=5 · ?entity=12 (or 1,3) · ?speaker=4 ·
 * ?ns=podcasts · ?q=refund (with ns/speaker). Optional: ?template=1 · ?pipeline=2 · ?steps=analyze,summarize ·
 * ?label=Northwind%20Labs · ?combine=<instructions> (a collection report: combine when reading finishes).
 */
export default function Page() {
  return (
    <Suspense>
      <RunOnPage />
    </Suspense>
  );
}
