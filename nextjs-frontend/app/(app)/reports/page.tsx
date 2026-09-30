import type { Metadata } from "next";
import { Suspense } from "react";

import { ReportsScreen } from "@/components/reports/reports-screen";

export const metadata: Metadata = { title: "Reports" };

/** Reports (RP1 namespace overview, RP2 recording report). The backend's own HTML reports stay at /reports/<ns>/…. */
export default function ReportsPage() {
  return (
    <Suspense>
      <ReportsScreen />
    </Suspense>
  );
}
