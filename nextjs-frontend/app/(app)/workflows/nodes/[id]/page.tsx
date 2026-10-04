import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CustomNodeEditor } from "@/components/workflows/custom-node-editor";

export const metadata: Metadata = { title: "Custom node" };

/** One custom node on the canvas. */
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const n = Number(id);
  if (!Number.isInteger(n) || n < 1) notFound();
  return <CustomNodeEditor id={n} />;
}
