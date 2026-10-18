import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { AutomationPage } from "@/components/automations/automation-page";

export const metadata: Metadata = { title: "Automation" };

/** One automation: the builder, and what it has done. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <AutomationPage id={n} />;
}
