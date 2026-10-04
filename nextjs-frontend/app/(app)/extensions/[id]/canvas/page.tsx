import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ToolGraphEditor } from "@/components/extensions/tool-graph-editor";

export const metadata: Metadata = { title: "Tool" };

/** An assistant tool on the canvas. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <ToolGraphEditor id={n} />;
}
