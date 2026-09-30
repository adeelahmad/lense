import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { JobDetail } from "@/components/activity/job-detail";

export async function generateMetadata({ params }: { params: Promise<{ jobId: string }> }): Promise<Metadata> {
  const { jobId } = await params;
  return { title: `Run #${jobId}` };
}

/** One run: steps, logs, and what it is waiting for (A2, A5). */
export default async function Page({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  const id = Number(jobId);
  if (!Number.isInteger(id) || id < 1) notFound();
  return <JobDetail jobId={id} />;
}
