import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PipelineEditor } from "@/components/pipelines/pipeline-editor";

export const metadata: Metadata = { title: "Pipeline" };

/** One pipeline's editor (PL2). */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <PipelineEditor id={n} />;
}
