import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { WorkflowEditor } from "@/components/workflows/workflow-editor";

export const metadata: Metadata = { title: "Workflow" };

/** One workflow on the canvas. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <WorkflowEditor id={n} />;
}
