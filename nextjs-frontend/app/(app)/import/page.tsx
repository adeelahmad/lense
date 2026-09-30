import type { Metadata } from "next";
import { Suspense } from "react";

import { ImportScreen } from "@/components/import/import-screen";

export const metadata: Metadata = { title: "Import" };

/** Import (I1–I5): upload or paste transcripts, or watch a folder of a source. */
export default function ImportPage() {
  return (
    <Suspense>
      <ImportScreen />
    </Suspense>
  );
}
