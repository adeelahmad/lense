import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { RoutineDetail } from "@/components/routines/routine-detail";

export const metadata: Metadata = { title: "Routine" };

/** One routine: its runs and its settings. */
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { id } = await params;
  const { tab } = await searchParams;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <RoutineDetail id={n} tab={tab === "settings" ? "settings" : "runs"} />;
}
