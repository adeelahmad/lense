import type { Metadata } from "next";
import { Suspense } from "react";

import { ReportSetup } from "@/components/collections/report-setup";

export const metadata: Metadata = { title: "New collection report" };

/** Collection report setup (CR1). ?collection=5 · ?ns=podcasts · ?entity=12 preselect the set. */
export default function Page() {
  return (
    <Suspense>
      <ReportSetup />
    </Suspense>
  );
}
