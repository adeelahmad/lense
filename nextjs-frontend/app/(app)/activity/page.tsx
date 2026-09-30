import type { Metadata } from "next";

import { ActivityPage } from "@/components/activity/activity-page";

export const metadata: Metadata = { title: "Activity" };

/** Activity (A1 runs, A3 workers). */
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab } = await searchParams;
  return <ActivityPage tab={tab === "workers" ? "workers" : "runs"} />;
}
