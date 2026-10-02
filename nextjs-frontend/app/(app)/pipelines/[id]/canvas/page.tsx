import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PipelineCanvas } from "@/components/pipelines/pipeline-canvas";

export const metadata: Metadata = { title: "Pipeline canvas" };

/** One pipeline drawn as a graph: its steps and the workflows it runs. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <PipelineCanvas id={n} />;
}
