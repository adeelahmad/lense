import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReportView } from "@/components/batches/report-view";

export const metadata: Metadata = { title: "Collection report" };

/** A combined report over a batch run's results (CR1). */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  return <ReportView id={Number(id)} />;
}
