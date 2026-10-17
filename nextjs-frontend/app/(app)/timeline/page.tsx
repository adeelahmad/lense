import type { Metadata } from "next";
import { Suspense } from "react";

import { TimelinePage } from "@/components/timeline/timeline-page";

export const metadata: Metadata = { title: "Timeline" };

/** What came in from every source, newest first, to review (backend: GET /api/v1/activities, docs/timeline.md). */
export default function Page() {
  return (
    <Suspense>
      <TimelinePage />
    </Suspense>
  );
}
