import type { Metadata } from "next";

import { WorkflowEditor } from "@/components/workflows/workflow-editor";

export const metadata: Metadata = { title: "New workflow" };

export default function Page() {
  return <WorkflowEditor />;
}
