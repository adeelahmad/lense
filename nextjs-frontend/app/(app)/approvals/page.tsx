import type { Metadata } from "next";
import { Suspense } from "react";

import { ApprovalsPage } from "@/components/approvals/approvals-page";

export const metadata: Metadata = { title: "Approvals" };

/** What waits for your pick: automations' actions that ask first and the assistant's proposals (backend:
 * GET /api/v1/approvals, docs/automations.md "Approvals"). */
export default function Page() {
  return (
    <Suspense>
      <ApprovalsPage />
    </Suspense>
  );
}
