import type { Metadata } from "next";
import { Suspense } from "react";

import { TopicsPage } from "@/components/topics/topics-page";

export const metadata: Metadata = { title: "Topics" };

/** A namespace's vocabulary of topics: what its recordings are about. */
export default function Page() {
  return (
    <Suspense>
      <TopicsPage />
    </Suspense>
  );
}
