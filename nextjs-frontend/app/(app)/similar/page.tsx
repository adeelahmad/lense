import type { Metadata } from "next";
import { Suspense } from "react";

import { SimilarPage } from "@/components/similar/similar-page";

export const metadata: Metadata = { title: "Similar" };

/** Everything like a selected text, a resource, a topic or an entity ("Find more"). */
export default function Page() {
  return (
    <Suspense>
      <SimilarPage />
    </Suspense>
  );
}
