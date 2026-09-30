import type { Metadata } from "next";
import { Suspense } from "react";

import { SearchPage } from "@/components/search/search-page";

export const metadata: Metadata = { title: "Search" };

/** Search (SE1–SE2). */
export default function Page() {
  return (
    <Suspense>
      <SearchPage />
    </Suspense>
  );
}
