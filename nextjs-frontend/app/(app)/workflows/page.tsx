import type { Metadata } from "next";

import { WorkflowsPage } from "@/components/workflows/workflows-page";

export const metadata: Metadata = { title: "Workflows" };

/** Workflows: graphs drawn on the canvas that pipelines run to make metadata. */
export default function Page() {
  return <WorkflowsPage />;
}
