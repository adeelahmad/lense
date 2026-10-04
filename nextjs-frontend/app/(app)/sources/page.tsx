import type { Metadata } from "next";
import { Suspense } from "react";

import { SourcesPage } from "@/components/sources/sources-page";

export const metadata: Metadata = { title: "Sources" };

/** Sources: storage connections and watched folders (SO1–SO3). `?add=1` opens Add connection. */
export default function Page() {
  return (
    <Suspense>
      <SourcesPage />
    </Suspense>
  );
}
