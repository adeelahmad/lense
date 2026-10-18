import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { AutomationPage } from "@/components/automations/automation-page";

export const metadata: Metadata = { title: "Automation" };

/** One automation: the builder, and what it has done (`?tab=runs|upcoming|variables|costs` opens a tab). */
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
  return <AutomationPage id={n} tab={tab} />;
}
