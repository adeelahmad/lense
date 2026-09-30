import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { BatchPage } from "@/components/batches/batch-page";

export const metadata: Metadata = { title: "Batch run" };

/** A batch run: progress, sample check, results table, combine (BA3, CR1). */
export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ report?: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  return <BatchPage id={Number(id)} autoReport={(await searchParams).report === "1"} />;
}
